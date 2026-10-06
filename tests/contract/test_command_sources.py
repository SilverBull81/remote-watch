# Проверки разбора команд, постоянной позиции и восстановления источника.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-102445
#
# Классы:
# -> FakeProvider: Подставной провайдер с наблюдаемыми подтверждениями.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие ресурсов и проверка владения.
#    -> poll(): Получение ограниченной порции неподтверждённых событий.
#    -> acknowledge(): Подтверждение только записанного события.
#    -> reply(): Одна попытка отправить ответ проверенному получателю.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.
#
# Тесты:
# -> event(): Подставное событие с известным временем.
# -> test_source_parser(): Разбор явного адреса и строковых аргументов.
# -> test_source_parser_rejects(): Отказ от неоднозначной или чрезмерной команды.
# -> test_source_journal_recovery(): Восстановление решения и необратимая граница очистки.
# -> test_source_journal_capacity_and_lock(): Предел защиты от повторов и исключительное владение.
# -> test_source_admission(): Права, свежесть и потери подтверждений источника.
# -> test_short_commands(): Короткая справка, однозначность и прежние границы доступа.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from test_command_hub import APP_TOKEN, SOURCE_TOKEN, Rig

from remote_watch.adapters.telegram_commands import TelegramCommandConfig, TelegramCommandProvider
from remote_watch.commands.client import CommandClient
from remote_watch.commands.protocol import CommandCapability
from remote_watch.commands.source import CommandSourceRunner
from remote_watch.commands.source_protocol import SourceEvent, SourcePending, SourceTargets, parse_source_command
from remote_watch.commands.source_store import ProcessLock, SourceJournal
from remote_watch.commands.storage import StoreConflict, StoreFull
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Подставной провайдер с наблюдаемыми подтверждениями
#------------------------------------------------------------------------------------------------------------------
class FakeProvider:
    """Retain synthetic events until a durable source acknowledges them."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Create independently observable event and reply queues."""

        self.events: list[SourceEvent] = []
        self.replies: list[tuple[str, str]] = []
        self.acks: list[str] = []
        self.lose_ack = False
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов и проверка владения
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> str:

        """Return a stable synthetic provider identity.

        :return: Stable nonsecret fingerprint of the opened provider stream.
        :rtype: str
        """

        return "fake-owner"
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Получение ограниченной порции неподтверждённых событий
    #--------------------------------------------------------------------------------------------------------------
    async def poll(
        self,
        cursor: str,
    ) -> tuple[SourceEvent, ...]:

        """Redeliver an unacknowledged event independently of its opaque ID.

        :param cursor: Opaque identifier of the last durably processed provider event.
        :type cursor: str

        :return: Bounded batch of authenticated provider events.
        :rtype: tuple[SourceEvent, ...]
        """

        # cursor — непрозрачный ID последнего сохранённого события.

        return tuple(self.events[:1])
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение только записанного события
    #--------------------------------------------------------------------------------------------------------------
    async def acknowledge(
        self,
        cursor: str,
    ) -> None:

        """Optionally lose the acknowledgement without removing the provider event.

        :param cursor: Opaque identifier of the last durably processed provider event.
        :type cursor: str
        """

        # cursor — непрозрачный ID последнего сохранённого события.

        if self.lose_ack:
            self.lose_ack = False
            raise CommandError("unavailable")
        self.acks.append(cursor)
        self.events[:] = [event for event in self.events if event.event_id != cursor]
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправить ответ проверенному получателю
    #--------------------------------------------------------------------------------------------------------------
    async def reply(
        self,
        conversation_id: str,
        text: str,
    ) -> None:

        """Record plain output for an assertion without accessing a real provider.

        :param conversation_id: Authenticated chat ID or configured private command topic.
        :type conversation_id: str

        :param text: Bounded plain text of a command or a provider reply.
        :type text: str
        """

        # conversation_id — проверенный ID чата либо закрытый топик команд.
        # text — ограниченный обычный текст команды либо ответа.

        self.replies.append((conversation_id, text))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Complete the fake provider lifecycle without external resources."""
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подставное событие с известным временем
#------------------------------------------------------------------------------------------------------------------
def event(
    number: int = 1,
    text: str = '/rw loader resume_load',
) -> SourceEvent:

    """Build one authenticated synthetic event at deterministic provider time.

    :param number: Synthetic command and provider event number.
    :type number: int

    :param text: Bounded plain text of a command or a provider reply.
    :type text: str

    :return: Bounded provider event with verified routing fields.
    :rtype: SourceEvent
    """

    # number — номер подставной команды и события провайдера.
    # text — ограниченный обычный текст команды либо ответа.

    return SourceEvent(
        event_id=str(number), message_date=1000, actor_id="owner", conversation_id="chat", text=text)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Разбор явного адреса и строковых аргументов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "/rw", "/rw loader", "/rw loader resume_load", '/rw loader resume_load a="two words"',
])
def test_source_parser(text: str) -> None:

    """Parse names and quoted values without interpreting Python or shell syntax.

    :param text: Bounded plain text of a command or a provider reply.
    :type text: str
    """

    # text — ограниченный обычный текст команды либо ответа.

    alias, name, arguments = parse_source_command(text)
    assert alias is None or alias == "loader"
    assert name is None or name == "resume_load"
    assert arguments in ({}, {"a": "two words"})
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ от неоднозначной или чрезмерной команды
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["resume_load", "/rw * resume_load", "/rw loader BAD", "/rw a b x=1 x=2",
                                 "/rw a b positional", '/rw a b x="', "/rw " + "я" * 5000])
def test_source_parser_rejects(text: str) -> None:

    """Reject ambiguous routing, duplicate arguments and excessive command text.

    :param text: Bounded plain text of a command or a provider reply.
    :type text: str
    """

    # text — ограниченный обычный текст команды либо ответа.

    with pytest.raises(ValueError):
        parse_source_command(text)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Восстановление решения и необратимая граница очистки
#------------------------------------------------------------------------------------------------------------------
def test_source_journal_recovery(tmp_path: Path) -> None:

    """Preserve one selected decision across reopen and refuse conflicting ownership.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.

    path = tmp_path / "source.sqlite"
    journal = SourceJournal(path)
    journal.open()
    journal.bind("owner")
    pending = SourcePending(position=0, event=event(), request=None)
    journal.reserve(pending)
    journal.close()
    recovered = SourceJournal(path)
    recovered.open()

    try:
        recovered.bind("owner")
        assert recovered.state() == (-1, "", pending)
        recovered.commit(pending)
        recovered.commit(pending)
        assert recovered.contains("1")
        assert recovered.state() == (0, "1", None)
        with pytest.raises(StoreConflict):
            recovered.bind("another-owner")
        recovered.prune(None)
        assert recovered.contains("1")
        recovered.prune(1001)
        assert not recovered.contains("1")
        recovered.prune(500)
        assert recovered.retired(1000)
    finally:
        recovered.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Предел защиты от повторов и исключительное владение
