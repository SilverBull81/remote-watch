# Атомарный журнал команд, позиций источников и результатов в SQLite.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260930-220144
#
# Состав модуля:
# -> SQLiteCommandStore: Журнал команд в локальной SQLite.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие и восстановление нового поколения журнала.
#    -> close(): Освобождение соединения SQLite.
#    -> cursor(): Чтение сохранённой позиции источника.
#    -> admit(): Атомарная запись решения и позиции источника.
#    -> get(): Поиск команды с проверкой всех полей цели.
#    -> transition(): Фиксация перехода с проверкой версии.
#    -> acknowledge(): Подтверждение точного сохранённого результата.
#    -> pending(): Ограниченная страница неподтверждённых или исполняемых команд.
#    -> release_execution(): Отметка о фактическом окончании ранее неизвестного исполнения.
#    -> audit(): Ограниченная страница аудита.
#    -> prune(): Очистка подтверждённых записей с сохранением защиты от повторов.
#    Служебные методы:
#    -> _transaction(): Ограниченная транзакция с проверкой поколения владельца.
#    -> _now(): Проверка монотонного времени.
#    -> _target(): Проверка локального поколения hub или приложения.
#    -> _row(): Получение строки с точной корреляцией.
#    -> _stored(): Проверка сохранённых данных перед возвратом.
#    -> _save(): Запись состояния и резерва результата.
#    -> _audit(): Добавление записи без аргументов и текста ответа.
#    -> _position(): Проверка целочисленной позиции потока.
#    -> _page(): Ограничение размера страницы.
#    -> _create(): Создание схемы в общей транзакции.
#


#******************************************************************************************************************
# ИМПОРТ И ОПРЕДЕЛЕНИЯ
#******************************************************************************************************************
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from time import monotonic

