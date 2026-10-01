# Восстановление источника, откат времени и повтор только доставки результата.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-184110
#
# Тесты:
# -> test_source_restart_keeps_original_session(): Сохранение выбранной сессии после перезапуска источника.
# -> test_command_http_internal_context(): Изоляция внутренних сетевых записей от уведомлений.
# -> test_source_clock_rollback_after_prune(): Необратимая граница очистки при откате UTC.
# -> test_source_reply_loss_only_retries_result(): Повтор ответа без повторного обработчика.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_command_dispatcher import until
from test_command_hub import APP_TOKEN, SOURCE_TOKEN, Rig
from test_command_sources import FakeProvider, event

from remote_watch.adapters._source_http import SourceHttp
from remote_watch.adapters.command_http import HttpsCommandTransport
from remote_watch.commands.protocol import callback_result
from remote_watch.commands.source import CommandSourceRunner
from remote_watch.commands.source_protocol import SourceTargets
from remote_watch.commands.source_store import SourceJournal
from remote_watch.commands.time import TimeSample
from remote_watch.commands.transport import CommandError
from remote_watch.notifications._context import delivery_context


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение выбранной сессии после перезапуска источника
#------------------------------------------------------------------------------------------------------------------
def test_source_restart_keeps_original_session(tmp_path: Path) -> None:

    """Never retarget a reserved message to an application session created after source recovery.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Изолированный асинхронный сценарий проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Crash the reader before hub submission while a new application session appears."""

        rig = Rig(tmp_path)
        await rig.start()
        provider = FakeProvider()
        provider.events.append(event())
        path = tmp_path / "source.sqlite"
        targets = SourceTargets(aliases={"loader": rig.identity})
        first = CommandSourceRunner(rig.hub, rig.hub.config.sources[0], targets, provider, SourceJournal(path))
        await first.start(activate=False)
        pending = first._prepare(event(), 0)
        await first._worker.call(lambda: first._worker.store.reserve(pending))
        await first.close()
        await rig.client.close()
        rig.now += 61
        await rig.hub.register(APP_TOKEN, replace(rig.registration, session_id="c" * 32))
        second = CommandSourceRunner(rig.hub, rig.hub.config.sources[0], targets, provider, SourceJournal(path))

        try:
            await second.start(activate=False)
            await second.step()
            assert await rig.hub.source_cursor(SOURCE_TOKEN) == 0
            assert rig.store.pending() == ()
            assert provider.acks == ["1"]
        finally:
            await second.close()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Изоляция внутренних сетевых записей от уведомлений
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["source", "client"])
def test_command_http_internal_context(kind: str) -> None:

    """Suppress internal network logging without leaking the guard into application callbacks.

    :param kind: Selected synthetic failure or provider scenario.
    :type kind: str
    """

    # kind — выбранный подставной сценарий провайдера или отказа.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Изолированный асинхронный сценарий проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Inject a transport failure while observing only the diagnostic context."""

        observed = []


        #----------------------------------------------------------------------------------------------------------
        # КЛАСС : Подставная сеть с проверкой контекста внутренней диагностики
        #----------------------------------------------------------------------------------------------------------
        class BrokenNetwork:
            """Fail inside the network operation after checking the shared notification guard."""


            #------------------------------------------------------------------------------------------------------
            # ИНТЕРФЕЙС : Подставной отказ внутри защищённого сетевого контекста
            #------------------------------------------------------------------------------------------------------
            def request(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> Any:

                """Reject a synthetic request under the internal-transport context.

                :param args: Positional arguments of the intercepted submission.
                :type args: Any

                :param kwargs: Named arguments of the intercepted submission.
                :type kwargs: Any

                :return: HTTP response with a bounded body and fixed status semantics.
                :rtype: Any
                """

                # args — позиционные аргументы перехваченной записи.
                # kwargs — именованные аргументы перехваченной записи.

                observed.append(delivery_context.get())
                raise RuntimeError("synthetic private transport failure")
            #------------------------------------------------------------------------------------------------------


            post = request
        #----------------------------------------------------------------------------------------------------------


        assert delivery_context.get() is False

        if kind == "source":
            transport = SourceHttp("https://example.invalid")
            transport._client = BrokenNetwork()
            with pytest.raises(CommandError, match="unavailable"):
                await transport.call("GET", "/")
        else:
            transport = HttpsCommandTransport("https://example.invalid", "x" * 40)
            transport._client = BrokenNetwork()
            transport._loop = asyncio.get_running_loop()
            # Проверка достигает post после кодирования заведомо допустимой модели.
            from remote_watch.commands.protocol import CommandCapability, CommandRegistration
            from remote_watch.events import Identity
            registration = CommandRegistration(identity=Identity(service="test", environment="test", region="ru",
                host="vm", instance_id="one"), session_id="a" * 32,
                capabilities=(CommandCapability(name="status", required_scope="read"),))
            with pytest.raises(CommandError, match="unavailable"):
                await transport.exchange("register", registration)
        assert delivery_context.get() is False
        assert observed == [True]
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Необратимая граница очистки при откате UTC
#------------------------------------------------------------------------------------------------------------------
def test_source_clock_rollback_after_prune(tmp_path: Path) -> None:

    """Keep a durable rejection floor after deleting old event IDs and rolling trusted time back.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Изолированный асинхронный сценарий проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Advance source retention then replay the same event under an older UTC sample."""

        rig = Rig(tmp_path)
        await rig.start()
        provider = FakeProvider()
        runner = CommandSourceRunner(rig.hub, rig.hub.config.sources[0],
            SourceTargets(aliases={"loader": rig.identity}), provider, SourceJournal(tmp_path / "source.sqlite"))

        try:
            await runner.start(activate=False)
            # Очистка уже пройденного времени остаётся в журнале даже без сохранённых ID.
            rig.trusted.install(TimeSample(lower_utc=1200, upper_utc=1200.1, observed_at=rig.now))
            await runner.step()
            rig.trusted.install(TimeSample(lower_utc=1000, upper_utc=1000.1, observed_at=rig.now))
            provider.events.append(event())
            await runner.step()
            assert rig.store.pending() == ()
            assert await rig.client.acquire() is None
            assert provider.acks == ["1"]
        finally:
            await runner.close()
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Повтор ответа без повторного обработчика
#------------------------------------------------------------------------------------------------------------------
def test_source_reply_loss_only_retries_result(tmp_path: Path) -> None:

    """Retry a retained reply after provider response loss without repeating the application callback.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Изолированный асинхронный сценарий проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Execute once then lose the provider's first acknowledgement of the result."""

        rig = Rig(tmp_path)
        await rig.start()
        provider = FakeProvider()
        runner = CommandSourceRunner(rig.hub, rig.hub.config.sources[0],
            SourceTargets(aliases={"loader": rig.identity}), provider, SourceJournal(tmp_path / "source.sqlite"),
            retry_interval=0.01)

        try:
            await runner.start(activate=False)
            provider.events.append(event())
            await runner.step()
            ticket = await rig.client.acquire()
            assert rig.client.begin(ticket)
            calls = [ticket.grant.request.name]
            result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "done")
            await rig.client.complete(ticket, result)
            original = provider.reply

            #------------------------------------------------------------------------------------------------------
            # ФУНКЦИЯ : Потеря ответа после принятого провайдером результата
            #------------------------------------------------------------------------------------------------------
            async def lost_reply(
                conversation_id: str,
                text: str,
            ) -> None:

                """Record actual delivery then simulate losing only its first response.

                :param conversation_id: Authenticated chat ID or configured private command topic.
                :type conversation_id: str

                :param text: Bounded plain text of a command or a provider reply.
                :type text: str
                """

                # conversation_id — проверенный ID чата либо закрытый топик команд.
                # text — ограниченный обычный текст команды либо ответа.

                await original(conversation_id, text)

                if len(provider.replies) == 1:
                    raise CommandError("unavailable")
            #------------------------------------------------------------------------------------------------------

            provider.reply = lost_reply
            runner.activate()
            await until(lambda: runner.stats.replies == 1)
            assert len(provider.replies) == 2
            assert provider.replies[0] == provider.replies[1]
            assert calls == ["resume_load"]
            await runner.close()
            assert await rig.client.acquire() is None
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
    print("Модуль test_command_source_recovery не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
