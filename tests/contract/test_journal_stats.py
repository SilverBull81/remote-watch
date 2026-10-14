# Сводки SQLite, кэш владельца и безопасная диагностика без изменения команд.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Тесты:
# -> test_command_snapshot(): Подсчёт сохранённых фаз без записи в журнал
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#
# -> test_offline_source(): Replay-ёмкость источника без чтения приватных значений
#
# -> test_offline_refusal(): Отказ без создания БД, recovery или sidecar
#
# -> test_worker_cache(): Чтение кэша при занятом worker и независимость ошибок наблюдения
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#       Вложенные объявления:
#       -> held(): Занятая реальная операция владельца
#
# -> test_ipc_metrics(): Ограниченная проверка IPC, включая максимальную сводку
#
# -> test_doctor_journal(): CLI остановленной БД, предупреждения и приватность отчёта
#
# -> test_doctor_server_cache(): Наблюдение живого сервера только по кэшу manager
#
# -> test_manager_snapshot_age(): Старый heartbeat не делает снимок свежим после сбоя
#
# -> test_observer_budget(): Прерывание SQL-наблюдения не мешает последующей записи
#
# -> test_resolved_unknown(): Согласованный UNKNOWN остаётся историей без предупреждения
#
# -> test_observer_cannot_timeout_commit(): Задержка диагностики не меняет исход и не освобождает executor
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#       Вложенные объявления:
#       -> slow_observation(): Задержка после настоящего успешного изменения журнала


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from test_command_hub import Rig
from test_command_reconnect import restart
from test_command_resolution import conflict

