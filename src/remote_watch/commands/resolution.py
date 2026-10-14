# Явная сверка завершённой команды с неопределённым итогом на остановленном hub.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-164146
#
# Классы:
# -> ResolutionError: Безопасная категория отказа без содержимого журналов
#
# Функции:
# -> journal(): Открытие существующей БД без восстановления поколения
#
# -> pending_summary(): Ограниченный список записей для локального разбора
#
# -> export_evidence(): Выгрузка точного завершённого результата клиента
#
# -> reconcile(): Предпросмотр либо атомарная фиксация решения владельца
#
# -> _evidence(): Строгая проверка ограниченного файла доказательства
#
# -> _match(): Проверка запроса, claim и неизменённого итога hub
#
# -> _fingerprint(): Отпечаток конкретного состояния перед применением
#
# -> _apply(): Отдельная запись решения без замены UNKNOWN


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from remote_watch.commands.protocol import (
    MAX_COMMAND_BYTES,
    CommandOutcome,
    CommandRequest,
    CommandResult,
    decode_command,
    encode_command,
)

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Безопасная категория отказа без содержимого журналов
#------------------------------------------------------------------------------------------------------------------
class ResolutionError(RuntimeError):
    """
    Report only fixed local resolution failure codes.
    """
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Открытие существующей БД без восстановления поколения
#------------------------------------------------------------------------------------------------------------------
@contextmanager
def journal(
    path: Path,
    *,
    writable: bool = False,
    ) -> Iterator[sqlite3.Connection]:

    """
    Open an existing journal without invoking runtime recovery or creating a file.

    :param path: Operator-selected local journal.
    :type path: Path

    :param writable: Explicit administrative transaction, otherwise read-only.
    :type writable: bool

    :return: Bounded SQLite connection closed on exit.
    :rtype: Iterator[sqlite3.Connection]
    """

    # path/writable — запись доступна только явному административному действию.

    db = sqlite3.connect(path.resolve().as_uri() + ("?mode=rw" if writable else "?mode=ro"),
                         uri=True, timeout=1, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        if not writable:
            db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN EXCLUSIVE" if writable else "BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] not in (1, 2):
            raise ResolutionError("unsupported_schema")
        yield db
        if writable:
            db.commit()
    finally:
        # Исключение до commit оставляет обе записи и поколение без изменений.
        if db.in_transaction:
            db.rollback()
        db.close()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ограниченный список записей для локального разбора
#------------------------------------------------------------------------------------------------------------------
def pending_summary(
    path: Path,
    ) -> dict[str, object]:

    """
    List safe state categories and local sequence numbers without command payloads.

    :param path: Existing journal to inspect read-only.
    :type path: Path

    :return: Bounded report without identities, arguments or result text.
    :rtype: dict[str, object]
    """

    # path — журнал читается напрямую, без open(), recovery и изменения generation.

    with journal(path) as db:
        role = db.execute("SELECT role FROM metadata WHERE id=1").fetchone()[0]
        if role not in ("client", "hub"):
            raise ResolutionError("invalid_journal")
        rows = db.execute("SELECT sequence, phase, acknowledged, running FROM commands "
                          "WHERE acknowledged=0 OR running=1 ORDER BY sequence LIMIT 1001").fetchall()
        states = {"ready", "claimed", "started", "completed", "unknown", "rejected", "expired"}
        return {"role": role, "truncated": len(rows) > 1000, "records": [
            {"sequence": row["sequence"], "phase": row["phase"] if row["phase"] in states else "invalid",
             "acknowledged": bool(row["acknowledged"]), "execution_active": bool(row["running"])}
            for row in rows[:1000]]}
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Выгрузка точного завершённого результата клиента
#------------------------------------------------------------------------------------------------------------------
def export_evidence(
    path: Path,
    sequence: int,
    output: Path,
    ) -> None:

    """
    Export one completed nonrunning result into a new private evidence file.

    :param path: Existing client journal, preferably from a stopped application.
    :type path: Path

    :param sequence: Explicit local record selected by the operator.
    :type sequence: int

    :param output: New file; existing files are never overwritten.
    :type output: Path
    """

    # path — путь к проверяемому файлу; содержимое не выводится в диагностику.


    # sequence/output — выбирается ровно одна запись; файл содержит приватные данные команд.

    with journal(path) as db:
        if db.execute("SELECT role FROM metadata WHERE id=1").fetchone()[0] != "client":
            raise ResolutionError("client_journal_required")
        row = db.execute("SELECT * FROM commands WHERE sequence=?", (sequence,)).fetchone()
        if row is None or row["phase"] != "completed" or row["running"] or row["acknowledged"]:
            raise ResolutionError("completed_unacknowledged_required")
        request, result = decode_command(row["request"]), decode_command(row["result"])
        if (type(request) is not CommandRequest or type(result) is not CommandResult
                or result.outcome is not CommandOutcome.COMPLETED or result.ref != request.ref
                or result.claim_id is None or result.claim_id != row["claim_id"]):
            raise ResolutionError("invalid_evidence")
        payload = {"schema": 1, "request": json.loads(encode_command(request)),
                   "result": json.loads(encode_command(result))}

    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Предпросмотр либо атомарная фиксация решения владельца
