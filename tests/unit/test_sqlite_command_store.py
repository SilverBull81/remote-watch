# Проверки атомарности, аварийного восстановления и пределов журнала SQLite.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260930-220144
#
# Состав модуля:
# -> request(): Подготовка подставной команды.
#
# -> options(): Согласованные данные разрешения и сессии.
#
# -> opened(): Открытие временного журнала с управляемыми часами.
#
# -> admit(): Атомарная запись решения и позиции источника.
#
# -> test_atomic_start_and_result_replay(): Единственный победитель конкурентного старта и повтор результата.
#
# -> test_restart_fences_old_handles_and_work(): Запрет продолжения старого поколения журнала.
#
# -> test_retention_preserves_source_guards(): Сохранение защиты источника после очистки результата.
#
# -> test_delayed_grant_cannot_renew_original_lifetime(): Сужение исходного срока при позднем разрешении.
#
# -> test_failed_admission_keeps_cursor(): Отсутствие подтверждения источнику при отказе записи.
#
# -> test_ignored_events_and_client_generation(): Запись отказов и проверка сессии приложения.
#
# -> test_unknown_callback_pins_record_until_actual_exit(): Запрет очистки ещё работающего обработчика.
#
# -> test_commit_failure_does_not_authorize_callback(): Запрет callback после неопределённого исхода commit.
#
# -> test_sqlite_lock_timeout_and_disk_bound(): Настоящая блокировка SQLite и физическое переполнение.
#
# -> _crash_worker(): Аварийная остановка отдельного процесса у границы записи.
#
# -> test_process_crash_boundaries(): Восстановление после аварийной остановки процесса.
#


#******************************************************************************************************************
# ИМПОРТ И ОПРЕДЕЛЕНИЯ
#******************************************************************************************************************
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from remote_watch import Identity
from remote_watch.command_protocol import (
    CommandClaim,
    CommandGrant,
    CommandRef,
    CommandRequest,
    CommandSession,
    callback_result,
    message_digest,
)
from remote_watch.command_state import CommandAction, CommandDeadline, CommandPhase
from remote_watch.command_storage import AuditCode, StoreConflict, StoreError, StoreFull, StoreLimits, StoreRole
from remote_watch.sqlite_command_store import SQLiteCommandStore


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка подставной команды
#------------------------------------------------------------------------------------------------------------------
def request(
    number: int = 1,
    epoch: str = 'a' * 32,
) -> CommandRequest:

    """Build deterministic synthetic requests without real actor or provider credentials.

    :param number: Synthetic event and command number.
    :type number: int

    :param epoch: Synthetic hub incarnation.
    :type epoch: str

    :return: Build deterministic synthetic requests without real actor or provider credentials.
    :rtype: CommandRequest
    """

    # number — номер подставной команды.
    # epoch — подставной ID запуска hub.

    ref = CommandRef(identity=Identity(service="loader", environment="test", region="ru", host="vm", instance_id="one"),
                     session_id="b" * 32, hub_epoch=epoch, command_id=f"{number:032x}")
    return CommandRequest(ref=ref, source_id="telegram", source_event_id=str(number), actor_id="42",
                          conversation_id="123", name="resume_load")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Согласованные данные разрешения и сессии
