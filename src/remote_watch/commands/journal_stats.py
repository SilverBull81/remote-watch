# Ограниченные сводки командных журналов без чтения запросов и восстановления владельца.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-111029
#
# Классы:
# -> JournalStats: Числа занятости и состояний одного журнала.
#
# Функции:
# -> read_stats(): Согласованное чтение агрегатов в пределах SQL-бюджета.
# -> inspect_journal(): Чтение остановленной локальной SQLite без recovery и файлов блокировок.
# -> _commands(): Сводка hub/client с лимитами из metadata.
# -> _source(): Сводка защиты от повторов и одного решения источника.
# -> validate_status(): Строгая проверка безопасного сообщения сводки.
# -> validate_stats(): Проверка полей числового снимка.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Числа занятости и состояний одного журнала
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class JournalStats:
    """Expose bounded aggregates without owners, identities, cursors or command payloads."""

    kind: str                          # hub/client/source; только известное назначение БД.
    schema_version: int                # Версия файла, без миграции при чтении.
    records: int                       # Сохранённые команды либо replay-ID источника.
    records_limit: int | None           # Предел числа записей; None, если он не сохранён в файле.
    pending: int                       # Без ACK или с неосвобождённым исполнением; у source — 0/1.
    unknown: int | None                 # Команды UNKNOWN; у source такого состояния нет.
    unresolved_unknown: int | None      # UNKNOWN без отдельного административного решения.
    execution_active: int | None        # Окончание исполнения не подтверждено, не число живых потоков.
    streams: int                       # Постоянные потоки; для source один state.
    streams_limit: int | None           # Предел потоков, не сбрасываемых при очистке payload.
    audit_entries: int                  # Число сохраняемых кратких операций аудита.
    audit_limit: int | None             # Предел аудита; сам аудит ротируется, не блокирует приём.
    payload_bytes: int                 # Размер запросов/результатов либо данных source, без содержимого.
    payload_reserved_bytes: int | None  # Резерв команд под будущий результат; для source не применим.
    payload_limit_bytes: int | None     # Логический предел с учётом резерва, если известен.
    database_bytes: int                # Размер выделенных SQLite-страниц, включая свободные.
    database_limit_bytes: int | None    # Настроенный предел основного файла; не объём диска ОС.
    free_database_bytes: int           # Страницы, которые SQLite может переиспользовать.
    resolutions: int                   # Число отдельных административных решений.
    capacity: str                      # ok/near_limit/at_limit/unknown; это оценка, не обещание приёма.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Согласованное чтение агрегатов в пределах SQL-бюджета
#------------------------------------------------------------------------------------------------------------------
def read_stats(
    db: sqlite3.Connection,
    *,
    source_limit: int | None = None,
    owner_generation: str | None = None,
) -> JournalStats:

    """Read bounded rows in one deferred transaction without changing journal state.

    :param db: Serialized owner connection or immutable offline connection.
    :type db: sqlite3.Connection
    :param source_limit: Owner's runtime replay limit; None when not stored in the file.
    :type source_limit: int | None
    :param owner_generation: Optional owner fence, checked inside the same read transaction.
    :type owner_generation: str | None
    :return: Immutable aggregate with no payload values.
    :rtype: JournalStats
    """

    # db/source_limit — запросы используют только числа, длины и постоянные имена таблиц.
    # Наблюдение не должно держать storage lane дольше 50 мс SQL-работы и не ждёт writer.
    if db.in_transaction:
        raise ValueError("journal transaction active")
    previous_wait = db.execute("PRAGMA busy_timeout").fetchone()[0]
    deadline = monotonic() + 0.05
    db.execute("PRAGMA busy_timeout=0")
    db.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
    try:
        db.execute("BEGIN")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 16")}
        if {"metadata", "commands", "streams", "audit"} <= tables and version in (1, 2):
            if owner_generation is not None:
                meta = db.execute("SELECT generation FROM metadata WHERE id=1").fetchone()
                if meta is None or meta[0] != owner_generation:
                    raise ValueError("journal generation expired")
            value = _commands(db, version)
        elif {"state", "seen"} <= tables and version == 1:
            value = _source(db, source_limit)
        else:
            raise ValueError("unsupported journal")
        validate_stats(asdict(value))
        return value
    finally:
        # После прерванного SELECT снимаем progress handler до rollback, иначе
        # сам откат тоже может прерваться. Таймаут соседних операций восстанавливается.
        db.set_progress_handler(None, 0)
        if db.in_transaction:
            db.rollback()
        db.execute(f"PRAGMA busy_timeout={int(previous_wait)}")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение остановленной локальной SQLite без recovery и файлов блокировок
