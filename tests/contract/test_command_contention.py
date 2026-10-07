# Проверки пересечений фонового обслуживания, HTTP-опроса и обновления времени.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261007-235742
#
# Тесты:
# -> test_worker_waiting(): Очередь, отмена, timeout и остановка без скрытого исполнения.
# -> test_poll_sweep_overlap(): Настоящий HTTP poll во время фонового sweep без отказов.
# -> test_refresh_readiness(): Действующее показание при refresh и реальные причины отказа.
# -> test_busy_details(): Проверка закрытого набора причин в HTTP и health.
# -> test_queued_claims(): Истечение и повтор grant во время очереди без второго разрешения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_command_hub import APP_TOKEN, Rig

from remote_watch.adapters.command_http import HttpsCommandTransport
from remote_watch.commands._worker import StoreWorker
from remote_watch.commands.health import HealthState
from remote_watch.commands.http_wire import decode_error
from remote_watch.commands.protocol import CommandClaim, CommandGrant, CommandResult, message_digest
from remote_watch.commands.time import TimeSample, TimeUnavailable
from remote_watch.commands.transport import CommandError
from remote_watch.gateway.command_server import CommandHubServer


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Очередь, отмена, timeout и остановка без скрытого исполнения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["normal", "cancel_waiter", "wait_timeout", "close",
                                  "cancel_active", "active_timeout"])