#------------------------------------------------------------------------------------------------------------------
def options(
    event: CommandRequest,
    now: float = 100,
) -> dict:

    """Build consistent session, claim, grant and relative deadlines.

    :param event: Synthetic immutable command request.
    :type event: CommandRequest

    :param now: Current local monotonic observation.
    :type now: float

    :return: Build consistent session, claim, grant and relative deadlines.
    :rtype: dict
    """

    # event — подставной запрос команды.
    # now — текущий локальный монотонный момент.

    claim = CommandClaim(ref=event.ref, claim_id="c" * 32, request_digest=message_digest(event))
    return {
        "session": CommandSession(identity=event.ref.identity, session_id=event.ref.session_id,
                                  hub_epoch=event.ref.hub_epoch, remaining_ttl=120),
        "claim": claim,
        "session_deadline": CommandDeadline.from_response(120, now, now, event.ref.hub_epoch),
        "grant": CommandGrant(request=event, claim_id=claim.claim_id, remaining_ttl=120, execution_timeout=10),
        "grant_deadline": CommandDeadline.from_response(10, now, now, event.ref.hub_epoch),
    }
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Открытие временного журнала с управляемыми часами
#------------------------------------------------------------------------------------------------------------------
def opened(
    path: Path,
    now: list[float],
    generation: str = 'a' * 32,
    limits: StoreLimits = StoreLimits(retention=300),
    role: StoreRole = StoreRole.HUB,
) -> SQLiteCommandStore:

    """Open an isolated journal with deterministic monotonic time.

    :param path: Local persistent database path.
    :type path: Path

    :param now: Current local monotonic observation.
    :type now: list[float]

    :param generation: Fresh owner incarnation; never reuse after process restart.
    :type generation: str

    :param limits: Finite capacity, retention and lock settings.
    :type limits: StoreLimits

    :param role: Hub or application-side journal role.
    :type role: StoreRole

    :return: Open an isolated journal with deterministic monotonic time.
    :rtype: SQLiteCommandStore
    """

    # path — путь локального файла журнала.
    # now — текущий локальный монотонный момент.
    # generation — новый ID запуска владельца журнала.
    # limits — конечные пределы ёмкости и ожидания.
    # role — журнал hub либо приложения.

    store = SQLiteCommandStore(path, owner_id="test", generation=generation, role=role,
                               limits=limits, clock=lambda: now[0])
    store.open()
    return store
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Атомарная запись решения и позиции источника
#------------------------------------------------------------------------------------------------------------------
def admit(
    store: SQLiteCommandStore,
    event: CommandRequest,
    now: float = 100,
) -> None:

    """Commit one event with a remaining lifetime before acknowledging its source.

    :param store: Journal instance participating in the test.
    :type store: SQLiteCommandStore

    :param event: Synthetic immutable command request.
    :type event: CommandRequest

    :param now: Current local monotonic observation.
    :type now: float
    """

    # store — проверяемый экземпляр журнала.
    # event — подставной запрос команды.
    # now — текущий локальный монотонный момент.

    position = int(event.source_event_id)
    result = store.admit("source", position, store.cursor("source"), event,
                         CommandDeadline.from_response(120, now, now, event.ref.hub_epoch))
    assert result.inserted
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Единственный победитель конкурентного старта и повтор результата
#------------------------------------------------------------------------------------------------------------------
def test_atomic_start_and_result_replay(tmp_path: Path) -> None:

    """Two independent connections racing one revision may authorize only one effect.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path
    """

    # tmp_path — временный каталог теста.

    now = [100.0]
    path = tmp_path / "commands.db"
    first, second = opened(path, now), opened(path, now)
    event = request()
    try:
        admit(first, event)
        data = options(event)
        claimed = first.transition(event.ref, 0, CommandAction.CLAIM, **data)

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Конкурирующая попытка начать команду
        #----------------------------------------------------------------------------------------------------------
        def start(store: SQLiteCommandStore) -> bool:

            """Race the same persisted revision through separate SQLite connections.

            :param store: Journal instance participating in the test.
            :type store: SQLiteCommandStore

            :return: Race the same persisted revision through separate SQLite connections.
            :rtype: bool
            """

            # store — проверяемый экземпляр журнала.

            try:
                return store.transition(event.ref, claimed.command.revision, CommandAction.START, **data).start_callback
            except StoreConflict:
                return False
        #----------------------------------------------------------------------------------------------------------

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sum(pool.map(start, (first, second))) == 1
        running = first.get(event.ref)
        assert not second.transition(event.ref, running.revision, CommandAction.START, **data).start_callback
        result = callback_result(event.ref, data["claim"].claim_id, "done")
        done = first.transition(event.ref, running.revision, CommandAction.FINISH, result=result, **data)
        assert done.command.record.phase is CommandPhase.COMPLETED
        assert not first.transition(event.ref, done.command.revision, CommandAction.FINISH, result=result, **data).start_callback
        first.acknowledge(result)
        first.acknowledge(result)
        with pytest.raises(StoreConflict):
            first.acknowledge(replace(result, text="other"))
        assert first.pending() == ()
    finally:
        first.close()
        second.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет продолжения старого поколения журнала
