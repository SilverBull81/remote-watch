# Сохранение режима команд между решениями источника, SQLite и доставкой ответа.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-102445
#
# Тесты:
# -> test_command_mode_recovery(): Восстановление решения и ответ по режиму исходной сессии.
# -> test_command_mode_configuration(): Раздельные настройки клиента/источника и безопасные ошибки.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from test_command_dispatcher import until
from test_command_gateway_config import settings, write_config
from test_command_hub import APP_TOKEN, SOURCE_TOKEN, Rig
from test_command_sources import FakeProvider, event

from remote_watch import CommandRegistry
from remote_watch.commands.client import CommandClient
from remote_watch.commands.protocol import callback_result, message_digest
from remote_watch.commands.source import CommandSourceRunner
from remote_watch.commands.source_protocol import SourceTargets, source_result_text
from remote_watch.commands.source_store import SourceJournal
from remote_watch.commands.transport import CommandError
from remote_watch.gateway.command_config import CommandConfigError, load_command_client, load_command_gateway


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Восстановление решения и ответ по режиму исходной сессии
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["full", "compact", "text"])
def test_command_mode_recovery(
    tmp_path: Path,
    mode: str,
) -> None:

    """Retain the client mode across source reopening, lost replies and a newer registration.

    :param tmp_path: Separate source, hub and application SQLite files.
    :type tmp_path: Path

    :param mode: Original application's requested command presentation.
    :type mode: str
    """

    # mode сохраняется в постоянном request до submit, а не читается при каждом retry.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Постоянное решение источника и единственный результат приложения
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise real journals without real provider credentials or external network."""

        rig = Rig(tmp_path)
        rig.registration = replace(rig.registration, command_display_mode=mode)
        rig.client = CommandClient(rig.registration, rig.transport, rig.local, clock=lambda: rig.now)
        await rig.start()
        provider = FakeProvider()
        provider.events.append(event(text="/rw loader resume_load"))
        targets = SourceTargets(aliases={"loader": rig.identity})
        journal_path = tmp_path / "presentation.sqlite"
        runner = CommandSourceRunner(rig.hub, rig.hub.config.sources[0], targets, provider,
                                     SourceJournal(journal_path), retry_interval=0.01)
        await runner.start(activate=False)
        submit = rig.hub.submit
        rig.hub.submit = AsyncMock(side_effect=CommandError("unavailable"))
        task = None
        try:
            with pytest.raises(CommandError):
                await runner.step()
            pending = runner._worker.store.state()[2]
            assert pending.request.command_display_mode == mode
            original = pending.request
            await runner.close()
            rig.hub.submit = submit
            runner = CommandSourceRunner(rig.hub, rig.hub.config.sources[0], targets, provider,
                SourceJournal(journal_path), retry_interval=0.01, command_display_mode="text")
            runner._prepare = Mock(side_effect=AssertionError("must recover the original decision"))
            await runner.start(activate=False)
            await runner.step()
            assert rig.store.pending()[0].record.request == original
            ticket = await rig.client.acquire()
            assert ticket is not None and rig.client.begin(ticket)
            assert ticket.grant.request.command_display_mode == mode
            result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "Application response")
            await rig.client.complete(ticket, result)
            expected = source_result_text(original, result)
            digest = message_digest(result)

            # Сначала провайдер недоступен; запись результата и режим остаются в SQLite.
            real_reply = provider.reply
            entered, release = asyncio.Event(), asyncio.Event()

            #------------------------------------------------------------------------------------------------------
            # ФУНКЦИЯ : Управляемая потеря ответа без гонки с опросом SQLite
            #------------------------------------------------------------------------------------------------------
            async def lost_reply(
                conversation_id: str,
                text: str,
            ) -> None:

                """Hold one provider attempt while the test inspects durable state.

                :param conversation_id: Synthetic authorized chat.
                :type conversation_id: str

                :param text: Original rendered response.
                :type text: str
                """

                # Пока провайдер ждёт, responder не начинает следующую операцию журнала.
                assert conversation_id == "chat" and text == expected
                entered.set()
                await release.wait()
                raise CommandError("unavailable")
            #------------------------------------------------------------------------------------------------------

            provider.reply = lost_reply
            task = asyncio.create_task(runner._respond())
            await asyncio.wait_for(entered.wait(), 3)
            assert len(await rig.hub.results(SOURCE_TOKEN)) == 1
            rig.now += rig.hub.config.session_ttl + 1
            newer = replace(rig.registration, session_id="f" * 32,
                            command_display_mode="full" if mode != "full" else "compact")
            await rig.hub.register(APP_TOKEN, newer)
            provider.reply = real_reply
            release.set()
            await until(lambda: runner.stats.replies == 1)
            assert provider.replies == [("chat", expected)]
            stored = rig.store.get(original.ref)
            assert stored.record.request.command_display_mode == mode
            assert message_digest(stored.record.result) == digest
            assert rig.client._active is None

        finally:
            rig.hub.submit = submit
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runner.close()
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Раздельные настройки клиента/источника и безопасные ошибки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", [None, "full", "compact", "text", "PRIVATE", False])
def test_command_mode_configuration(
    tmp_path: Path,
    mode: object,
) -> None:

    """Read independent client/source defaults while rejecting invalid values without disclosure.

    :param tmp_path: Directory for synthetic configuration files only.
    :type tmp_path: Path

    :param mode: Optional valid mode or deliberately invalid configuration value.
    :type mode: object
    """

    # Служебные ошибки остаются русскими в CLI; содержимое неверного поля не выводится.
    raw = settings()
    client_raw = {"schema_version": 1, "identity": raw["targets"]["one"],
                  "endpoint": "https://gateway.invalid", "state_file": "client.sqlite", "owner_id": "client",
                  "token": "x" * 40, "command_display_mode": mode}
    path = write_config(tmp_path / "client.json", client_raw)
    registry = CommandRegistry.from_callbacks({"status": lambda: "ok"})
    if mode in (None, "full", "compact", "text"):
        client = load_command_client(path, registry)
        assert client.registration.command_display_mode == mode
    else:
        with pytest.raises(CommandConfigError) as caught:
            load_command_client(path, registry)
        assert caught.value.field == "client.command_display_mode" and "PRIVATE" not in str(caught.value)

    raw["sources"][0]["command_display_mode"] = mode
    path = write_config(tmp_path / "gateway.json", raw)
    if mode in ("full", "compact", "text"):
        assert load_command_gateway(path).providers[0].command_display_mode == mode
    else:
        with pytest.raises(CommandConfigError) as caught:
            load_command_gateway(path)
        assert caught.value.field == "sources[0].command_display_mode" and "PRIVATE" not in str(caught.value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_presentation не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