#------------------------------------------------------------------------------------------------------------------
def inspect_journal(path: Path) -> JournalStats:

    """Inspect an explicitly offline journal without creating files or opening it as an owner.

    :param path: Existing stopped local journal, not a network share or live WAL database.
    :type path: Path
    :return: Bounded immutable aggregates; unknown source limits remain None.
    :rtype: JournalStats
    """

    # path — immutable запрещает SQLite создавать sidecar и запускать rollback recovery.
    # При незавершённой транзакции нельзя выдавать основной файл за полное состояние.
    resolved = path.resolve()
    if str(resolved).startswith(("\\\\", "//")):
        raise ValueError("local journal required")
    if any(Path(str(resolved) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
        raise ValueError("journal sidecar present")
    db = sqlite3.connect(resolved.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0)
    try:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise ValueError("unsupported journal mode")
        return read_stats(db)
    finally:
        db.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Сводка hub/client с лимитами из metadata
#------------------------------------------------------------------------------------------------------------------
def _commands(
    db: sqlite3.Connection,
    version: int,
) -> JournalStats:

    """Aggregate at most 100001 command rows and reject an over-bound journal.

    :param db: Active read transaction.
    :type db: sqlite3.Connection
    :param version: Validated command storage version.
    :type version: int
    :return: Counts and persisted capacity settings.
    :rtype: JournalStats
    """

    # metadata.settings — только проверенные числовые пределы; owner/generation не извлекаются.
    meta = db.execute("SELECT role, settings FROM metadata WHERE id=1").fetchone()
    if meta is None or meta[0] not in ("hub", "client") or len(meta[1]) > 4096:
        raise ValueError("invalid metadata")
    limits = json.loads(meta[1])
    for key in ("records", "streams", "audit", "payload_bytes", "database_bytes"):
        if type(limits.get(key)) is not int or not 1 <= limits[key] <= 2**31:
            raise ValueError("invalid limits")
    row = db.execute("SELECT COUNT(*), COALESCE(SUM(acknowledged=0 OR running=1),0), "
        "COALESCE(SUM(phase='unknown'),0), COALESCE(SUM(running),0), COALESCE(SUM(reserved),0), "
        "COALESCE(SUM(length(request)+COALESCE(length(result),0)),0) FROM "
        "(SELECT acknowledged,running,phase,reserved,request,result FROM commands LIMIT 100001)").fetchone()
    if row[0] > 100000:
        raise ValueError("journal row bound")
    streams = db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM streams LIMIT 1025)").fetchone()[0]
    audit = db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM audit LIMIT 100001)").fetchone()[0]
    resolutions, extra = ((0, 0) if version == 1 else db.execute(
        "SELECT COUNT(*),COALESCE(SUM(length(accepted)),0) FROM "
        "(SELECT accepted FROM resolutions LIMIT 100001)").fetchone())
    if streams > 1024 or audit > 100000 or resolutions > row[0]:
        raise ValueError("journal aggregate bound")
    resolved_unknown = (0 if version == 1 else db.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM commands c JOIN resolutions r "
        "ON c.command_id=r.command_id WHERE c.phase='unknown' LIMIT 100001)").fetchone()[0])
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    size = page_size * db.execute("PRAGMA page_count").fetchone()[0]
    free = page_size * db.execute("PRAGMA freelist_count").fetchone()[0]
    ratios = (row[0] / limits["records"], streams / limits["streams"],
              row[4] / limits["payload_bytes"], (size - free) / limits["database_bytes"])
    capacity = "at_limit" if max(ratios) >= 1 else "near_limit" if max(ratios) >= 0.8 else "ok"
    return JournalStats(meta[0], version, row[0], limits["records"], row[1], row[2],
                        row[2] - resolved_unknown, row[3], streams,
                        limits["streams"], audit, limits["audit"], row[5] + extra, row[4],
                        limits["payload_bytes"], size, limits["database_bytes"], free, resolutions, capacity)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Сводка защиты от повторов и одного решения источника