#------------------------------------------------------------------------------------------------------------------
def test_restart_fences_old_handles_and_work(tmp_path: Path) -> None:

    """Recovery closes all active phases and never recreates pre-restart deadlines.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path
    """

    # tmp_path — временный каталог теста.

    now = [100.0]
    old = opened(tmp_path / "commands.db", now)
    events = [request(i) for i in range(1, 4)]
    for event in events:
        admit(old, event)
    old.transition(events[1].ref, 0, CommandAction.CLAIM, **options(events[1]))
    claim = old.transition(events[2].ref, 0, CommandAction.CLAIM, **options(events[2]))
    old.transition(events[2].ref, claim.command.revision, CommandAction.START, **options(events[2]))
    now[0] = 1
    new = opened(tmp_path / "commands.db", now, "d" * 32)
    try:
        assert [row.record.phase for row in new.pending()] == [CommandPhase.EXPIRED, CommandPhase.EXPIRED, CommandPhase.UNKNOWN]
        assert new.cursor("source") == 3
        with pytest.raises(StoreConflict):
            old.cursor("source")
        with pytest.raises(StoreConflict):
            new.transition(events[2].ref, new.get(events[2].ref).revision, CommandAction.START, **options(events[2]))
        assert new.prune() == 0
    finally:
        old.close()
        new.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение защиты источника после очистки результата
#------------------------------------------------------------------------------------------------------------------
def test_retention_preserves_source_guards(tmp_path: Path) -> None:

    """Deleting terminal payload never resets the committed source position.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path
    """

    # tmp_path — временный каталог теста.

    now = [100.0]
    store = opened(tmp_path / "commands.db", now, limits=StoreLimits(records=1, audit=3, retention=300))
    event = request()
    try:
        admit(store, event)
        with pytest.raises(StoreFull):
            admit(store, request(2))
        assert store.cursor("source") == 1
        now[0] = 221
        expired = store.transition(event.ref, 0, CommandAction.CLAIM, **options(event))
        assert expired.command.record.phase is CommandPhase.EXPIRED
        store.acknowledge(expired.command.record.result)
        now[0] = 520
        assert store.prune() == 0
        now[0] = 521
        assert store.prune() == 1
        replay = store.admit("source", 1, -1, event, options(event)["grant_deadline"])
        assert not replay.inserted and replay.command is None and replay.cursor == 1
        admit(store, request(2), now[0])
        assert len(store.audit()) <= 3
        assert store.prune() == 0
    finally:
        store.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сужение исходного срока при позднем разрешении
#------------------------------------------------------------------------------------------------------------------
def test_delayed_grant_cannot_renew_original_lifetime(tmp_path: Path) -> None:

    """Waiting for a grant consumes the original admission deadline, not its whole execution timeout.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path
    """

    # tmp_path — временный каталог теста.

    now = [100.0]
    store = opened(tmp_path / "commands.db", now)
    event = request()
    try:
        admit(store, event)
        store.transition(event.ref, 0, CommandAction.CLAIM, **options(event))
        now[0] = 215
        running = store.transition(event.ref, 1, CommandAction.START, **options(event, now[0]))
        assert running.start_callback
        now[0] = 220
        stopped = store.transition(event.ref, running.command.revision, CommandAction.CLAIM, **options(event, now[0]))
        assert stopped.command.record.phase is CommandPhase.UNKNOWN
        assert not stopped.start_callback
    finally:
        store.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отсутствие подтверждения источнику при отказе записи
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["cursor", "command", "source", "session", "stream_limit", "bytes", "future", "rollback"])
def test_failed_admission_keeps_cursor(
    tmp_path: Path,
    case: str,
) -> None:

    """Rejected, conflicting or full transactions never acknowledge unrecorded commands.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path

    :param case: Selected success or failure scenario.
    :type case: str
    """

    # tmp_path — временный каталог теста.
    # case — выбранный тестовый сценарий.

    now = [100.0]
    store = opened(tmp_path / "commands.db", now,
                   limits=StoreLimits(streams=1, payload_bytes=65536 if case == "bytes" else 33554432, retention=300))
    try:
        if case == "bytes":
            with pytest.raises(StoreFull):
                admit(store, request())
            assert store.cursor("source") == -1
            return
        admit(store, request())
        event, stream, cursor = request(2), "source", 1
        if case == "cursor":
            cursor = -1
        elif case == "command":
            event = replace(event, ref=request().ref)
        elif case == "source":
            event = replace(event, source_event_id="1")
        elif case == "session":
            event = request(2, "d" * 32)
        elif case == "stream_limit":
            stream, cursor = "other", -1
        elif case == "future":
            now[0] = 99
        elif case == "rollback":
            now[0] = 80
        with pytest.raises(StoreError):
            store.admit(stream, 2, cursor, event, options(event)["grant_deadline"])
        with sqlite3.connect(tmp_path / "commands.db") as db:
            assert db.execute("SELECT position FROM streams WHERE name='source'").fetchone()[0] == 1
    finally:
        store.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запись отказов и проверка сессии приложения
