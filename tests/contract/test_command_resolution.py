# Административная сверка двух итогов без повтора команды и потери истории.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Функции:
# -> conflict(): Два остановленных владельца с COMPLETED и UNKNOWN одной команды
#
# Тесты:
# -> test_resolution_receipt(): Сверка, перезапуск и точная квитанция без исполнения
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#
# -> test_resolution_guards(): Отказы до записи при неверном доказательстве или подтверждении
#
# -> test_resolution_atomicity(): Полный откат ошибки в середине фиксации
#
# -> test_resolution_cli(): Безопасный CLI и запрет перезаписи доказательства
#
# -> test_resolution_health(): Понятная причина сверки до reconnect и после закрытия
#
# -> test_resolution_limits(): Соблюдение пределов журнала и размера доказательства
#
# -> test_resolution_migration(): Обычное открытие схемы 1 и каскадная очистка решения


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from secrets import token_hex
from unittest.mock import patch

import pytest
from test_command_hub import APP_TOKEN, Rig
from test_command_reconnect import fast_client, restart, wait_for

from remote_watch.commands.client import CommandClient
from remote_watch.commands.health import HealthState
from remote_watch.commands.protocol import CommandResult, callback_result
from remote_watch.commands.resolution import ResolutionError, export_evidence, pending_summary, reconcile
from remote_watch.commands.sqlite_store import SQLiteCommandStore
from remote_watch.commands.storage import StoreRole
from remote_watch.commands.transport import CommandError
from remote_watch.diagnostics.command_resolution import main

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Два остановленных владельца с COMPLETED и UNKNOWN одной команды
#------------------------------------------------------------------------------------------------------------------
async def conflict(
    path: Path,
    ) -> tuple[Rig, CommandResult]:

    """
    Persist a client completion while the restarted hub recovers unknown execution.

    :param path: Isolated synthetic databases and evidence.
    :type path: Path

    :return: Closed fixture and its original completed result.
    :rtype: tuple[Rig, CommandResult]
    """

    # path — только синтетические данные; потеря ответа моделируется до hub.commit.
    rig = Rig(path)
    await rig.start()
    try:
        await rig.submit(rig.request())
        ticket = await rig.client.acquire()
        assert rig.client.begin(ticket)
        result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "private-result-Ж")
        with patch.object(rig.transport, "exchange", side_effect=CommandError("unavailable")):
            with pytest.raises(CommandError, match="unavailable"):
                await rig.client.complete(ticket, result)
        await rig.client.close()
        await restart(rig, path)
        assert rig.store.get(result.ref).record.result.outcome.value == "unknown"
    finally:
        await rig.close()
    export_evidence(path / "client.sqlite", 1, path / "evidence.json")
    return rig, result
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сверка, перезапуск и точная квитанция без исполнения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("legacy", [False, True])
def test_resolution_receipt(
    tmp_path: Path,
    legacy: bool,
    ) -> None:

    """
    Recover the blocked route and preserve UNKNOWN across migration and lost receipts.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path

    :param legacy: Start with the previous on-disk schema.
    :type legacy: bool
    """

    # tmp_path/legacy — решение связывается с исходным claim, а не новой сессией.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Apply once, retry safely and resume ordinary reconnect with no callback replay.
        """

        rig, result = await conflict(tmp_path)
        hub, evidence = tmp_path / "hub.sqlite", tmp_path / "evidence.json"
        if legacy:
            with sqlite3.connect(hub) as db:
                db.execute("DROP TABLE resolutions")
                db.execute("PRAGMA user_version=1")
        before = hub.read_bytes()
        preview = reconcile(hub, evidence)
        assert hub.read_bytes() == before
        assert preview["status"] == "preview"
        assert pending_summary(tmp_path / "client.sqlite")["records"][0]["phase"] == "completed"
        applied = reconcile(hub, evidence, expected=preview["fingerprint"], owners_stopped=True)
        assert applied["status"] == "applied"
        repeated = reconcile(hub, evidence, expected=preview["fingerprint"], owners_stopped=True)
        assert repeated["status"] == "already_applied"
        with sqlite3.connect(hub) as db:
            assert db.execute("SELECT COUNT(*) FROM resolutions").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM audit WHERE code='admin_resolved'").fetchone()[0] == 1
            assert db.execute("SELECT phase,running FROM commands").fetchone() == ("unknown", 0)
            assert db.execute("PRAGMA user_version").fetchone()[0] == 2

        await restart(rig, tmp_path)
        generation = token_hex(16)
        rig.local = SQLiteCommandStore(tmp_path / "client.sqlite", owner_id="app", generation=generation,
                                      role=StoreRole.CLIENT, clock=lambda: rig.now)
        rig.registration = replace(rig.registration, session_id=generation)
        rig.client = CommandClient(rig.registration, rig.transport, rig.local, clock=lambda: rig.now)
        await rig.client.start()
        fast_client(rig)
        try:
            # Точный результат принят, но первый ответ потерян; повтор остаётся безопасным.
            rig.transport.lose = "result"
            with pytest.raises(CommandError, match="unavailable"):
                await rig.client.flush_results()
            assert rig.client.health.has_pending_results
            await rig.client.flush_results()
            assert not rig.client.health.has_pending_results
            assert (await rig.client.lookup(result.ref)).acknowledged
            assert rig.store.get(result.ref).record.result.outcome.value == "unknown"
            assert not rig.store.get(result.ref).execution_active
            assert rig.store.resolved_result(result.ref) == result
            assert await rig.client.acquire() is None
            # Подмена принятого итога и неверный principal не получают квитанцию.
            with pytest.raises(CommandError, match="outcome_conflict"):
                await rig.hub.finish(APP_TOKEN, replace(result, text="forged"))
            with pytest.raises(CommandError):
                await rig.hub.finish("invalid", result)
            await restart(rig, tmp_path)
            await wait_for(lambda: rig.client.health.reconnects == 1)
            assert rig.client.health.ready and await rig.client.acquire() is None
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказы до записи при неверном доказательстве или подтверждении
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["confirmation", "fingerprint", "claim", "request", "outcome", "role", "stale"])
def test_resolution_guards(
    tmp_path: Path,
    mode: str,
    ) -> None:

    """
    Reject mismatches and stale previews without partially changing either journal.

    :param tmp_path: Synthetic journal directory.
    :type tmp_path: Path

    :param mode: Invalid input or missing explicit operator attestation.
    :type mode: str
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.


    # mode — изменения payload ограничены тестовым файлом и не выводятся в CLI.
    asyncio.run(conflict(tmp_path))
    hub, evidence = tmp_path / "hub.sqlite", tmp_path / "evidence.json"
    preview = reconcile(hub, evidence)
    data = json.loads(evidence.read_text(encoding="utf-8"))
    if mode == "claim":
        data["result"]["payload"]["claim_id"] = "f" * 32
    elif mode == "request":
        data["request"]["payload"]["actor_id"] = "other"
    elif mode == "outcome":
        data["result"]["payload"]["outcome"] = "unknown"
    elif mode in ("role", "stale"):
        with sqlite3.connect(hub) as db:
            db.execute("UPDATE metadata SET role='client'" if mode == "role"
                       else "UPDATE commands SET revision=revision+1")
    evidence.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = hub.read_bytes()
    with pytest.raises((ResolutionError, ValueError)):
        reconcile(hub, evidence, expected="wrong" if mode == "fingerprint" else preview["fingerprint"],
                  owners_stopped=mode != "confirmation")
    assert hub.read_bytes() == before
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полный откат ошибки в середине фиксации
#------------------------------------------------------------------------------------------------------------------
def test_resolution_atomicity(
    tmp_path: Path,
    ) -> None:

    """
    Fail after inserting acceptance and prove that the entire administrative transaction rolls back.

    :param tmp_path: Isolated journals with a synthetic SQLite abort trigger.
    :type tmp_path: Path
    """

    # tmp_path — триггер срабатывает после записи resolutions, но до смены generation.
    asyncio.run(conflict(tmp_path))
    hub, evidence = tmp_path / "hub.sqlite", tmp_path / "evidence.json"
    with sqlite3.connect(hub) as db:
        db.execute("CREATE TRIGGER refuse_resolution BEFORE UPDATE ON commands "
                   "BEGIN SELECT RAISE(ABORT,'private SQL failure'); END")
    preview = reconcile(hub, evidence)
    before = hub.read_bytes()
    with pytest.raises(sqlite3.Error):
        reconcile(hub, evidence, expected=preview["fingerprint"], owners_stopped=True)
    assert hub.read_bytes() == before
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасный CLI и запрет перезаписи доказательства
#------------------------------------------------------------------------------------------------------------------
def test_resolution_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    ) -> None:

    """
    Avoid echoing payloads, paths and low-level exceptions in CLI reports.

    :param tmp_path: Isolated databases and private evidence.
    :type tmp_path: Path

    :param capsys: Captured diagnostic stdout.
    :type capsys: pytest.CaptureFixture[str]
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.


    # capsys — ни текст результата, ни поле пользователя не должны попасть в отчёт.
    asyncio.run(conflict(tmp_path))
    client, hub, evidence = tmp_path / "client.sqlite", tmp_path / "hub.sqlite", tmp_path / "evidence.json"
    assert main(["list", "--journal", str(client)]) == 0
    assert main(["preview", "--journal", str(hub), "--evidence", str(evidence)]) == 0
    assert main(["export", "--journal", str(client), "--sequence", "1", "--output", str(evidence)]) == 2
    assert main(["apply", "--journal", str(hub), "--evidence", str(evidence), "--expect", "wrong"]) == 2
    missing = tmp_path / "absent.sqlite"
    assert main(["list", "--journal", str(missing)]) == 2
    assert not missing.exists()
    output = capsys.readouterr().out
    assert "private-result" not in output and str(tmp_path) not in output and "Traceback" not in output
    assert "owners_stopped_confirmation_required" in output
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Понятная причина сверки до reconnect и после закрытия
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reason", ["outcome_conflict", "unresolved_results", "unknown_execution", "unavailable"])
def test_resolution_health(
    reason: str,
    ) -> None:

    """
    Keep administrative action visible after closure without mislabeling transient outages.

    :param reason: Fixed protocol or reconnect reason.
    :type reason: str
    """

    # reason — конфликт результата замечается до следующего цикла heartbeat.
    health = HealthState()
    if reason == "outcome_conflict":
        health.failure("result", CommandError(reason), object())
    else:
        health.recovery("blocked" if reason != "unavailable" else "reconnecting", reason)
    expected = reason != "unavailable"
    assert health.snapshot(1, 0).operator_action_required == expected
    health.close()
    snapshot = health.snapshot(1, 0)
    assert snapshot.operator_action_required == expected
    assert snapshot.recovery_hint == ("review_command_journal" if expected else None)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Соблюдение пределов журнала и размера доказательства
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["capacity", "oversized", "stale_generation"])
def test_resolution_limits(
    tmp_path: Path,
    mode: str,
    ) -> None:

    """
    Refuse over-capacity acceptance, oversized evidence and a replaced journal generation.

    :param tmp_path: Synthetic administrative inputs.
    :type tmp_path: Path

    :param mode: Boundary to invalidate after a valid preview.
    :type mode: str
    """

    # tmp_path/mode — границы проверяются до необратимой фиксации решения.
    asyncio.run(conflict(tmp_path))
    hub, evidence = tmp_path / "hub.sqlite", tmp_path / "evidence.json"
    if mode == "capacity":
        with sqlite3.connect(hub) as db:
            settings = json.loads(db.execute("SELECT settings FROM metadata").fetchone()[0])
            settings["payload_bytes"] = db.execute("SELECT SUM(reserved) FROM commands").fetchone()[0]
            db.execute("UPDATE metadata SET settings=?", (json.dumps(settings),))
    preview = reconcile(hub, evidence)
    if mode == "oversized":
        evidence.write_bytes(b"x" * 200000)
    elif mode == "stale_generation":
        with sqlite3.connect(hub) as db:
            db.execute("UPDATE metadata SET generation=?", (token_hex(16),))
    before = hub.read_bytes()
    with pytest.raises(ResolutionError):
        reconcile(hub, evidence, expected=preview["fingerprint"], owners_stopped=True)
    assert hub.read_bytes() == before
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Обычное открытие схемы 1 и каскадная очистка решения
#------------------------------------------------------------------------------------------------------------------
def test_resolution_migration(
    tmp_path: Path,
    ) -> None:

    """
    Migrate an existing journal and retain resolution exactly as long as its original command.

    :param tmp_path: Isolated synthetic journal with deterministic retention.
    :type tmp_path: Path
    """

    # tmp_path — миграция не должна потерять UNKNOWN, owner или позиции источников.
    rig, result = asyncio.run(conflict(tmp_path))
    hub, evidence = tmp_path / "hub.sqlite", tmp_path / "evidence.json"
    with sqlite3.connect(hub) as db:
        db.execute("DROP TABLE resolutions")
        db.execute("PRAGMA user_version=1")
        original = db.execute("SELECT request,result,claim_id FROM commands").fetchone()
        streams = db.execute("SELECT * FROM streams").fetchall()
    store = SQLiteCommandStore(hub, owner_id="hub", generation=token_hex(16),
                               role=StoreRole.HUB, clock=lambda: rig.now)
    store.open()
    try:
        assert store.resolved_result(result.ref) is None
        assert store.resolved_result(replace(result.ref, command_id=token_hex(16))) is None
        assert store.get(result.ref).execution_active
    finally:
        store.close()
    preview = reconcile(hub, evidence)
    reconcile(hub, evidence, expected=preview["fingerprint"], owners_stopped=True)
    with sqlite3.connect(hub) as db:
        assert db.execute("SELECT request,result,claim_id FROM commands").fetchone() == original
        assert db.execute("SELECT * FROM streams").fetchall() == streams
        assert db.execute("SELECT owner FROM metadata").fetchone()[0] == "hub"
    store = SQLiteCommandStore(hub, owner_id="hub", generation=token_hex(16),
                               role=StoreRole.HUB, clock=lambda: rig.now)
    store.open()
    try:
        assert store.resolved_result(result.ref) == result
        store.acknowledge(store.get(result.ref).record.result)
        assert store.prune() == 0
        rig.now += 86401
        assert store.prune() == 1
        assert store.resolved_result(result.ref) is None
    finally:
        store.close()
    with sqlite3.connect(hub) as db:
        assert db.execute("SELECT COUNT(*) FROM resolutions").fetchone()[0] == 0
        assert db.execute("SELECT * FROM streams").fetchall() == streams
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Назначение тестового файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Тесты сверки команд запускаются через pytest.")
#------------------------------------------------------------------------------------------------------------------