def test_worker_waiting(
    tmp_path: Path,
    mode: str,
) -> None:

    """Keep executor work serial and never execute a discarded waiting request.

    :param tmp_path: Temporary SQLite directory.
    :type tmp_path: Path

    :param mode: Selected cancellation, deadline or lifecycle boundary.
    :type mode: str
    """

    # tmp_path/mode — настоящий журнал и управляемая граница ожидания.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Управляемая занятость без зависимости от скорости диска
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Pin a real worker with events and inspect queue admission before releasing it."""

        worker = StoreWorker(Rig(tmp_path).store, 5, max_waiters=1)
        await worker.open()
        entered, release = threading.Event(), threading.Event()
        calls = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Удержание рабочего потока до сигнала теста
        #----------------------------------------------------------------------------------------------------------
        def blocked() -> None:

            """Hold the executor independently of the async caller lifetime."""

            entered.set()
            assert release.wait(10)
            calls.append("active")
        #----------------------------------------------------------------------------------------------------------

        if mode == "active_timeout":
            worker.timeout = 0.05
        active = asyncio.create_task(worker.call(blocked))
        waiting = closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if mode == "cancel_active":
                active.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await active
            elif mode == "active_timeout":
                with pytest.raises(CommandError, match="unavailable"):
                    await active
            worker.timeout = 0.05 if mode == "wait_timeout" else 5
            waiting = asyncio.create_task(worker.call(lambda: calls.append("waiting")))
            # call регистрирует место до первого await; не ждём произвольную паузу.
            await asyncio.sleep(0)
            assert worker._waiters == 1
            with pytest.raises(CommandError) as caught:
                await worker.call(lambda: calls.append("overflow"))
            assert caught.value.busy_reason == "storage_queue_full"
            if mode == "cancel_waiter":
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting
            elif mode == "wait_timeout":
                with pytest.raises(CommandError) as caught:
                    await waiting
                assert caught.value.busy_reason == "storage_wait_timeout"
            elif mode == "close":
                closing = asyncio.create_task(worker.close(5))
                await asyncio.sleep(0)
            assert calls == [] and worker._gate.locked()
            release.set()
            if mode not in {"cancel_active", "active_timeout"}:
                await active
            if mode == "close":
                with pytest.raises(CommandError, match="closed"):
                    await waiting
            elif mode not in {"cancel_waiter", "wait_timeout"}:
                await waiting
            expected = ["active"] if mode in {"cancel_waiter", "wait_timeout", "close"} else ["active", "waiting"]
            assert calls == expected and worker._waiters == 0
            if mode != "close":
                assert await worker.call(lambda: "recovered") == "recovered"
        finally:
            release.set()
            await asyncio.gather(*(task for task in (active, waiting, closing) if task), return_exceptions=True)
            await worker.close(5)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящий HTTP poll во время фонового sweep без отказов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("expired", [False, True])
def test_poll_sweep_overlap(
    tmp_path: Path,
    expired: bool,
) -> None:

    """Wait for actual maintenance contention through HTTP without hiding an expired session.

    :param tmp_path: Temporary hub journal.
    :type tmp_path: Path

    :param expired: Expire the session while its storage request is waiting.
    :type expired: bool
    """

    # tmp_path/expired — локальный HTTP и срок сессии на управляемых часах.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Повторные пересечения poll и sweep с настоящим SQLite
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise idle polls, heartbeat and bounded overload with maintenance still enabled."""

        rig = Rig(tmp_path, refresh_interval=0.1)
        second_identity = replace(rig.identity, instance_id="two")
        principal = replace(rig.hub.config.principals[0], identities=(rig.identity, second_identity))
        rig.hub.config = replace(rig.hub.config, principals=(principal,))
        server = CommandHubServer(rig.hub)
        await server.start(allow_loopback_http=True)
        transports = [HttpsCommandTransport(f"http://127.0.0.1:{server.port}", APP_TOKEN,
                      allow_loopback_http=True) for _ in range(3)]
        for transport in transports:
            await transport.open()
        entered, release = threading.Event(), threading.Event()
        original = rig.store.pending
        armed = [True]

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Задержка одной выборки журнала до разрешения теста
        #----------------------------------------------------------------------------------------------------------
        def pending(*, limit: int = 1000) -> tuple:

            """Pause the next scan, then delegate to real SQLite.

            :param limit: Original bounded page size.
            :type limit: int

            :return: Actual journal rows.
            :rtype: tuple
            """

            # limit — прежний предел; вмешиваемся только в момент завершения.
            if armed[0]:
                armed[0] = False
                entered.set()
                assert release.wait(10)
            return original(limit=limit)
        #----------------------------------------------------------------------------------------------------------

        jobs = []
        try:
            session = await transports[0].exchange("register", rig.registration)
            session_two = await transports[2].exchange("register", replace(rig.registration,
                identity=second_identity, session_id="c" * 32))
            rig.store.pending = pending
            # Первый вход обеспечивает настоящий фоновый sweep, без его отключения.
            assert await asyncio.to_thread(entered.wait, 5)
            poll = asyncio.create_task(transports[0].exchange("poll", session))
            jobs.append(poll)
            #------------------------------------------------------------------------------------------------------
            # ФУНКЦИЯ : Ожидание регистрации ограниченного waiter
            #------------------------------------------------------------------------------------------------------
            async def queued() -> None:

                """Wait until HTTP polling enters the bounded storage queue."""

                while not rig.hub._worker._waiters:
                    await asyncio.sleep(0.001)
            #------------------------------------------------------------------------------------------------------

            await asyncio.wait_for(queued(), 5)
            assert not poll.done()
            assert await transports[1].exchange("heartbeat", session) is not None
            with pytest.raises(CommandError) as caught:
                await transports[2].exchange("poll", session)
            assert caught.value.http_status == 429 and caught.value.busy_reason == "session_poll_active"
            if expired:
                rig.now += rig.hub.config.session_ttl + 1
            release.set()
            if expired:
                with pytest.raises(CommandError, match="stale_session"):
                    await poll
            else:
                assert await poll is None
                # Повторные пересечения нескольких внутренних операций больше не
                # требуют клиентских retries; ни одной пользовательской команды нет.
                for _ in range(10):
                    results = await asyncio.gather(rig.hub.sweep(), transports[0].exchange("poll", session),
                                                   transports[1].exchange("heartbeat", session),
                                                   transports[2].exchange("poll", session_two))
                    assert results[1] is None and results[3] is None
                assert rig.hub.health()["ready"]
        finally:
            release.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            for transport in transports:
                await transport.close()
            await server.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Действующее показание при refresh и реальные причины отказа
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["success", "failure", "cancel", "expiry", "wide", "rollback", "invalid"])
def test_refresh_readiness(
    tmp_path: Path,
    mode: str,
) -> None:

    """Keep readiness during normal refresh but fail closed at actual trust boundaries.

    :param tmp_path: Temporary journal for a real hub health probe.
    :type tmp_path: Path

    :param mode: Source response or clock boundary to inject.
    :type mode: str
    """

    # tmp_path/mode — локальный hub без сети и ответ источника после явного сигнала.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Обновление UTC с наблюдением готовности во время await
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Inspect health while the external time response is deliberately pending."""

        rig = Rig(tmp_path)
        await rig.hub.start()
        entered, release = asyncio.Event(), asyncio.Event()

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной внешний источник с задержкой ответа
        #----------------------------------------------------------------------------------------------------------
        async def sample() -> TimeSample:

            """Return a fresh interval or a controlled failure after the test signal.

            :return: Synthetic trusted UTC interval.
            :rtype: TimeSample
            """

            entered.set()
            await release.wait()
            if mode == "failure":
                raise OSError("private-time-detail")
            if mode == "invalid":
                return None
            return TimeSample(lower_utc=1000, upper_utc=1010 if mode == "wide" else 1000.1,
                              observed_at=rig.now)
        #----------------------------------------------------------------------------------------------------------

        task = asyncio.create_task(rig.trusted.refresh(SimpleNamespace(sample=sample)))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert rig.hub.health()["ready"] and rig.hub.health()["time_reason"] is None
            assert rig.trusted.check(1000, rig.hub.epoch).deadline is not None
            if mode in {"expiry", "rollback"}:
                rig.now = 99 if mode == "rollback" else 100 + rig.trusted.policy.sample_ttl
                assert not rig.hub.health()["ready"]
                assert rig.hub.health()["time_reason"] == ("clock_rollback" if mode == "rollback"
                                                          else "sample_expired")
                assert rig.trusted.check(1000, rig.hub.epoch).deadline is None
            if mode == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                if mode in {"failure", "wide", "invalid"}:
                    with pytest.raises(TimeUnavailable):
                        await task
                else:
                    await task
            reason = {"failure": "source_failed", "wide": "uncertainty", "cancel": "refresh_cancelled",
                      "invalid": "invalid_sample"}.get(mode)
            health = rig.hub.health()
            assert health["time_reason"] == reason
            assert health["ready"] == (reason is None)
            assert "private" not in json.dumps(health)
            if reason is not None:
                assert rig.trusted.check(1000, rig.hub.epoch).deadline is None
                rig.trusted.install(TimeSample(lower_utc=1000, upper_utc=1000.1, observed_at=rig.now))
                assert rig.hub.health()["ready"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка закрытого набора причин в HTTP и health
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reason", ["storage_busy", "storage_queue_full", "storage_wait_timeout",
    "session_poll_active", "http_requests_full", "http_polls_full", "transport_requests_full",
    "client_operation_active", "PRIVATE", None, 4,
    ["PRIVATE"], {"PRIVATE": True}])
def test_busy_details(reason: object) -> None:

    """Retain only known contention reasons, clear current details on recovery and keep counters.

    :param reason: Valid reason or adversarial JSON-compatible value.
    :type reason: object
    """

    # reason — значение без доверия; в исключение/health попадает только whitelist.
    data = json.dumps({"code": "busy", "busy_reason": reason}).encode()
    error = decode_error(data, 429)
    expected = reason if isinstance(reason, str) and reason != "PRIVATE" else None
    assert error.code == "busy" and error.http_status == 429 and error.busy_reason == expected
    assert "PRIVATE" not in str(error)
    for status in (400, 409, 503):
        assert decode_error(data, status).busy_reason is None
    assert CommandError("unavailable", busy_reason=reason).busy_reason is None
    state = HealthState()
    state.failure("poll", error, object())
    assert state._stages["poll"].busy_reason == expected
    state.success("heartbeat")
    assert state._stages["poll"].current_error == "busy"
    state.success("poll")
    assert state._stages["poll"].busy_reason is None
    assert state._stages["poll"].recoveries == 1
    assert state._stages["poll"].transient_failures == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Истечение и повтор grant во время очереди без второго разрешения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("expired", [False, True])
def test_queued_claims(
    tmp_path: Path,
    expired: bool,
) -> None:

    """Recheck durable deadlines after storage waiting and grant execution at most once.

    :param tmp_path: Directory containing the real durable hub store.
    :type tmp_path: Path

    :param expired: Advance monotonic time while both claims are queued.
    :type expired: bool
    """

    # tmp_path/expired — настоящий STARTED/результат, без вызова пользовательского callback.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Два одинаковых claim за одной удерживаемой операцией
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Queue duplicate requests and inspect durable outcomes after releasing the store."""

        rig = Rig(tmp_path)
        await rig.start()
        release, entered = threading.Event(), threading.Event()
        jobs = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Удержание единственного исполнителя
        #----------------------------------------------------------------------------------------------------------
        def block() -> None:

            """Keep later grant decisions outside the executor until released."""

            entered.set()
            assert release.wait(10)
        #----------------------------------------------------------------------------------------------------------

        try:
            request = rig.request()
            await rig.submit(request)
            claim = CommandClaim(ref=request.ref, claim_id="d" * 32, request_digest=message_digest(request))
            blocked = asyncio.create_task(rig.hub._worker.call(block))
            jobs.append(blocked)
            assert await asyncio.to_thread(entered.wait, 5)
            claims = [asyncio.create_task(rig.hub.claim(APP_TOKEN, claim)) for _ in range(2)]
            jobs.extend(claims)
            await asyncio.sleep(0)
            assert rig.hub._worker._waiters == 2
            if expired:
                rig.now += 121
            release.set()
            await blocked
            outcomes = await asyncio.gather(*claims, return_exceptions=True)
            if expired:
                assert all(isinstance(item, CommandResult) for item in outcomes)
                assert not any(isinstance(item, CommandGrant) for item in outcomes)
            else:
                assert sum(isinstance(item, CommandGrant) for item in outcomes) == 1
                failures = [item for item in outcomes if isinstance(item, CommandError)]
                assert len(failures) == 1 and failures[0].code == "already_started"
        finally:
            release.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_contention не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