#------------------------------------------------------------------------------------------------------------------
def test_ignored_events_and_client_generation(tmp_path: Path) -> None:

    """Persist rejected source decisions and fence application-session journals.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path
    """

    # tmp_path — временный каталог теста.

    now = [100.0]
    store = opened(tmp_path / "client.db", now, "b" * 32,
                   limits=StoreLimits(records=1, retention=300), role=StoreRole.CLIENT)
    try:
        assert store.admit("source", 1, -1, None, rejection=AuditCode.DENIED).cursor == 1
        assert store.admit("source", 1, -1, None).inserted is False
        event = request(2)
        admit(store, event)
        assert store.cursor("source") == 2
        assert store.audit()[0].code is AuditCode.DENIED
    finally:
        store.close()

    # Увеличение ёмкости разрешено только новому поколению. Попытка уменьшить
    # предел не должна повредить уже открытый журнал или его позицию источника.
    store = opened(tmp_path / "client.db", now, "d" * 32,
                   limits=StoreLimits(records=2, retention=300), role=StoreRole.CLIENT)
    try:
        next_event = request(3)
        next_event = replace(next_event, ref=replace(next_event.ref, session_id="d" * 32))
        admit(store, next_event)
        with pytest.raises(StoreError):
            opened(tmp_path / "client.db", now, "e" * 32,
                   limits=StoreLimits(records=1, retention=300), role=StoreRole.CLIENT)
        assert store.cursor("source") == 3
    finally:
        store.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет очистки ещё работающего обработчика
#------------------------------------------------------------------------------------------------------------------
def test_unknown_callback_pins_record_until_actual_exit(tmp_path: Path) -> None:

    """Timeout and ACK cannot make a still-running callback eligible for deletion.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path
    """

    # tmp_path — временный каталог теста.

    now = [100.0]
    store = opened(tmp_path / "commands.db", now)
    event = request()
    try:
        admit(store, event)
        claimed = store.transition(event.ref, 0, CommandAction.CLAIM, **options(event))
        started = store.transition(event.ref, claimed.command.revision, CommandAction.START, **options(event))
        now[0] = 111
        unknown = store.transition(event.ref, started.command.revision, CommandAction.CLAIM, **options(event))
        assert unknown.command.execution_active
        store.acknowledge(unknown.command.record.result)
        now[0] = 1000
        assert store.prune() == 0 and store.pending()[0].execution_active
        store.release_execution(event.ref, options(event)["claim"].claim_id)
        assert store.get(event.ref).record.phase is CommandPhase.UNKNOWN
        now[0] = 1300
        assert store.prune() == 1
    finally:
        store.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет callback после неопределённого исхода commit
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("moment", ["before", "after"])
def test_commit_failure_does_not_authorize_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    moment: str,
) -> None:

    """Even an ambiguous commit failure never returns permission to invoke a callback.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path

    :param monkeypatch: Pytest scoped dependency replacement.
    :type monkeypatch: pytest.MonkeyPatch

    :param moment: Before or after the selected commit.
    :type moment: str
    """

    # tmp_path — временный каталог теста.
    # monkeypatch — временная подмена зависимости в тесте.
    # moment — момент до или после фиксации.

    original = sqlite3.connect

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Соединение с подставным сбоем commit
    #--------------------------------------------------------------------------------------------------------------
    class Connection(sqlite3.Connection):
        """Inject a safe synthetic I/O failure at STARTED commit."""

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Проверяемая граница фиксации транзакции
        #----------------------------------------------------------------------------------------------------------
        def commit(self) -> None:

            """Raise before or after committing the execution marker."""

            exists = self.execute("SELECT 1 FROM sqlite_master WHERE name='commands'").fetchone()
            row = self.execute("SELECT phase FROM commands").fetchone() if exists else None
            hit = row is not None and row[0] == "started"
            if hit and moment == "before":
                raise sqlite3.OperationalError("private-disk-detail")
            super().commit()
            if hit and moment == "after":
                raise sqlite3.OperationalError("private-disk-detail")
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Создание тестового соединения SQLite
    #--------------------------------------------------------------------------------------------------------------
    def connect(
        database: object,
        **kwargs: object,
    ) -> sqlite3.Connection:

        """Create only instrumented test connections.

        :param database: Temporary SQLite database path.
        :type database: object

        :param kwargs: Arguments forwarded to the original test connection factory.
        :type kwargs: object

        :return: Create only instrumented test connections.
        :rtype: sqlite3.Connection
        """

        # database — путь временной базы SQLite.
        # kwargs — параметры тестового соединения.

        return original(database, factory=Connection, **kwargs)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(sqlite3, "connect", connect)
    path = tmp_path / "commands.db"
    store = opened(path, [100.0])
    event = request()
    admit(store, event)
    claimed = store.transition(event.ref, 0, CommandAction.CLAIM, **options(event))
    with pytest.raises(StoreError) as caught:
        store.transition(event.ref, claimed.command.revision, CommandAction.START, **options(event))
    assert "private" not in str(caught.value)
    with pytest.raises(StoreError):
        store.cursor("source")
    store.close()
    monkeypatch.setattr(sqlite3, "connect", original)
    recovered = opened(path, [1.0], "d" * 32)
    try:
        expected = CommandPhase.EXPIRED if moment == "before" else CommandPhase.UNKNOWN
        assert recovered.get(event.ref).record.phase is expected
    finally:
        recovered.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящая блокировка SQLite и физическое переполнение