#------------------------------------------------------------------------------------------------------------------
def reconcile(
    path: Path,
    evidence: Path,
    *,
    expected: str | None = None,
    owners_stopped: bool = False,
    ) -> dict[str, object]:

    """
    Preview or explicitly accept one client result without rewriting the hub outcome.

    :param path: Actual local hub journal, never a client journal.
    :type path: Path

    :param evidence: Private evidence file copied from the owning application.
    :type evidence: Path

    :param expected: Preview fingerprint; absence selects read-only mode.
    :type expected: str | None

    :param owners_stopped: Operator attestation that hub and execution owner have stopped.
    :type owners_stopped: bool

    :return: Safe summary and fingerprint without payload values.
    :rtype: dict[str, object]
    """

    # path — путь к проверяемому файлу; содержимое не выводится в диагностику.
    # evidence — данные результата клиента для сверки с записью сервера.
    # expected — ожидаемые данные для точной проверки результата.


    # owners_stopped — явное подтверждение оператора; сетевая проверка не доказывает остановку исполнения.
    # Окончание процесса приложения проверяется человеком до снятия execution_active.

    if expected is not None and not owners_stopped:
        raise ResolutionError("owners_stopped_confirmation_required")

    request, result = _evidence(evidence)
    with journal(path, writable=expected is not None) as db:
        meta = db.execute("SELECT * FROM metadata WHERE id=1").fetchone()
        if meta is None or meta["role"] != "hub":
            raise ResolutionError("hub_journal_required")
        row = db.execute("SELECT * FROM commands WHERE command_id=?", (request.ref.command_id,)).fetchone()
        _match(row, request, result)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        previous = (db.execute("SELECT * FROM resolutions WHERE command_id=?",
                              (request.ref.command_id,)).fetchone() if version == 2 else None)
        if previous is not None:
            if decode_command(previous["accepted"]) != result:
                raise ResolutionError("resolution_conflict")
            if expected is not None and expected != previous["fingerprint"]:
                raise ResolutionError("preview_changed")
            return {"status": "already_applied", "fingerprint": previous["fingerprint"]}
        fingerprint = _fingerprint(meta, row, result)
        if expected is not None:
            if expected != fingerprint:
                raise ResolutionError("preview_changed")
            _apply(db, meta, row, result, fingerprint)
        return {"status": "applied" if expected is not None else "preview", "fingerprint": fingerprint,
                "hub_outcome": "unknown", "client_outcome": "completed",
                "decision": "execution_ended_accept_client_result",
                "original_outcome_preserved": True}
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Строгая проверка ограниченного файла доказательства
#------------------------------------------------------------------------------------------------------------------
def _evidence(
    path: Path,
    ) -> tuple[CommandRequest, CommandResult]:

    """
    Decode bounded canonical command objects from an operator-supplied file.

    :param path: Private evidence path, never printed.
    :type path: Path

    :return: Validated request and completed result.
    :rtype: tuple[CommandRequest, CommandResult]
    """

    # path — даже локальный файл ограничен до разбора JSON.

    with path.open("rb") as handle:
        raw = handle.read(2 * MAX_COMMAND_BYTES + 1025)

    if len(raw) > 2 * MAX_COMMAND_BYTES + 1024:
        raise ResolutionError("invalid_evidence")

    obj = json.loads(raw)
    if type(obj) is not dict or set(obj) != {"schema", "request", "result"} or type(obj["schema"]) is not int:
        raise ResolutionError("invalid_evidence")

    if obj["schema"] != 1:
        raise ResolutionError("invalid_evidence")

    request = decode_command(json.dumps(obj["request"], ensure_ascii=False, separators=(",", ":")).encode())
    result = decode_command(json.dumps(obj["result"], ensure_ascii=False, separators=(",", ":")).encode())
    if (type(request) is not CommandRequest or type(result) is not CommandResult
            or result.outcome is not CommandOutcome.COMPLETED or result.ref != request.ref
            or result.claim_id is None):
        raise ResolutionError("invalid_evidence")

    return request, result
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка запроса, claim и неизменённого итога hub
#------------------------------------------------------------------------------------------------------------------
def _match(
    row: sqlite3.Row | None,
    request: CommandRequest,
    result: CommandResult,
    ) -> None:

    """
    Reject absent, unrelated or non-unknown hub records.

    :param row: Selected hub row.
    :type row: sqlite3.Row | None

    :param request: Original complete client request.
    :type request: CommandRequest

    :param result: Original completed client result.
    :type result: CommandResult
    """

    # row/request/result — совпадение одной команды или имени приложения недостаточно.

    if row is None or decode_command(row["request"]) != request or row["claim_id"] != result.claim_id:
        raise ResolutionError("command_mismatch")

    original = decode_command(row["result"]) if row["result"] is not None else None
    if (row["phase"] != "unknown" or type(original) is not CommandResult
            or original.outcome is not CommandOutcome.UNKNOWN or original.ref != result.ref
            or original.claim_id != result.claim_id):
        raise ResolutionError("hub_unknown_required")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Отпечаток конкретного состояния перед применением
