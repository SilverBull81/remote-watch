# Проверки восстановления этапов команд без стирания истории и потери результата.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-210047
#
# Тесты:
# -> wait_until(): Конечное ожидание наблюдаемого состояния без фиксированной задержки.
# -> test_health_stages(): Независимые отказы, восстановление, stale_session и shutdown.
# -> test_result_health(): Потеря результата после callback не скрывается успешным poll.
# -> test_dispatcher_readiness(): Временный busy и восстановление в фоновом исполнителе.
# -> test_storage_timeout_health(): Timeout журнала не означает освобождение его рабочего потока.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import gc
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock
from weakref import ref

import pytest
from test_command_dispatcher import configured
from test_command_hub import Rig

from remote_watch import CommandRegistry
from remote_watch.commands.protocol import callback_result
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Конечное ожидание наблюдаемого состояния без фиксированной задержки
#------------------------------------------------------------------------------------------------------------------
async def wait_until(predicate: Callable[[], bool]) -> None:

    """Wait for an observable condition within a finite test deadline.

    :param predicate: Side-effect-free test condition.
    :type predicate: Callable[[], bool]
    """

    # predicate — наблюдаемое состояние; timeout ограничивает зависание при регрессии.
    deadline = asyncio.get_running_loop().time() + 3
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимые отказы, восстановление, stale_session и shutdown
#------------------------------------------------------------------------------------------------------------------
def test_health_stages(tmp_path: Path) -> None:

    """Keep stage failures independent and count repeated failures even when an exception object is reused.

    :param tmp_path: Isolated real SQLite journals.
    :type tmp_path: Path
    """

    # tmp_path — отдельные журналы; реального gateway и команд провайдера нет.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Последовательные отказы и независимое восстановление этапов
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise poll, heartbeat and storage without relying on wall-clock timing."""

        rig = Rig(tmp_path)
        assert not rig.client.health.ready
        await rig.hub.start()
        startup = asyncio.create_task(rig.client.start())
        await startup
        startup_ref = ref(startup)
        del startup
        await asyncio.sleep(0)
        gc.collect()
        # Context фонового heartbeat не удерживает завершённую родительскую задачу.
        assert startup_ref() is None
        original = rig.transport.exchange
        try:
            assert rig.client.health.ready
            failure = CommandError("busy", http_status=429)
            rig.transport.exchange = AsyncMock(side_effect=failure)
            for _ in range(3):
                with pytest.raises(CommandError, match="busy"):
                    await rig.client.acquire()
            state = rig.client.health
            assert not state.ready and state.poll.transient_failures == 3
            assert state.poll.failures == 3 and state.poll.http_status == 429
            assert state.result.failures == 0 and state.storage.failures == 0

            with pytest.raises(CommandError, match="busy"):
                await rig.client._heartbeat_once()
            rig.transport.exchange = original
            assert await rig.client.acquire() is None
            state = rig.client.health
            assert state.poll.current_error is None and state.poll.recoveries == 1
            assert not state.ready and state.heartbeat.current_error == "busy"
            assert state.last_error == "busy" and state.last_error_stage == "heartbeat"
            await rig.client._heartbeat_once()
            assert rig.client.health.ready and rig.client.health.heartbeat.recoveries == 1
            # Старый снимок остаётся неизменным после следующих наблюдений.
            assert state.heartbeat.current_error == "busy"

            # Отказ worker не должен дополнительно считаться отказом poll или result.
            worker = rig.client._worker.call
            rig.client._worker.call = AsyncMock(side_effect=CommandError("busy"))
            with pytest.raises(CommandError, match="busy"):
                await rig.client.acquire()
            state = rig.client.health
            assert state.storage.failures == 1 and state.poll.failures == 3 and state.result.failures == 0
            rig.client._worker.call = worker
            await rig.client.acquire()
            assert rig.client.health.ready and rig.client.health.storage.recoveries == 1

            rig.transport.exchange = AsyncMock(side_effect=CommandError("stale_session", http_status=409))
            with pytest.raises(CommandError, match="stale_session"):
                await rig.client._heartbeat_once()
            rig.transport.exchange = original
            assert not rig.client.health.session_valid
            with pytest.raises(CommandError, match="stale_session"):
                await rig.client.acquire()
        finally:
            await rig.close()
        assert rig.client.health.closed and not rig.client.health.ready
        assert rig.client.health.heartbeat.current_error == "stale_session"
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Потеря результата после callback не скрывается успешным poll
#------------------------------------------------------------------------------------------------------------------
def test_result_health(tmp_path: Path) -> None:

    """Keep a lost durable receipt visible until retry acknowledges the same result.

    :param tmp_path: Isolated real SQLite journals.
    :type tmp_path: Path
    """

    # tmp_path — отдельные журналы позволяют отличить commit от доставки квитанции.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Один callback и повтор только результата
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Lose a committed result response and recover without another callback invocation."""

        rig = Rig(tmp_path)
        await rig.start()
        try:
            await rig.submit(rig.request())
            ticket = await rig.client.acquire()
            assert rig.client.begin(ticket)
            result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "done")
            rig.transport.lose = "result"
            with pytest.raises(CommandError, match="unavailable"):
                await rig.client.complete(ticket, result)
            state = rig.client.health
            assert not state.ready and state.has_pending_results
            assert state.result.failures == 1 and state.result.transient_failures == 1
            await rig.client._heartbeat_once()
            assert not rig.client.health.ready and rig.client.health.result.current_error == "unavailable"
            await rig.client.flush_results()
            state = rig.client.health
            assert state.ready and not state.has_pending_results and state.result.recoveries == 1
            assert state.last_error == "unavailable" and state.last_error_stage == "result"
            assert rig.local.get(result.ref).acknowledged
            original = rig.transport.exchange
            rig.transport.exchange = AsyncMock(side_effect=CommandError("unavailable"))
            with pytest.raises(CommandError):
                await rig.client._heartbeat_once()
            rig.transport.exchange = original
            await rig.client.acquire()
            assert not rig.client.health.ready
            assert rig.client.health.last_error_stage == "heartbeat"
            assert rig.client.health.result.current_error is None
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Временный busy и восстановление в фоновом исполнителе
#------------------------------------------------------------------------------------------------------------------
def test_dispatcher_readiness(tmp_path: Path) -> None:

    """Expose recovery through the public dispatcher stats while preserving the last incident.

    :param tmp_path: Isolated real SQLite journals.
    :type tmp_path: Path
    """

    # tmp_path — журналы исполнителя; busy снимается через реальный цикл dispatcher.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Наблюдение фонового цикла из второго потока
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Recover the real poll loop and read immutable stats from another thread."""

        rig, dispatcher = configured(tmp_path, CommandRegistry.from_callbacks({"status": lambda: "ready"}))
        await rig.hub.start()
        original = rig.transport.exchange
        await dispatcher.start()
        try:
            rig.transport.exchange = AsyncMock(side_effect=CommandError("busy"))
            await wait_until(lambda: dispatcher.stats.health.poll.transient_failures >= 3)
            assert not dispatcher.stats.ready
            rig.transport.exchange = original
            await wait_until(lambda: dispatcher.stats.ready)
            state = await asyncio.to_thread(lambda: dispatcher.stats)
            assert state.last_error == "busy" and state.last_error_stage == "poll"
            assert state.health.poll.recoveries == 1
        finally:
            await dispatcher.close()
            await rig.hub.close()
        assert dispatcher.stats.closed and not dispatcher.stats.ready
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Timeout журнала не означает освобождение его рабочего потока
#------------------------------------------------------------------------------------------------------------------
def test_storage_timeout_health(tmp_path: Path) -> None:

    """Recover storage health only after a real worker finishes and a later operation succeeds.

    :param tmp_path: Isolated real SQLite journals.
    :type tmp_path: Path
    """

    # tmp_path — отдельные журналы; Event удерживает рабочий поток после timeout caller.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка занятости потока после прекращения ожидания
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Hold storage beyond its wait budget, release it, then observe actual recovery."""

        rig = Rig(tmp_path)
        release = threading.Event()
        await rig.start()
        try:
            rig.client._worker.timeout = 0.02
            with pytest.raises(CommandError, match="unavailable"):
                await rig.client._storage(lambda: release.wait(3))
            assert not rig.client._worker._pending.done()
            assert not rig.client.health.ready
            assert rig.client.health.storage.current_error == "unavailable"
            with pytest.raises(CommandError, match="busy"):
                await rig.client.pending()
            assert rig.client.health.storage.failures == 2
            assert rig.client.health.storage.recoveries == 0
            release.set()
            await wait_until(lambda: rig.client._worker._pending.done())
            rig.client._worker.timeout = 3
            await rig.client.pending()
            assert rig.client.health.ready and rig.client.health.storage.recoveries == 1
        finally:
            release.set()
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_client_health не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