#------------------------------------------------------------------------------------------------------------------
def test_sqlite_lock_timeout_and_disk_bound(tmp_path: Path) -> None:

    """Exercise real database locking and page limits instead of only logical row limits.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path
    """

    # tmp_path — временный каталог теста.

    now = [100.0]
    path = tmp_path / "bounded.db"
    limits = StoreLimits(database_bytes=1048576, lock_timeout=0.01, audit=3, retention=300)
    store = opened(path, now, limits=limits)
    other = sqlite3.connect(path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StoreError):
            store.cursor("source")
    finally:
        other.rollback()
        other.close()
        assert store.cursor("source") == -1
        store.close()
    store = opened(path, now, "d" * 32, limits)
    last = 0
    try:
        for number in range(1, 600):
            event = replace(request(number, "d" * 32), arguments={str(i): "x" * 1000 for i in range(4)})
            try:
                admit(store, event)
            except StoreError:
                break
            last = number
        else:
            pytest.fail("physical or logical capacity was not enforced")
        assert path.stat().st_size <= limits.database_bytes
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT position FROM streams").fetchone()[0] == last
            assert db.execute("SELECT count(*) FROM commands").fetchone()[0] == last
    finally:
        store.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Аварийная остановка отдельного процесса у границы записи
#------------------------------------------------------------------------------------------------------------------
def _crash_worker(
    path: str,
    phase: str,
    moment: str,
) -> None:

    """Terminate the process immediately around a selected durable transaction boundary.

    :param path: Local persistent database path.
    :type path: str

    :param phase: State whose commit is interrupted.
    :type phase: str

    :param moment: Before or after the selected commit.
    :type moment: str
    """

    # path — путь локального файла журнала.
    # phase — состояние у проверяемой границы commit.
    # moment — момент до или после фиксации.

    original = sqlite3.connect

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Соединение с подставным сбоем commit
    #--------------------------------------------------------------------------------------------------------------
    class Connection(sqlite3.Connection):
        """Inject a hard process exit around commit without graceful rollback."""

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Проверяемая граница фиксации транзакции
        #----------------------------------------------------------------------------------------------------------
        def commit(self) -> None:

            """Crash immediately before or after the selected commit."""

            exists = self.execute("SELECT 1 FROM sqlite_master WHERE name='commands'").fetchone()
            row = self.execute("SELECT phase, acknowledged FROM commands").fetchone() if exists else None
            position = self.execute("SELECT position FROM streams WHERE name='source'").fetchone() if exists else None
            hit = ((row is not None and row[0] == phase)
                   or (phase == "acknowledged" and row is not None and row[1] == 1)
                   or (phase == "pruned" and row is None and position is not None))
            if hit and moment == "before":
                os._exit(17)
            super().commit()
            if hit and moment == "after":
                os._exit(17)
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Создание тестового соединения SQLite
    #--------------------------------------------------------------------------------------------------------------
    def connect(
        database: object,
        **kwargs: object,
    ) -> sqlite3.Connection:

        """Use the instrumented connection only inside the test subprocess.

        :param database: Temporary SQLite database path.
        :type database: object

        :param kwargs: Arguments forwarded to the original test connection factory.
        :type kwargs: object

        :return: Use the instrumented connection only inside the test subprocess.
        :rtype: sqlite3.Connection
        """

        # database — путь временной базы SQLite.
        # kwargs — параметры тестового соединения.

        return original(database, factory=Connection, **kwargs)
    #--------------------------------------------------------------------------------------------------------------

    sqlite3.connect = connect
    now = [100.0]
    store = opened(Path(path), now)
    event = request()
    admit(store, event)
    claim = store.transition(event.ref, 0, CommandAction.CLAIM, **options(event))
    started = store.transition(event.ref, claim.command.revision, CommandAction.START, **options(event))
    if started.start_callback:
        # Файл фиксирует единственный синтетический эффект вне транзакции SQLite.
        with Path(path + ".effect").open("ab") as stream:
            stream.write(b"1")
            stream.flush()
            os.fsync(stream.fileno())
    result = callback_result(event.ref, options(event)["claim"].claim_id, "done")
    store.transition(event.ref, started.command.revision, CommandAction.FINISH, result=result, **options(event))
    store.acknowledge(result)
    now[0] = 1000
    store.prune()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Восстановление после аварийной остановки процесса
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("phase", ["ready", "claimed", "started", "completed", "unknown", "acknowledged", "pruned"])
@pytest.mark.parametrize("moment", ["before", "after"])
def test_process_crash_boundaries(
    tmp_path: Path,
    phase: str,
    moment: str,
) -> None:

    """Recover real abrupt exits without starting an already authorized callback again.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path

    :param phase: State whose commit is interrupted.
    :type phase: str

    :param moment: Before or after the selected commit.
    :type moment: str
    """

    # tmp_path — временный каталог теста.
    # phase — состояние у проверяемой границы commit.
    # moment — момент до или после фиксации.

    path = tmp_path / "crash.db"
    if phase == "unknown":
        # В этом сценарии прерывается сама транзакция восстановления поколения.
        prepared = opened(path, [100.0], "d" * 32)
        event = request(epoch="d" * 32)
        try:
            admit(prepared, event)
            prepared.transition(event.ref, 0, CommandAction.CLAIM, **options(event))
            prepared.transition(event.ref, 1, CommandAction.START, **options(event))
        finally:
            prepared.close()
    environment = os.environ.copy()
    # При wheel-проверке импортируется установленный пакет, без подмены исходниками.
    import remote_watch
    environment["PYTHONPATH"] = str(Path(remote_watch.__file__).resolve().parents[1])
    script = "import runpy,sys; runpy.run_path(sys.argv[1])['_crash_worker'](*sys.argv[2:])"
    child = subprocess.run([sys.executable, "-c", script, __file__, str(path), phase, moment],
                           env=environment, capture_output=True, timeout=30)
    assert child.returncode == 17, child.stderr.decode(errors="replace")
    store = opened(path, [1.0], "e" * 32)
    try:
        rows = store.pending()
        if phase == "ready" and moment == "before":
            assert not rows and store.cursor("source") == -1
        elif phase in ("acknowledged", "pruned"):
            saved = store.get(request().ref)
            if phase == "pruned" and moment == "after":
                assert saved is None
            else:
                assert saved.record.phase is CommandPhase.COMPLETED
            assert store.cursor("source") == 1
            assert not store.admit("source", 1, -1, None).inserted
        else:
            expected = (CommandPhase.COMPLETED if phase == "completed" and moment == "after" else
                        CommandPhase.UNKNOWN if phase in ("completed", "unknown") or (phase == "started" and moment == "after")
                        else CommandPhase.EXPIRED)
            assert rows[0].record.phase is expected
            assert store.cursor("source") == 1
            with pytest.raises(StoreConflict):
                store.transition(request().ref, rows[0].revision, CommandAction.START, **options(request()))
        effect = Path(str(path) + ".effect")
        assert (effect.read_bytes() if effect.exists() else b"") == (
            b"1" if phase in ("completed", "acknowledged", "pruned") else b""
        )
    finally:
        store.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.unit.test_sqlite_command_store не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