from ._validation import require_int, require_number, require_text
from .command_protocol import (
    MAX_COMMAND_BYTES,
    CommandClaim,
    CommandGrant,
    CommandRef,
    CommandRequest,
    CommandResult,
    CommandSession,
    _nonce,
    decode_command,
    encode_command,
)
from .command_state import (
    CommandAction,
    CommandDeadline,
    CommandPhase,
    CommandRecord,
    advance_command,
    recover_command,
)
from .command_storage import (
    Admission,
    AuditCode,
    AuditEntry,
    CommittedTransition,
    StoreConflict,
    StoredCommand,
    StoreError,
    StoreFull,
    StoreLimits,
    StoreRole,
)


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Журнал команд в локальной SQLite
#------------------------------------------------------------------------------------------------------------------
class SQLiteCommandStore:
    """Own a bounded local SQLite journal; one logical owner may use serialized handles."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        path: str | Path,
        *,
        owner_id: str,
        generation: str,
        role: StoreRole,
        limits: StoreLimits = StoreLimits(),
        clock: Callable[[], float] = monotonic,
    ) -> None:

        """Store configuration without opening files or starting background work.

        :param path: Local persistent database path.
        :type path: str | Path

        :param owner_id: Stable logical journal owner.
        :type owner_id: str

        :param generation: Fresh owner incarnation; never reuse after process restart.
        :type generation: str

        :param role: Hub or application-side journal role.
        :type role: StoreRole

        :param limits: Finite capacity, retention and lock settings.
        :type limits: StoreLimits

        :param clock: Local monotonic clock, injectable for deterministic tests.
        :type clock: Callable[[], float]
        """

        # path — путь локального файла журнала.
        # owner_id — постоянное имя владельца журнала.
        # generation — новый ID запуска владельца журнала.
        # role — журнал hub либо приложения.
        # limits — конечные пределы ёмкости и ожидания.
        # clock — локальный монотонный отсчёт времени.

        if not isinstance(path, (str, Path)) or str(path) in ("", ":memory:"):
            raise ValueError("a persistent database path is required")
        require_text(owner_id, "store owner", 128)
        _nonce(generation)
        if type(role) is not StoreRole or type(limits) is not StoreLimits or not callable(clock):
            raise TypeError("invalid storage configuration")
        self._path = Path(path)
        self._owner = owner_id
        self._generation = generation
        self._role = role
        self._limits = limits
        self._clock = clock
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._failed = False
        self._last: float | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие и восстановление нового поколения журнала
    #--------------------------------------------------------------------------------------------------------------
    def open(self) -> None:

        """Create or validate the schema and recover all old-generation work atomically."""

        if not self._lock.acquire(timeout=self._limits.lock_timeout):
            raise StoreError("store busy")
        connection = None
        try:
            if self._connection is not None:
                if self._failed:
                    raise StoreError("store unavailable")
                return
            if self._failed:
                raise StoreError("store unavailable")
            connection = sqlite3.connect(self._path, timeout=self._limits.lock_timeout,
                                         isolation_level=None, check_same_thread=False)
            # Соединение используется только под собственной блокировкой. Журнал
            # отката выбран вместо WAL, чтобы отставший читатель не раздувал WAL-файл.
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise StoreError("unsupported journal mode")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA cache_size=-2048")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            maximum = self._limits.database_bytes // page_size
            actual = connection.execute(f"PRAGMA max_page_count={maximum}").fetchone()[0]
            if actual > maximum:
                raise StoreFull("store capacity exceeded")

            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StoreError("unsupported store schema")
            if version == 0:
                if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone() is not None:
                    raise StoreError("unrecognized store schema")
                self._create(connection)
            meta = connection.execute("SELECT * FROM metadata WHERE id=1").fetchone()
            settings = json.dumps(asdict(self._limits), sort_keys=True)
            now = self._now()
            if meta is None:
                connection.execute("INSERT INTO metadata VALUES (1, ?, ?, ?, ?, ?)",
                                   (self._owner, self._role.value, self._generation, settings, now))
            else:
                if meta["owner"] != self._owner or meta["role"] != self._role.value:
                    raise StoreError("store configuration mismatch")
                if meta["settings"] != settings:
                    # После остановки владельца лимиты можно увеличить, чтобы выйти
                    # из переполнения. Соседний handle того же запуска менять их не может.
                    previous_limits = json.loads(meta["settings"])
                    if meta["generation"] == self._generation or any(
                        value < previous_limits[key] for key, value in asdict(self._limits).items() if key != "lock_timeout"
                    ):
                        raise StoreError("store configuration mismatch")
                    db_settings = json.dumps(asdict(self._limits), sort_keys=True)
                    connection.execute("UPDATE metadata SET settings=? WHERE id=1", (db_settings,))
                if meta["generation"] != self._generation:
                    # Старые локальные сроки не восстанавливаются. Даже запись STARTED
                    # без результата становится UNKNOWN, а не приглашением повторить callback.
                    rows = connection.execute("SELECT * FROM commands ORDER BY sequence").fetchall()
                    if len(rows) > self._limits.records:
                        raise StoreFull("store capacity exceeded")
                    for row in rows:
                        old = self._stored(row)
                        recovered = recover_command(old.record)
                        if recovered != old.record:
                            self._save(connection, row, recovered, now)
                            self._audit(connection, AuditCode.RECOVERED, recovered)
                    connection.execute("UPDATE commands SET retain_until=?", (now + self._limits.retention,))
                    connection.execute("UPDATE metadata SET generation=?, last_now=? WHERE id=1", (self._generation, now))
                elif now < meta["last_now"]:
                    raise StoreError("store clock rollback")
            connection.commit()
            self._connection = connection
        except (sqlite3.Error, OSError, ValueError, TypeError):
            self._failed = True
            raise StoreError("store unavailable") from None
        finally:
            if connection is not None and connection is not self._connection:
                connection.close()
            self._lock.release()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Освобождение соединения SQLite
    #--------------------------------------------------------------------------------------------------------------
    def close(self) -> None:

        """Close the connection; reopening requires a new store object and generation."""

        if not self._lock.acquire(timeout=self._limits.lock_timeout):
            raise StoreError("store busy")
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._failed = True
        finally:
            self._lock.release()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение сохранённой позиции источника
    #--------------------------------------------------------------------------------------------------------------
    def cursor(
        self,
        stream: str,
    ) -> int:

        """Return the persisted high-water mark for one ordered source.

        :param stream: Stable authenticated ordered-stream identifier.
        :type stream: str

        :return: Return the persisted high-water mark for one ordered source.
        :rtype: int
        """

        # stream — постоянное имя упорядоченного источника.

        require_text(stream, "stream", 128)
        with self._transaction() as (db, _):
            row = db.execute("SELECT position FROM streams WHERE name=?", (stream,)).fetchone()
            return -1 if row is None else row[0]
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Атомарная запись решения и позиции источника
    #--------------------------------------------------------------------------------------------------------------
    def admit(
        self,
        stream: str,
        position: int,
        expected_cursor: int,
        request: CommandRequest | None,
        deadline: CommandDeadline | None = None,
        rejection: AuditCode = AuditCode.IGNORED,
    ) -> Admission:

        """Persist a source decision and its cursor together before acknowledging the source.

        :param stream: Stable authenticated ordered-stream identifier.
        :type stream: str

        :param position: Monotonically increasing delivery position, stable on retry.
        :type position: int

        :param expected_cursor: Previously observed committed cursor for comparison.
        :type expected_cursor: int

        :param request: Validated command intent, or None for a rejected source event.
        :type request: CommandRequest | None

        :param deadline: Original remaining lifetime on this process monotonic clock.
        :type deadline: CommandDeadline | None

        :param rejection: Fixed audit code for a source decision without a command.
        :type rejection: AuditCode

        :return: Persist a source decision and its cursor together before acknowledging the source.
        :rtype: Admission
        """

        # stream — постоянное имя упорядоченного источника.
        # position — возрастающая позиция, неизменная при повторе.
        # expected_cursor — ожидаемая предыдущая позиция источника.
        # request — команда либо отсутствие принятой команды.
        # deadline — исходный оставшийся срок на локальных часах.
        # rejection — фиксированная причина пропуска или отказа.

        require_text(stream, "stream", 128)
        self._position(position)
        if type(expected_cursor) is not int or not -1 <= expected_cursor <= 2**63 - 1:
            raise ValueError("invalid expected cursor")
        if request is not None and type(request) is not CommandRequest:
            raise TypeError("invalid command request")
        if request is None and (type(rejection) is not AuditCode or
                               rejection not in (AuditCode.IGNORED, AuditCode.DENIED, AuditCode.STALE)):
            raise ValueError("invalid source decision")

        with self._transaction() as (db, now):
            row = db.execute("SELECT position FROM streams WHERE name=?", (stream,)).fetchone()
            current = -1 if row is None else row[0]
            # После очистки payload позиция остаётся навсегда. Старый update или
            # повтор доставки клиенту уже не сможет создать новую команду.
            if position <= current:
                previous = db.execute("SELECT * FROM commands WHERE stream=? AND position=?", (stream, position)).fetchone()
                if previous is not None and request is not None and self._stored(previous).record.request != request:
                    raise StoreConflict("source event conflict")
                return Admission(cursor=current, command=None if previous is None else self._stored(previous), inserted=False)
            if current != expected_cursor:
                raise StoreConflict("source cursor conflict")
            if row is None and db.execute("SELECT count(*) FROM streams").fetchone()[0] >= self._limits.streams:
                raise StoreFull("store capacity exceeded")

            stored = None
            if request is not None:
                self._target(request.ref)
                if type(deadline) is not CommandDeadline or deadline.remaining(now, request.ref.hub_epoch) <= 0:
                    raise StoreConflict("command deadline expired")
                body = encode_command(request)
                # Резерв результата учитывается заранее: заполненная очередь новых
                # запросов не должна съедать логическую ёмкость для их завершения.
                reserved = len(body) + MAX_COMMAND_BYTES
                count, size = db.execute("SELECT count(*), coalesce(sum(reserved), 0) FROM commands").fetchone()
                if count >= self._limits.records or size + reserved > self._limits.payload_bytes:
                    raise StoreFull("store capacity exceeded")
                if db.execute("SELECT 1 FROM commands WHERE command_id=? OR (source=? AND source_event=?)",
                              (request.ref.command_id, request.source_id, request.source_event_id)).fetchone() is not None:
                    raise StoreConflict("command identity conflict")
                db.execute("INSERT INTO commands (command_id, stream, position, source, source_event, generation, "
                           "request, phase, revision, acknowledged, reserved, sent, received, expires, retain_until) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', 0, 0, ?, ?, ?, ?, ?)",
                           (request.ref.command_id, stream, position, request.source_id, request.source_event_id,
                            self._generation, body, reserved, deadline.sent_at, deadline.received_at, deadline.expires_at,
                            now + self._limits.retention))
                created = self._row(db, request.ref)
                stored = self._stored(created)
                self._audit(db, AuditCode.ACCEPTED, stored.record)
            else:
                self._audit(db, rejection, None)
            db.execute("INSERT INTO streams VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET position=excluded.position",
                       (stream, position))
            return Admission(cursor=position, command=stored, inserted=stored is not None)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Поиск команды с проверкой всех полей цели
    #--------------------------------------------------------------------------------------------------------------
    def get(
        self,
        ref: CommandRef,
    ) -> StoredCommand | None:

        """Read only an exact reference, including old terminal results.

        :param ref: Exact command, identity, session and hub reference.
        :type ref: CommandRef

        :return: Read only an exact reference, including old terminal results.
        :rtype: StoredCommand | None
        """

        # ref — полная ссылка на команду и её цель.

        with self._transaction() as (db, _):
            row = self._row(db, ref, required=False)
            return None if row is None else self._stored(row)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Фиксация перехода с проверкой версии
    #--------------------------------------------------------------------------------------------------------------
    def transition(
        self,
        ref: CommandRef,
        revision: int,
        action: CommandAction,
        session: CommandSession,
        claim: CommandClaim,
        session_deadline: CommandDeadline,
        grant: CommandGrant | None = None,
        result: CommandResult | None = None,
        grant_deadline: CommandDeadline | None = None,
    ) -> CommittedTransition:

        """Apply a state transition under revision comparison and return only after commit.

        :param ref: Exact command, identity, session and hub reference.
        :type ref: CommandRef

        :param revision: Expected persisted command revision.
        :type revision: int

        :param action: Requested state-machine action.
        :type action: CommandAction

        :param session: Authenticated application session metadata.
        :type session: CommandSession

        :param claim: Claim bound to the exact command payload.
        :type claim: CommandClaim

        :param session_deadline: Independent local registration deadline.
        :type session_deadline: CommandDeadline

        :param grant: Correlated authorization for the only execution attempt.
        :type grant: CommandGrant | None

        :param result: Exact terminal callback result.
        :type result: CommandResult | None

        :param grant_deadline: Local grant deadline after subtracting the entire round trip.
        :type grant_deadline: CommandDeadline | None

        :return: Apply a state transition under revision comparison and return only after commit.
        :rtype: CommittedTransition
        """

        # ref — полная ссылка на команду и её цель.
        # revision — ожидаемый номер версии сохранённой команды.
        # action — запрошенный переход состояния.
        # session — проверенные сведения о сессии приложения.
        # claim — попытка, связанная с полным запросом.
        # session_deadline — отдельный срок действия регистрации.
        # grant — разрешение на единственную попытку.
        # result — точный итог обработчика.
        # grant_deadline — срок разрешения после вычета времени запроса.

        require_int(revision, "revision", minimum=0)
        with self._transaction() as (db, now):
            row = self._row(db, ref)
            stored = self._stored(row)
            if stored.revision != revision:
                raise StoreConflict("command revision conflict")
            if row["generation"] != self._generation:
                raise StoreConflict("command generation expired")
            deadline = CommandDeadline(hub_epoch=ref.hub_epoch, sent_at=row["sent"],
                                       received_at=row["received"], expires_at=row["expires"])
            # Срок START дополнительно сужается timeout разрешения, но никогда
            # не создаётся заново при повторном запросе. База хранит исходный предел.
            if action is CommandAction.START and stored.record.phase is not CommandPhase.STARTED:
                if (type(grant) is not CommandGrant or type(grant_deadline) is not CommandDeadline
                        or grant_deadline.hub_epoch != ref.hub_epoch
                        or grant_deadline.expires_at - grant_deadline.sent_at > grant.execution_timeout):
                    raise StoreConflict("invalid grant deadline")
                if deadline.expires_at > grant_deadline.sent_at:
                    deadline = CommandDeadline(hub_epoch=ref.hub_epoch, sent_at=grant_deadline.sent_at,
                                               received_at=grant_deadline.received_at,
                                               expires_at=min(deadline.expires_at, grant_deadline.expires_at))
            proposal = advance_command(stored.record, action, session, claim, deadline, session_deadline,
                                       now, grant, result)
            if proposal.record != stored.record:
                self._save(db, row, proposal.record, now)
                if proposal.start_callback:
                    db.execute("UPDATE commands SET sent=?, received=?, expires=? WHERE sequence=?",
                               (deadline.sent_at, deadline.received_at, deadline.expires_at, row["sequence"]))
                self._audit(db, AuditCode.TRANSITION, proposal.record)
                stored = self._stored(self._row(db, ref))
            answer = CommittedTransition(command=stored, start_callback=proposal.start_callback)
        # Выход из контекста включает commit. При ошибке commit этот return не достижим.
        if answer.start_callback:
            after_commit = self._now()
            if min(deadline.remaining(after_commit, ref.hub_epoch),
                   session_deadline.remaining(after_commit, ref.hub_epoch)) <= 0:
                return CommittedTransition(command=answer.command, start_callback=False)
        return answer
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение точного сохранённого результата
    #--------------------------------------------------------------------------------------------------------------
    def acknowledge(
        self,
        result: CommandResult,
    ) -> None:

        """Acknowledge only the exact persisted terminal result.

        :param result: Exact terminal callback result.
        :type result: CommandResult
        """

        # result — точный итог обработчика.

        if type(result) is not CommandResult:
            raise TypeError("invalid command result")
        with self._transaction() as (db, now):
            row = self._row(db, result.ref)
            stored = self._stored(row)
            if stored.record.result != result:
                raise StoreConflict("command result conflict")
            if not stored.acknowledged:
                db.execute("UPDATE commands SET acknowledged=1, revision=revision+1, retain_until=? WHERE sequence=?",
                           (now + self._limits.retention, row["sequence"]))
                self._audit(db, AuditCode.ACKNOWLEDGED, stored.record)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ограниченная страница неподтверждённых или исполняемых команд
    #--------------------------------------------------------------------------------------------------------------
    def pending(
        self,
        after: int = 0,
        limit: int = 100,
    ) -> tuple[StoredCommand, ...]:

        """Page through unacknowledged records without loading the full journal.

        :param after: Exclusive sequence lower bound for pagination.
        :type after: int

        :param limit: Maximum records in one bounded page or cleanup batch.
        :type limit: int

        :return: Page through unacknowledged records without loading the full journal.
        :rtype: tuple[StoredCommand, ...]
        """

        # after — последний уже прочитанный номер.
        # limit — предел одной страницы или порции очистки.

        self._page(after, limit)
        with self._transaction() as (db, _):
            return tuple(self._stored(row) for row in db.execute(
                "SELECT * FROM commands WHERE sequence>? AND (acknowledged=0 OR running=1) ORDER BY sequence LIMIT ?",
                (after, limit)))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Отметка о фактическом окончании ранее неизвестного исполнения
    #--------------------------------------------------------------------------------------------------------------
    def release_execution(
        self,
        ref: CommandRef,
        claim_id: str,
    ) -> None:

        """Release an UNKNOWN execution only after the caller verifies it can no longer run.

        :param ref: Exact command, identity, session and hub reference.
        :type ref: CommandRef

        :param claim_id: Identifier of the original execution attempt.
        :type claim_id: str
        """

        # ref — полная ссылка на команду и её цель.
        # claim_id — ID исходной попытки исполнения.

        _nonce(claim_id)
        with self._transaction() as (db, now):
            row = self._row(db, ref)
            stored = self._stored(row)
            if stored.record.claim_id != claim_id or stored.record.result is None:
                raise StoreConflict("execution is not terminal")
            if stored.execution_active:
                # ACK результата UNKNOWN не доказывает остановку потока. Это отдельный
                # сигнал executor/сверки с приложением, а не результат истечения TTL.
                db.execute("UPDATE commands SET running=0, revision=revision+1, retain_until=? WHERE sequence=?",
                           (now + self._limits.retention, row["sequence"]))
                self._audit(db, AuditCode.EXECUTION_RELEASED, stored.record)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ограниченная страница аудита
    #--------------------------------------------------------------------------------------------------------------
    def audit(
        self,
        after: int = 0,
        limit: int = 100,
    ) -> tuple[AuditEntry, ...]:

        """Read a bounded page without command text or actor details.

        :param after: Exclusive sequence lower bound for pagination.
        :type after: int

        :param limit: Maximum records in one bounded page or cleanup batch.
        :type limit: int

        :return: Read a bounded page without command text or actor details.
        :rtype: tuple[AuditEntry, ...]
        """

        # after — последний уже прочитанный номер.
        # limit — предел одной страницы или порции очистки.

        self._page(after, limit)
        with self._transaction() as (db, _):
            return tuple(AuditEntry(sequence=row[0], code=AuditCode(row[1]), command_id=row[2], phase=row[3])
                         for row in db.execute("SELECT * FROM audit WHERE sequence>? ORDER BY sequence LIMIT ?",
                                               (after, limit)))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Очистка подтверждённых записей с сохранением защиты от повторов
    #--------------------------------------------------------------------------------------------------------------
    def prune(
        self,
        limit: int = 100,
    ) -> int:

        """Delete acknowledged terminal payloads after retention, keeping stream high-water marks.

        :param limit: Maximum records in one bounded page or cleanup batch.
        :type limit: int

        :return: Delete acknowledged terminal payloads after retention, keeping stream high-water marks.
        :rtype: int
        """

        # limit — предел одной страницы или порции очистки.

        self._page(0, limit)
        with self._transaction() as (db, now):
            # Активное исполнение и результат без ACK остаются независимо от возраста.
            # Удаляется только payload; постоянная позиция потока защищает от повтора.
            result = db.execute("DELETE FROM commands WHERE sequence IN (SELECT sequence FROM commands "
                                "WHERE acknowledged=1 AND running=0 AND result IS NOT NULL AND retain_until<=? "
                                "ORDER BY sequence LIMIT ?)", (now, limit))
            return result.rowcount
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Ограниченная транзакция с проверкой поколения владельца
    #--------------------------------------------------------------------------------------------------------------
    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, float]]:

        """Serialize bounded operations and fence handles from an earlier owner generation.

        :return: Serialize bounded operations and fence handles from an earlier owner generation.
        :rtype: Iterator[tuple[sqlite3.Connection, float]]
        """

        if not self._lock.acquire(timeout=self._limits.lock_timeout):
            raise StoreError("store busy")
        db = self._connection
        try:
            if db is None or self._failed:
                raise StoreError("store unavailable")
            db.execute("BEGIN IMMEDIATE")
            meta = db.execute("SELECT generation, last_now FROM metadata WHERE id=1").fetchone()
            if meta is None or meta[0] != self._generation:
                raise StoreConflict("store generation expired")
            now = self._now()
            if now < meta[1]:
                self._failed = True
                raise StoreError("store clock rollback")
            yield db, now
            db.execute("UPDATE metadata SET last_now=? WHERE id=1", (now,))
            db.commit()
        except sqlite3.Error as error:
            # Занятая БД допускает повтор операции после отката: START не был
            # подтверждён. Для Python 3.10 предусмотрены точные стандартные тексты.
            code = getattr(error, "sqlite_errorcode", None)
            if code in (5, 6) or (code is None and error.args in (("database is locked",), ("database table is locked",))):
                raise StoreError("store busy") from None
            self._failed = True
            raise StoreError("store unavailable") from None
        finally:
            if db is not None and db.in_transaction:
                try:
                    db.rollback()
                except sqlite3.Error:
                    self._failed = True
            self._lock.release()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка монотонного времени
    #--------------------------------------------------------------------------------------------------------------
    def _now(self) -> float:

        """Detect local clock rollback before touching retention or authorization.

        :return: Detect local clock rollback before touching retention or authorization.
        :rtype: float
        """

        now = self._clock()
        require_number(now, "monotonic time", allow_zero=True)
        if self._last is not None and now < self._last:
            self._failed = True
            raise StoreError("store clock rollback")
        self._last = now
        return now
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка локального поколения hub или приложения
    #--------------------------------------------------------------------------------------------------------------
    def _target(
        self,
        ref: CommandRef,
    ) -> None:

        """Require the locally owned hub or application generation.

        :param ref: Exact command, identity, session and hub reference.
        :type ref: CommandRef
        """

        # ref — полная ссылка на команду и её цель.

        actual = ref.hub_epoch if self._role is StoreRole.HUB else ref.session_id
        if actual != self._generation:
            raise StoreConflict("command generation expired")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Получение строки с точной корреляцией
    #--------------------------------------------------------------------------------------------------------------
    def _row(
        self,
        db: sqlite3.Connection,
        ref: CommandRef,
        required: bool = True,
    ) -> sqlite3.Row | None:

        """Fetch and verify full command correlation instead of trusting its ID alone.

        :param db: Connection owned by the active transaction.
        :type db: sqlite3.Connection

        :param ref: Exact command, identity, session and hub reference.
        :type ref: CommandRef

        :param required: Whether absence is a conflict.
        :type required: bool

        :return: Fetch and verify full command correlation instead of trusting its ID alone.
        :rtype: sqlite3.Row | None
        """

        # db — соединение текущей транзакции.
        # ref — полная ссылка на команду и её цель.
        # required — считать ли отсутствие команды конфликтом.

        if type(ref) is not CommandRef:
            raise TypeError("invalid command reference")
        row = db.execute("SELECT * FROM commands WHERE command_id=?", (ref.command_id,)).fetchone()
        if row is not None and self._stored(row).record.request.ref != ref:
            raise StoreConflict("command reference conflict")
        if row is None and required:
            raise StoreConflict("command not found")
        return row
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка сохранённых данных перед возвратом
    #--------------------------------------------------------------------------------------------------------------
    def _stored(
        self,
        row: sqlite3.Row,
    ) -> StoredCommand:

        """Validate bounded persisted wire before returning a journal record.

        :param row: Persisted SQLite row.
        :type row: sqlite3.Row

        :return: Validate bounded persisted wire before returning a journal record.
        :rtype: StoredCommand
        """

        # row — строка журнала SQLite.

        try:
            request = decode_command(row["request"])
            result = None if row["result"] is None else decode_command(row["result"])
            record = CommandRecord(request=request, phase=CommandPhase(row["phase"]), claim_id=row["claim_id"], result=result)
            return StoredCommand(record=record, revision=row["revision"], sequence=row["sequence"],
                                 acknowledged=bool(row["acknowledged"]), execution_active=bool(row["running"]))
        except (ValueError, TypeError):
            self._failed = True
            raise StoreError("invalid stored command") from None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Запись состояния и резерва результата
    #--------------------------------------------------------------------------------------------------------------
    def _save(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
        record: CommandRecord,
        now: float,
    ) -> None:

        """Persist progress while keeping the reserved result capacity.

        :param db: Connection owned by the active transaction.
        :type db: sqlite3.Connection

        :param row: Persisted SQLite row.
        :type row: sqlite3.Row

        :param record: Validated command state to persist.
        :type record: CommandRecord

        :param now: Current local monotonic observation.
        :type now: float
        """

        # db — соединение текущей транзакции.
        # row — строка журнала SQLite.
        # record — проверенное состояние для записи.
        # now — текущий локальный монотонный момент.

        body = None if record.result is None else encode_command(record.result)
        running = row["running"] or record.phase is CommandPhase.STARTED
        if record.result is not None and (record.phase is CommandPhase.COMPLETED
                or record.result.reason.value in ("callback_error", "invalid_result")):
            running = False
        db.execute("UPDATE commands SET phase=?, claim_id=?, result=?, running=?, revision=revision+1, retain_until=? "
                   "WHERE sequence=?", (record.phase.value, record.claim_id, body,
                                         int(running), now + self._limits.retention, row["sequence"]))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Добавление записи без аргументов и текста ответа
    #--------------------------------------------------------------------------------------------------------------
    def _audit(
        self,
        db: sqlite3.Connection,
        code: AuditCode,
        record: CommandRecord | None,
    ) -> None:

        """Append safe metadata and trim only audit, never command replay guards.

        :param db: Connection owned by the active transaction.
        :type db: sqlite3.Connection

        :param code: Fixed safe audit operation code.
        :type code: AuditCode

        :param record: Validated command state to persist.
        :type record: CommandRecord | None
        """

        # db — соединение текущей транзакции.
        # code — фиксированное действие аудита.
        # record — проверенное состояние для записи.

        db.execute("INSERT INTO audit (code, command_id, phase) VALUES (?, ?, ?)",
                   (code.value, None if record is None else record.request.ref.command_id,
                    None if record is None else record.phase.value))
        db.execute("DELETE FROM audit WHERE sequence NOT IN (SELECT sequence FROM audit ORDER BY sequence DESC LIMIT ?)",
                   (self._limits.audit,))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка целочисленной позиции потока
    #--------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _position(value: int) -> None:

        """Validate a source sequence supported by SQLite integer storage.

        :param value: Candidate value checked against the contract.
        :type value: int
        """

        # value — проверяемое значение.

        require_int(value, "source position", minimum=0)
        if value > 2**63 - 1:
            raise ValueError("source position out of range")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Ограничение размера страницы
    #--------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _page(
        after: int,
        limit: int,
    ) -> None:

        """Bound every page independently of the total storage capacity.

        :param after: Exclusive sequence lower bound for pagination.
        :type after: int

        :param limit: Maximum records in one bounded page or cleanup batch.
        :type limit: int
        """

        # after — последний уже прочитанный номер.
        # limit — предел одной страницы или порции очистки.

        SQLiteCommandStore._position(after)
        require_int(limit, "page size")
        if limit > 1000:
            raise ValueError("page size exceeds limit")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Создание схемы в общей транзакции
    #--------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _create(db: sqlite3.Connection) -> None:

        """Create schema using individual statements inside the caller's transaction.

        :param db: Connection owned by the active transaction.
        :type db: sqlite3.Connection
        """

        # db — соединение текущей транзакции.

        # executescript делает неявный commit; здесь все DDL должны остаться
        # внутри того же BEGIN, что и создание владельца/поколения журнала.
        statements = (
            "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, role TEXT NOT NULL, "
            "generation TEXT NOT NULL, settings TEXT NOT NULL, last_now REAL NOT NULL)",
            "CREATE TABLE streams (name TEXT PRIMARY KEY, position INTEGER NOT NULL)",
            "CREATE TABLE commands (sequence INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT UNIQUE NOT NULL, "
            "stream TEXT NOT NULL, position INTEGER NOT NULL, source TEXT NOT NULL, source_event TEXT NOT NULL, "
            "generation TEXT NOT NULL, request BLOB NOT NULL, phase TEXT NOT NULL, claim_id TEXT, result BLOB, "
            "revision INTEGER NOT NULL, acknowledged INTEGER NOT NULL, running INTEGER NOT NULL DEFAULT 0, reserved INTEGER NOT NULL, "
            "sent REAL NOT NULL, received REAL NOT NULL, expires REAL NOT NULL, retain_until REAL NOT NULL, "
            "UNIQUE(stream, position), UNIQUE(source, source_event))",
            "CREATE TABLE audit (sequence INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, command_id TEXT, phase TEXT)",
            "PRAGMA user_version=1",
        )
        for statement in statements:
            db.execute(statement)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.sqlite_command_store не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