#------------------------------------------------------------------------------------------------------------------
def _source(
    db: sqlite3.Connection,
    limit: int | None,
) -> JournalStats:

    """Count replay keys and pending presence without decoding provider cursor or payload.

    :param db: Active source read transaction.
    :type db: sqlite3.Connection
    :param limit: Configured replay bound known only to a running owner.
    :type limit: int | None
    :return: Source aggregate with explicit unknown offline limits.
    :rtype: JournalStats
    """

    # seen содержит replay-ID; наружу выходит только число и общий объём строк.
    seen, payload = db.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(event AS BLOB))),0) "
                              "FROM (SELECT event FROM seen LIMIT 10001)").fetchone()
    if seen > 10000:
        raise ValueError("source row bound")
    state = db.execute("SELECT pending IS NOT NULL, COALESCE(length(CAST(pending AS BLOB)),0) "
                       "+length(CAST(cursor AS BLOB)) FROM state WHERE id=1").fetchone()
    if state is None or type(state[1]) is not int:
        raise ValueError("source state missing")
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    size = page_size * db.execute("PRAGMA page_count").fetchone()[0]
    free = page_size * db.execute("PRAGMA freelist_count").fetchone()[0]
    ratio = None if limit is None else max(seen / limit, (size - free) / (page_size * 2048))
    capacity = ("unknown" if ratio is None else "at_limit" if ratio >= 1 else
                "near_limit" if ratio >= 0.8 else "ok")
    return JournalStats("source", 1, seen, limit, state[0], None, None, None, 1, None, 0, None,
                        payload + state[1], None, None, size,
                        page_size * 2048 if limit is not None else None, free, 0, capacity)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Строгая проверка безопасного сообщения сводки
#------------------------------------------------------------------------------------------------------------------
def validate_status(value: object) -> dict:

    """Validate a cached journal observation crossing local IPC boundaries.

    :param value: Untrusted decoded status fragment.
    :type value: object
    :return: Independent sanitized status, never a raw database value.
    :rtype: dict
    """

    # value — никакие произвольные имена, ошибки или дополнительные поля не переносятся.
    if type(value) is not dict or set(value) != {"state", "age_seconds", "snapshot"}:
        raise ValueError("invalid journal status")
    if type(value["state"]) is not str or value["state"] not in (
            "available", "unavailable", "unsupported", "not_observed", "closed"):
        raise ValueError("invalid journal state")
    age = value["age_seconds"]
    if age is not None and (type(age) is not int or not 0 <= age <= 2**31 - 1):
        raise ValueError("invalid journal age")
    snapshot = None if value["snapshot"] is None else validate_stats(value["snapshot"])
    if (snapshot is None) != (age is None) or value["state"] == "available" and snapshot is None:
        raise ValueError("invalid journal observation")
    return {"state": value["state"], "age_seconds": age, "snapshot": snapshot}
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка полей числового снимка
#------------------------------------------------------------------------------------------------------------------
def validate_stats(value: object) -> dict:

    """Reject arbitrary fields and unbounded numbers in a journal aggregate.

    :param value: Proposed aggregate from a local owner or file.
    :type value: object
    :return: Independent whitelisted values.
    :rtype: dict
    """

    # value — метки роли/ёмкости фиксированы; остальные значения числовые или None.
    if type(value) is not dict or set(value) != set(JournalStats.__dataclass_fields__):
        raise ValueError("invalid journal snapshot")
    if type(value["kind"]) is not str or value["kind"] not in ("hub", "client", "source"):
        raise ValueError("invalid journal kind")
    if type(value["capacity"]) is not str or value["capacity"] not in (
            "ok", "near_limit", "at_limit", "unknown"):
        raise ValueError("invalid journal capacity")
    optional = {"records_limit", "unknown", "unresolved_unknown", "execution_active", "streams_limit",
                "audit_limit", "payload_reserved_bytes", "payload_limit_bytes", "database_limit_bytes"}
    for key, number in value.items():
        if key in ("kind", "capacity") or key in optional and number is None:
            continue
        if type(number) is not int or not 0 <= number <= 2**63 - 1:
            raise ValueError("invalid journal counter")
    if value["schema_version"] not in (1, 2) or value["pending"] > value["records"] + 1:
        raise ValueError("invalid journal counts")
    return dict(value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.commands.journal_stats не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