#------------------------------------------------------------------------------------------------------------------
def _fingerprint(
    meta: sqlite3.Row,
    row: sqlite3.Row,
    result: CommandResult,
    ) -> str:

    """
    Bind approval to owner, generation, revision and both exact outcomes.

    :param meta: Original owner metadata.
    :type meta: sqlite3.Row

    :param row: Exact record observed by the preview.
    :type row: sqlite3.Row

    :param result: Completed client evidence.
    :type result: CommandResult

    :return: SHA-256 preview identifier, not an authentication token.
    :rtype: str
    """

    # meta — служебные сведения о поколении и формате журнала.
    # row — прочитанная запись журнала для проверки её состояния.
    # result — сохранённый результат исходного исполнения.


    # last_now не входит в отпечаток; ревизия и generation защищают от устаревшей сверки.

    values = [meta["owner"], meta["generation"], meta["settings"], row["revision"],
              row["acknowledged"], row["running"], json.loads(row["request"]),
              json.loads(row["result"]), json.loads(encode_command(result))]
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Отдельная запись решения без замены UNKNOWN
#------------------------------------------------------------------------------------------------------------------
def _apply(
    db: sqlite3.Connection,
    meta: sqlite3.Row,
    row: sqlite3.Row,
    result: CommandResult,
    fingerprint: str,
    ) -> None:

    """
    Persist separate acceptance, release execution and fence old handles atomically.

    :param db: Exclusive administrative transaction.
    :type db: sqlite3.Connection

    :param meta: Original owner settings.
    :type meta: sqlite3.Row

    :param row: Original unknown command, preserved verbatim.
    :type row: sqlite3.Row

    :param result: Completed client result to accept for receipts only.
    :type result: CommandResult

    :param fingerprint: Exact approved preview.
    :type fingerprint: str
    """

    # db — открытое соединение с журналом; владение остаётся у вызывающего кода.
    # meta — служебные сведения о поколении и формате журнала.
    # row — прочитанная запись журнала для проверки её состояния.
    # result — сохранённый результат исходного исполнения.
    # fingerprint — отпечаток ранее проверенных данных для защиты от их подмены.


    # Дополнительный результат учитывается в логическом и физическом лимите БД.

    accepted = encode_command(result)
    settings = json.loads(meta["settings"])
    reserved = db.execute("SELECT COALESCE(SUM(reserved),0) FROM commands").fetchone()[0]
    if reserved + len(accepted) > settings["payload_bytes"]:
        raise ResolutionError("store_capacity")

    pages = settings["database_bytes"] // db.execute("PRAGMA page_size").fetchone()[0]
    if db.execute(f"PRAGMA max_page_count={pages}").fetchone()[0] > pages:
        raise ResolutionError("store_capacity")

    db.execute("CREATE TABLE IF NOT EXISTS resolutions (command_id TEXT PRIMARY KEY "
               "REFERENCES commands(command_id) ON DELETE CASCADE, accepted BLOB NOT NULL, "
               "fingerprint TEXT NOT NULL, decided_at TEXT NOT NULL)")
    db.execute("PRAGMA user_version=2")
    db.execute("INSERT INTO resolutions VALUES (?, ?, ?, ?)",
               (row["command_id"], accepted, fingerprint, datetime.now(timezone.utc).isoformat()))

    # UNKNOWN, его текст и ACK источника остаются прежними. Владелец отдельно
    # подтверждает окончание исполнения и принимает клиентский итог для квитанции.
    db.execute("UPDATE commands SET running=0, revision=revision+1, reserved=reserved+? WHERE command_id=?",
               (len(accepted), row["command_id"]))
    db.execute("INSERT INTO audit(code,command_id,phase) VALUES ('admin_resolved',?,'unknown')",
               (row["command_id"],))
    db.execute("DELETE FROM audit WHERE sequence NOT IN (SELECT sequence FROM audit "
               "ORDER BY sequence DESC LIMIT ?)", (settings["audit"],))

    # Новое служебное поколение ограждает старые открытые handle. Это дополнительная
    # защита, а не доказательство остановки callback: его подтверждает оператор.
    db.execute("UPDATE metadata SET generation=? WHERE id=1", (uuid4().hex,))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.commands.resolution не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