#------------------------------------------------------------------------------------------------------------------
def test_source_journal_capacity_and_lock(tmp_path: Path) -> None:

    """Backpressure preserves fresh replay evidence and exclusive reader ownership.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.

    journal = SourceJournal(tmp_path / "source.sqlite", seen_limit=32)
    journal.open()

    try:
        rival = SourceJournal(journal.path)
        with pytest.raises(StoreConflict):
            rival.open()
        for number in range(32):
            pending = SourcePending(position=number, event=event(number), request=None)
            journal.reserve(pending)
            journal.commit(pending)
        with pytest.raises(StoreFull):
            journal.reserve(SourcePending(position=32, event=event(32), request=None))
        assert journal.state()[:2] == (31, "31")
    finally:
        journal.close()
    lock = ProcessLock(tmp_path / "owner.lock")
    lock.open()
    lock.close()
    second = ProcessLock(lock.path)
    second.open()
    second.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Права, свежесть и потери подтверждений источника
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["normal", "lost_ack", "lost_hub_response", "foreign", "old", "help", "long"])
def test_source_admission(
    tmp_path: Path,
    kind: str,
) -> None:

    """Bind one session durably and reject foreign or stale source messages without callback retries.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param kind: Selected synthetic failure or provider scenario.
    :type kind: str
    """

    # tmp_path — отдельный временный каталог теста.
    # kind — выбранный подставной сценарий провайдера или отказа.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Изолированный асинхронный сценарий проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise provider checkpoints against the actual hub and client journals."""

        rig = Rig(tmp_path)
        await rig.start()
        provider = FakeProvider()
        incoming = event()

        if kind == "foreign":
            incoming = replace(incoming, actor_id="intruder")
        elif kind == "old":
            incoming = replace(incoming, message_date=500)
        elif kind == "help":
            incoming = replace(incoming, text="/rw loader")
        elif kind == "long":
            incoming = replace(incoming, text="/rw loader resume_load x=" + "я" * 5000)
        provider.events.append(incoming)
        journal = SourceJournal(tmp_path / "source.sqlite")
        runner = CommandSourceRunner(rig.hub, rig.hub.config.sources[0],
            SourceTargets(aliases={"loader": rig.identity}), provider, journal, retry_interval=0.01)

        try:
            await runner.start(activate=False)
            if kind == "lost_ack":
                provider.lose_ack = True
                with pytest.raises(CommandError, match="unavailable"):
                    await runner.step()
            if kind == "lost_hub_response":
                submit = rig.hub.submit

                #--------------------------------------------------------------------------------------------------
                # ФУНКЦИЯ : Потеря ответа после постоянной записи hub
                #--------------------------------------------------------------------------------------------------
                async def lost(
                    *args: object,
                    **kwargs: object,
                ) -> object:

                    """Lose one response after the hub commits its durable decision.

                    :param args: Positional arguments of the intercepted submission.
                    :type args: object

                    :param kwargs: Named arguments of the intercepted submission.
                    :type kwargs: object

                    :return: Validated provider result or intercepted synthetic response.
                    :rtype: object
                    """

                    # args — позиционные аргументы перехваченной записи.
                    # kwargs — именованные аргументы перехваченной записи.

                    await submit(*args, **kwargs)
                    raise CommandError("unavailable")
                #--------------------------------------------------------------------------------------------------

                rig.hub.submit = lost
                with pytest.raises(CommandError, match="unavailable"):
                    await runner.step()
                rig.hub.submit = submit
                pending = journal.state()[2]
                assert pending is not None and pending.request is not None
            await runner.step()
            assert await rig.hub.source_cursor(SOURCE_TOKEN) == 0
            assert provider.acks == ["1"]
            ticket = await rig.client.acquire()
            if kind in ("normal", "lost_ack", "lost_hub_response"):
                assert ticket is not None
                assert ticket.grant.request.ref.session_id == rig.registration.session_id
                with pytest.raises(CommandError, match="busy"):
                    await rig.client.acquire()
            else:
                assert ticket is None
            if kind == "foreign":
                assert not provider.replies
            elif kind == "help":
                assert "resume_load" in provider.replies[0][1]
        finally:
            await runner.close()
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Короткая справка, однозначность и прежние границы доступа
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["Help", "hElP", "/help", "/status", "/resume_load", "restricted",
                                  "foreign_actor", "foreign_chat", "multi_offline", "explicit",
                                  "offline", "foreign_bot", "legacy", "ntfy", "lost_ack", "long_help",
                                  "restricted_command", "second_live", "new_session", "telegram_wire",
                                  "explicit_help", "long_explicit_help", "ntfy_help", "help_collision"])