from remote_watch.commands._worker import StoreWorker
from remote_watch.commands.journal_stats import inspect_journal
from remote_watch.commands.protocol import callback_result
from remote_watch.commands.resolution import reconcile
from remote_watch.commands.source_store import SourceJournal
from remote_watch.commands.transport import CommandError
from remote_watch.diagnostics.gateway_doctor import _journal_rows, _server_online, journal_checks, main
from remote_watch.gateway_server.manager import ChildState, GatewayManager
from remote_watch.gateway_server.messages import status_message

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Подсчёт сохранённых фаз без записи в журнал
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("phase", ["ready", "started", "completed", "unknown"])
def test_command_snapshot(
    tmp_path: Path,
    phase: str,
    ) -> None:

    """
    Inspect exact command phases and preserve every byte and generation of the durable journal.

    :param tmp_path: Synthetic hub and client files.
    :type tmp_path: Path

    :param phase: Retained command state to observe.
    :type phase: str
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.


    # phase — UNKNOWN после рестарта не означает, что callback продолжает работать.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Create real protocol transitions and compare owner and offline counts.
        """

        rig = Rig(tmp_path)
        await rig.start()
        try:
            await rig.submit(rig.request())
            if phase != "ready":
                ticket = await rig.client.acquire()
                assert rig.client.begin(ticket)
                if phase == "completed":
                    await rig.client.complete(ticket,
                        callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "private-result"))
                elif phase == "unknown":
                    await restart(rig, tmp_path)
            path = tmp_path / "hub.sqlite"
            before = path.read_bytes()
            sample = rig.store.journal_stats()
            assert path.read_bytes() == before
            assert sample.records == 1 and sample.records_limit == 1024 and sample.pending == 1
            assert sample.unknown == int(phase == "unknown")
            assert sample.unresolved_unknown == sample.unknown
            assert sample.execution_active == int(phase in ("started", "unknown"))
            assert sample.payload_reserved_bytes > 0 and sample.database_limit_bytes == 67108864
            assert sample.payload_bytes <= sample.payload_reserved_bytes
            assert "private-result" not in repr(sample)
            # Тот же снимок доступен в клиенте без вызова хранилища при чтении.
            with patch.object(rig.local, "journal_stats", side_effect=RuntimeError("private")):
                assert rig.client.journal_status["snapshot"]["kind"] == "client"
        finally:
            await rig.close()
        before = (tmp_path / "hub.sqlite").read_bytes()
        offline = inspect_journal(tmp_path / "hub.sqlite")
        assert offline == sample and (tmp_path / "hub.sqlite").read_bytes() == before
        if phase == "ready":
            # Историческая схема 1 читается без автоматического перевода в 2.
            with sqlite3.connect(tmp_path / "hub.sqlite") as db:
                db.execute("DROP TABLE resolutions")
                db.execute("PRAGMA user_version=1")
            before = (tmp_path / "hub.sqlite").read_bytes()
            assert inspect_journal(tmp_path / "hub.sqlite").schema_version == 1
            assert (tmp_path / "hub.sqlite").read_bytes() == before
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Replay-ёмкость источника без чтения приватных значений
#------------------------------------------------------------------------------------------------------------------
def test_offline_source(
    tmp_path: Path,
    ) -> None:

    """
    Keep configured source limits separate from limits not persisted in an offline file.

    :param tmp_path: Isolated source owner and its stopped journal.
    :type tmp_path: Path
    """

    # tmp_path — приватные значения нужны только для проверки отсутствия их в отчёте.
    path = tmp_path / "source.sqlite"
    source = SourceJournal(path, seen_limit=32)
    source.open()
    source.bind("private-provider-fingerprint")
    try:
        # Синтетические seen-строки не содержат настоящих ID и не запускают provider.
        with source._db:
            source._db.executemany("INSERT INTO seen VALUES (?,0)",
                                   [("private-event-" + str(i),) for i in range(26)])
            source._db.execute("UPDATE state SET cursor='private-cursor',pending='private-payload'")
        before = path.read_bytes()
        stats = source.journal_stats()
        assert stats.records == 26 and stats.records_limit == 32 and stats.pending == 1
        assert stats.capacity == "near_limit" and stats.unknown is None and stats.execution_active is None
        assert stats.database_limit_bytes > 0 and path.read_bytes() == before
        assert "private" not in repr(stats)
    finally:
        source.close()
    files = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    offline = inspect_journal(path)
    assert offline.records == 26 and offline.records_limit is None
    assert offline.database_limit_bytes is None and offline.capacity == "unknown"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == files
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ без создания БД, recovery или sidecar
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["missing", "unknown", "rollback", "wal", "corrupt"])
def test_offline_refusal(
    tmp_path: Path,
    mode: str,
    ) -> None:

    """
    Refuse unavailable or incomplete offline journals without creating files or repairing them.

    :param tmp_path: Isolated directory for malformed inputs.
    :type tmp_path: Path

    :param mode: Refusal boundary.
    :type mode: str
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.


    # mode — даже имя отсутствующего файла не должно стать новой пустой SQLite.
    path = tmp_path / "private-journal.sqlite"
    if mode != "missing":
        if mode == "corrupt":
            path.write_bytes(b"private-not-a-database")
        else:
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE private_table(secret TEXT)")
            if mode in ("rollback", "wal"):
                Path(str(path) + ("-journal" if mode == "rollback" else "-wal")).write_bytes(b"private-sidecar")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    rows = journal_checks(path)
    assert rows[0]["status"] == "failed" and rows[0]["code"] == "journal_read_failed"
    assert "private" not in repr(rows)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Чтение кэша при занятом worker и независимость ошибок наблюдения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cancel_observation", [False, True])
def test_worker_cache(
    tmp_path: Path,
    cancel_observation: bool,
    ) -> None:

    """
    Preserve successful results and original errors when the optional observation fails.

    :param tmp_path: Isolated real source store.
    :type tmp_path: Path

    :param cancel_observation: Contain a misbehaving observer's cancellation separately from the original work.
    :type cancel_observation: bool
    """

    # tmp_path/cancel_observation — ошибка и самовольная отмена наблюдателя не меняют исход операции.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Block one owner operation and read the cached status from its loop.
        """

        source = SourceJournal(tmp_path / "source.sqlite")
        worker = StoreWorker(source, 1)
        entered, release = threading.Event(), threading.Event()
        await worker.open()
        assert worker.journal_status()["state"] == "available"
        assert worker.journal_status()["snapshot"]["records"] == 0

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Занятая реальная операция владельца
        #----------------------------------------------------------------------------------------------------------
        def held() -> int:

            """
            Keep the one executor occupied until the test releases it.

            :return: Unmodified operation result.
            :rtype: int
            """

            entered.set()
            release.wait(2)
            return 42
        #----------------------------------------------------------------------------------------------------------

        task = asyncio.create_task(worker.call(held))
        try:
            while not entered.is_set():
                await asyncio.sleep(0)
            failure = asyncio.CancelledError() if cancel_observation else RuntimeError("private-error")
            with patch.object(source, "journal_stats", side_effect=failure) as reader:
                for _ in range(20):
                    assert worker.journal_status()["state"] == "available"
                assert reader.call_count == 0
                # Принудительный срок наблюдения, без ожидания пяти реальных секунд.
                worker._journal_next = 0
                release.set()
                assert await task == 42
                assert worker.journal_status()["state"] == "unavailable"
                assert worker.journal_status()["snapshot"]["records"] == 0
                worker._journal_next = 0
                sentinel = CommandError("denied")
                with pytest.raises(CommandError) as error:
                    await worker.call(Mock(side_effect=sentinel))
                assert error.value is sentinel
            worker._journal_next = 0
            assert await worker.call(lambda: 7) == 7
            assert worker.journal_status()["state"] == "available"
        finally:
            release.set()
            await worker.close(1)
        assert worker.journal_status()["state"] == "closed"
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограниченная проверка IPC, включая максимальную сводку
#------------------------------------------------------------------------------------------------------------------
def test_ipc_metrics(
    tmp_path: Path,
    ) -> None:

    """
    Validate nine bounded journal snapshots and reject fields capable of leaking payloads.

    :param tmp_path: Synthetic SQLite used to construct a real valid snapshot.
    :type tmp_path: Path
    """

    # tmp_path — никаких произвольных source ID в IPC нет, только позиции в списке.
    source = SourceJournal(tmp_path / "source.sqlite")
    source.open()
    try:
        value = asdict(source.journal_stats())
    finally:
        source.close()
    large = {key: number if type(number) is not int else 2**63 - 1 for key, number in value.items()}
    large.update(schema_version=1, pending=1)
    status = {"state": "available", "age_seconds": 2**31 - 1, "snapshot": large}
    message = {"state": "ready", "reason": None, "time_reason": None, "failure": None, "counters": {},
               "journals": {"hub": status, "sources": [status] * 8}}
    safe = status_message(message)
    assert len(json.dumps(safe).encode()) < 14000
    for key, invalid in (("private", "secret"), ("records", True), ("kind", "private"), ("records", -1)):
        forged = dict(value)
        forged[key] = invalid
        bad = dict(message, journals={"hub": dict(status, snapshot=forged), "sources": []})
        with pytest.raises(ValueError):
            status_message(bad)
    with pytest.raises(ValueError):
        status_message(dict(message, journals={"hub": status, "sources": [status] * 9}))
    # Deep copy: дальнейшее изменение входного JSON не меняет принятый кэш.
    large["records"] = 0
    assert safe["journals"]["hub"]["snapshot"]["records"] != 0
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : CLI остановленной БД, предупреждения и приватность отчёта
#------------------------------------------------------------------------------------------------------------------
def test_doctor_journal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    ) -> None:

    """
    Return safe stopped-journal metrics without exposing its path or application payload.

    :param tmp_path: Synthetic source and new report.
    :type tmp_path: Path

    :param capsys: Captured shareable CLI output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.


    # capsys — режим journal не читает конфиги/токены и не запускает сетевых проверок.
    path = tmp_path / "private-source.sqlite"
    source = SourceJournal(path)
    source.open()
    source.close()
    report = tmp_path / "doctor.json"
    assert main(["--mode", "journal", "--journal", str(path), "--json", "--report", str(report)]) == 0
    output = capsys.readouterr().out
    assert "private" not in output and str(tmp_path) not in output
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["mode"] == "journal" and data["passed"]
    assert data["checks"][0]["journal"]["kind"] == "source"
    status = {"state": "available", "age_seconds": 31, "snapshot": asdict(inspect_journal(path))}
    status["snapshot"].update(kind="hub", records=10, pending=2, unknown=1, unresolved_unknown=1,
                              execution_active=1, records_limit=10, capacity="at_limit")
    rows = _journal_rows("journal", status)
    assert {row["code"] for row in rows} == {"journal_stale", "journal_capacity", "journal_unknown"}
    assert all(row["status"] == "warning" for row in rows)
    for args in (["--mode", "journal", "--journal", str(path), "--online"], ["--mode", "journal"]):
        with pytest.raises(SystemExit):
            main(args)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Наблюдение живого сервера только по кэшу manager
#------------------------------------------------------------------------------------------------------------------
def test_doctor_server_cache(
    tmp_path: Path,
    ) -> None:

    """
    Consume the local manager's cached aggregate without opening any journal from doctor.

    :param tmp_path: Synthetic stopped source for a safe snapshot fixture.
    :type tmp_path: Path
    """

    # tmp_path — source здесь нужен только для формы снимка; live-проверка БД не читает.
    path = tmp_path / "source.sqlite"
    source = SourceJournal(path)
    source.open()
    value = {"state": "available", "age_seconds": 0, "snapshot": asdict(source.journal_stats())}
    source.close()
    snapshot = {"state": "ready", "components": {"commands": {"state": "ready",
                "journals": {"hub": value, "sources": [value]}}}}
    config = Mock(notifications=None, commands=None)
    with patch("remote_watch.gateway_server.manager.control_request", new=AsyncMock(return_value=snapshot)):
        with patch("remote_watch.diagnostics.gateway_doctor.inspect_journal", side_effect=AssertionError):
            rows = asyncio.run(_server_online(config, 1))
    metrics = [row for row in rows if row.get("journal") is not None]
    assert len(metrics) == 2 and all(row["status"] == "ok" for row in metrics)
    assert metrics[1]["check"] == "commands.journal.source_1"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Старый heartbeat не делает снимок свежим после сбоя
#------------------------------------------------------------------------------------------------------------------
def test_manager_snapshot_age(
    tmp_path: Path,
    ) -> None:

    """
    Keep age and detached snapshots correct when a child stops reporting or restarts.

    :param tmp_path: Synthetic journal fixture.
    :type tmp_path: Path
    """

    # tmp_path — экземпляр manager не запускается; проверяется только преобразование кэша.
    source = SourceJournal(tmp_path / "source.sqlite")
    source.open()
    stats = asdict(source.journal_stats())
    source.close()
    child = Mock(spec=ChildState)
    child.message = {"state": "ready", "reason": None, "time_reason": None, "failure": None, "counters": {},
                     "journals": {"hub": {"state": "available", "age_seconds": 2, "snapshot": stats},
                                  "sources": []}}
    child.contact, child.contacted, child.restarts = 100, True, 0
    with patch("remote_watch.gateway_server.manager.monotonic", return_value=140):
        first = GatewayManager._component_status(None, child)
        assert first["journals"]["hub"]["age_seconds"] == 42
        assert child.message["journals"]["hub"]["age_seconds"] == 2
        child.message["state"] = "failed"
        stopped = GatewayManager._component_status(None, child)
        assert stopped["journals"]["hub"]["state"] == "unavailable"
        assert stopped["journals"]["hub"]["age_seconds"] == 42
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Прерывание SQL-наблюдения не мешает последующей записи
#------------------------------------------------------------------------------------------------------------------
def test_observer_budget(
    tmp_path: Path,
    ) -> None:

    """
    Restore progress handling and busy timeout after a metrics query exceeds its budget.

    :param tmp_path: Synthetic source with enough replay rows to invoke progress handling.
    :type tmp_path: Path
    """

    # tmp_path — срок истекает детерминированно; реального ожидания и зависшего I/O нет.
    source = SourceJournal(tmp_path / "source.sqlite")
    source.open()
    try:
        with source._db:
            source._db.executemany("INSERT INTO seen VALUES (?,0)", [(str(i),) for i in range(500)])
        before = (tmp_path / "source.sqlite").read_bytes()
        with patch("remote_watch.commands.journal_stats.monotonic", side_effect=[0, 1]):
            with pytest.raises(sqlite3.OperationalError):
                source.journal_stats()
        assert (tmp_path / "source.sqlite").read_bytes() == before
        assert not source._db.in_transaction
        assert source._db.execute("PRAGMA busy_timeout").fetchone()[0] == 1000
        source.bind("same-owner-after-observation-timeout")
        assert source.journal_stats().records == 500
    finally:
        source.close()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Согласованный UNKNOWN остаётся историей без предупреждения
#------------------------------------------------------------------------------------------------------------------
def test_resolved_unknown(
    tmp_path: Path,
    ) -> None:

    """
    Keep original UNKNOWN while reporting separately whether it lacks an administrative decision.

    :param tmp_path: Synthetic stopped conflict and accepted evidence.
    :type tmp_path: Path
    """

    # tmp_path — используется настоящий путь сверки, без ручного ACK или замены итога.
    asyncio.run(conflict(tmp_path))
    path, evidence = tmp_path / "hub.sqlite", tmp_path / "evidence.json"
    before = inspect_journal(path)
    assert before.unknown == before.unresolved_unknown == 1 and before.resolutions == 0
    preview = reconcile(path, evidence)
    reconcile(path, evidence, expected=preview["fingerprint"], owners_stopped=True)
    after = inspect_journal(path)
    assert after.unknown == 1 and after.unresolved_unknown == 0 and after.resolutions == 1
    assert after.execution_active == 0
    rows = _journal_rows("journal", {"state": "available", "age_seconds": 0, "snapshot": asdict(after)})
    assert not any(row["code"] == "journal_unknown" for row in rows)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Задержка диагностики не меняет исход и не освобождает executor
#------------------------------------------------------------------------------------------------------------------
def test_observer_cannot_timeout_commit(
    tmp_path: Path,
    ) -> None:

    """
    Return an in-budget committed result even if a later observation remains blocked.

    :param tmp_path: Isolated source and controlled observer blocking.
    :type tmp_path: Path
    """

    # tmp_path — имитируется зависший I/O наблюдения; новая запись не выполняется параллельно.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Keep the real storage lane pinned while preserving the original operation outcome.
        """

        source = SourceJournal(tmp_path / "source.sqlite")
        worker = StoreWorker(source, 0.1)
        await worker.open()
        entered, release = threading.Event(), threading.Event()
        original = source.journal_stats

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Задержка после настоящего успешного изменения журнала
        #----------------------------------------------------------------------------------------------------------
        def slow_observation() -> object:

            """
            Block diagnostics only, while allowing the original bind to finish.

            :return: Normal source aggregate after release.
            :rtype: object
            """

            entered.set()
            release.wait(2)
            return original()
        #----------------------------------------------------------------------------------------------------------

        worker._journal_next = 0
        try:
            with patch.object(source, "journal_stats", side_effect=slow_observation):
                assert await worker.call(lambda: source.bind("committed-owner")) is None
                assert entered.is_set() and worker._gate.locked()
                assert not worker._pending.done()
                # Один следующий caller ждёт наблюдателя в прежнем общем бюджете,
                # а не получает мгновенный busy и не запускается параллельно.
                next_call = asyncio.create_task(worker.call(source.state))
                await asyncio.sleep(0.01)
                assert not next_call.done()
                release.set()
                assert (await next_call)[0] == -1
        finally:
            release.set()
            await worker.close(1)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Назначение тестового файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Тесты сводки журналов запускаются через pytest.")
#------------------------------------------------------------------------------------------------------------------