def test_short_commands(
    tmp_path: Path,
    case: str,
) -> None:

    """Resolve implicit targets from static ACL and preserve exact session binding.

    :param tmp_path: Isolated source and command journals.
    :type tmp_path: Path

    :param case: Selected syntax, permission or ambiguity scenario.
    :type case: str
    """

    # tmp_path — отдельные журналы; case — сценарий без настоящего Telegram/ntfy.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Обработка события через постоянное решение источника
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Run one source event without interpreting availability as implicit addressing."""

        rig = Rig(tmp_path)
        config = rig.hub.config
        access = config.sources[0].access[0]
        second = replace(rig.identity, instance_id="two")
        scopes = frozenset({"read", "control"})
        access = replace(access, scopes=frozenset({"read"}) if case.startswith("restricted") else scopes)
        if case == "telegram_wire":
            access = replace(access, actor_id="42", conversation_id="-123")
        rules = ((access, replace(access, identity=second))
                 if case in {"multi_offline", "explicit", "second_live"} else (access,))
        rig.hub.config = replace(config,
            principals=(replace(config.principals[0], scopes=scopes, identities=(rig.identity, second)),),
            sources=(replace(config.sources[0], access=rules),))
        rig.registration = replace(rig.registration, capabilities=(
            CommandCapability(name="status", required_scope="read"),
            CommandCapability(name="resume_load", required_scope="control")))
        if case in {"long_help", "long_explicit_help"}:
            rig.registration = replace(rig.registration, capabilities=tuple(
                CommandCapability(name=f"a{number:02d}" + "x" * 61, required_scope="read")
                for number in range(64)))
        if case == "help_collision":
            rig.registration = replace(rig.registration, capabilities=(
                *rig.registration.capabilities, CommandCapability(name="help", required_scope="read")))
        rig.client = CommandClient(rig.registration, rig.transport, rig.local, clock=lambda: rig.now)
        await rig.start()
        if case == "second_live":
            await rig.hub.register(APP_TOKEN, replace(rig.registration, identity=second, session_id="f" * 32))
        provider = FakeProvider()
        text = case if case in {"Help", "hElP", "/help", "/status", "/resume_load"} else "/status"
        if case in {"restricted", "long_help", "help_collision", "ntfy_help"}:
            text = "/help"
        elif case in {"explicit_help", "long_explicit_help"}:
            text = "/rw loader"
        elif case == "restricted_command":
            text = "/resume_load"
        elif case == "explicit":
            text = "/rw loader status"
        elif case == "legacy":
            text = "/rw"
        elif case == "foreign_bot":
            text = "/status@another_bot"
        incoming = event(text=text)
        if case == "telegram_wire":
            telegram = TelegramCommandProvider(TelegramCommandConfig(token_env="SYNTHETIC_TEST"))
            telegram._username = "test_bot"
            incoming = telegram._event({"update_id": 1, "message": {"date": 1000,
                "from": {"id": 42, "is_bot": False}, "chat": {"id": -123, "type": "group"},
                "text": "/status@TEST_BOT"}})
        if case == "foreign_actor":
            incoming = replace(incoming, actor_id="stranger")
        if case == "foreign_chat":
            incoming = replace(incoming, conversation_id="another-chat")
        if case == "offline":
            rig.now += config.session_ttl + 1
        provider.events.append(incoming)
        aliases = {"loader": rig.identity}
        if len(rules) == 2:
            aliases["second"] = second
        journal = SourceJournal(tmp_path / "short.sqlite")
        runner = CommandSourceRunner(rig.hub, rig.hub.config.sources[0], SourceTargets(aliases=aliases),
                                     provider, journal, short_commands=case not in {"ntfy", "ntfy_help"})
        try:
            await runner.start(activate=False)
            if case == "new_session":
                submit = rig.hub.submit
                rig.hub.submit = AsyncMock(side_effect=CommandError("unavailable"))
                with pytest.raises(CommandError):
                    await runner.step()
                pending = journal.state()[2]
                assert pending.request.ref.session_id == rig.registration.session_id
                rig.hub.submit = submit
                rig.now += config.session_ttl + 1
                await rig.hub.register(APP_TOKEN, replace(rig.registration, session_id="f" * 32))
                runner._prepare = Mock(side_effect=AssertionError("must not select another session"))
            if case == "lost_ack":
                provider.lose_ack = True
                with pytest.raises(CommandError):
                    await runner.step()
                # Повтор события не создаёт новую команду даже при повторной доставке.
                provider.lose_ack = False
            await runner.step()
            rows = rig.store.pending()
            accepted = case in {"/status", "/resume_load", "explicit", "lost_ack", "telegram_wire"}
            assert bool(rows) is accepted
            if accepted:
                assert len(rows) == 1
                assert rows[0].record.request.ref.session_id == rig.registration.session_id
                assert rows[0].record.request.name == ("resume_load" if case == "/resume_load" else "status")
            elif case in {"Help", "hElP", "/help", "restricted", "explicit_help", "ntfy_help", "help_collision"}:
                expected = "Allowed Bot Commands:\n-> /help\n-> /status"
                if case != "restricted":
                    expected += "\n-> /resume_load"
                if case == "explicit_help":
                    expected += "\nUsage: /rw loader command [arg_name=value]"
                assert provider.replies[0][1] == expected
            elif case.startswith("foreign_") and case != "foreign_bot":
                assert not provider.replies
            elif case in {"multi_offline", "second_live"}:
                assert "loader" in provider.replies[0][1] and "second" in provider.replies[0][1]
            elif case in {"long_help", "long_explicit_help"}:
                assert len(provider.replies) == 2
                lines = []
                for _, page in provider.replies:
                    assert len(page.encode("utf-8")) <= 3800
                    assert page.splitlines()[0] == "Allowed Bot Commands:"
                    lines.extend(page.splitlines()[1:])
                expected = ["-> /help"] + ["-> /" + cap.name for cap in rig.registration.capabilities]
                if case == "long_explicit_help":
                    expected += ["Usage: /rw loader command [arg_name=value]"]
                assert lines == expected
            elif case == "legacy":
                assert provider.replies[0][1] == "Configured applications:\n-> loader\nHelp: /rw app_name or /help"
            elif case == "offline":
                assert provider.replies[0][1] == "Application unavailable."
        finally:
            await runner.close()
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_sources не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
