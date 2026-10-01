# Постоянная позиция источника и защита от повторов после сбоев.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-185745
#
# Классы:
# -> ProcessLock: Исключительная блокировка владельца на уровне ОС.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие ресурсов и проверка владения.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.
#
# -> SourceJournal: Журнал позиции, незавершённого решения и защиты от повторов.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие ресурсов и проверка владения.
#    -> bind(): Закрепление владельца журнала и провайдера.
#    -> state(): Чтение позиции и одного незавершённого решения.
#    -> contains(): Проверка уже обработанного ID события.
#    -> reserve(): Сохранение решения до передачи команды в hub.
#    -> commit(): Сохранение позиции после решения hub.
#    -> skip_duplicate(): Проход через уже обработанное событие.
#    -> prune(): Удаление только устаревшей защиты от повторов.
#    -> retired(): Проверка необратимой границы удалённых событий.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.
#
# Функции:
# -> _encode_pending(): Запись ограниченного решения без секретов.
# -> _decode_pending(): Восстановление решения с прежней сессией.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO

from remote_watch._validation import require_text
from remote_watch.commands.protocol import CommandRequest, decode_command, encode_command
from remote_watch.commands.source_protocol import SourceEvent, SourcePending
from remote_watch.commands.storage import StoreConflict, StoreFull


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Исключительная блокировка владельца на уровне ОС
#------------------------------------------------------------------------------------------------------------------
class ProcessLock:
    """Hold one nonblocking operating-system lock until explicit close or process death."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        path: Path,
    ) -> None:

        """Retain the explicit lock path without touching the filesystem.

        :param path: Explicit local data path or fixed HTTP operation path.
        :type path: Path
        """

        # path — явный путь локальных данных либо фиксированный путь HTTP-операции.

        self.path = Path(path)
        self._handle: BinaryIO | None = None
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов и проверка владения
    #--------------------------------------------------------------------------------------------------------------
    def open(self) -> None:

        """Refuse a second process before either owner can consume provider events."""

        if self._handle is not None:
            raise StoreConflict("process lock already open")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")

        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._handle = handle
        except BaseException:
            handle.close()
            raise StoreConflict("another command owner is active") from None
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    def close(self) -> None:

        """Release the lock without deleting another process's lock pathname."""

        if self._handle is not None:
            self._handle.close()
            self._handle = None
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Журнал позиции, незавершённого решения и защиты от повторов
#------------------------------------------------------------------------------------------------------------------
class SourceJournal:
    """Retain one in-flight decision, an opaque cursor and bounded provider deduplication records."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        path: Path,
        *,
        seen_limit: int = 1000,
    ) -> None:

        """Prepare an exclusively owned source journal without filesystem access.

        :param path: Explicit local data path or fixed HTTP operation path.
        :type path: Path

        :param seen_limit: Maximum retained fresh provider event identifiers.
        :type seen_limit: int
        """

        # path — явный путь локальных данных либо фиксированный путь HTTP-операции.
        # seen_limit — предел сохраняемых ID ещё свежих событий провайдера.

        if type(seen_limit) is not int or not 32 <= seen_limit <= 10000:
            raise ValueError("invalid source journal capacity")
        self.path = Path(path)
        self._limit = seen_limit
        self._db: sqlite3.Connection | None = None
        self._lock: ProcessLock | None = None
        self._provider_lock: ProcessLock | None = None
        self._closed = False
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов и проверка владения
    #--------------------------------------------------------------------------------------------------------------
    def open(self) -> None:

        """Acquire a lifetime process lock and open a size-bounded synchronous SQLite journal."""

        if self._closed or self._db is not None:
            raise StoreConflict("source journal closed")
        self._lock = ProcessLock(self.path.with_suffix(self.path.suffix + ".lock"))

        try:
            self._lock.open()
            self._db = sqlite3.connect(self.path, timeout=1, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA max_page_count=2048")
            # Ограничение действует и при заполнении диска. Незнакомую схему
            # не перезаписываем: потеря прежней позиции могла бы повторить команду.
            if self._db.execute("PRAGMA page_count").fetchone()[0] > 2048:
                raise StoreFull("source journal capacity")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StoreConflict("source journal schema")
            if version == 0:
                if self._db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise StoreConflict("unknown source journal")
                with self._db:
                    self._db.execute("CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), "
                                     "fingerprint TEXT, position INTEGER, cursor TEXT, "
                                     "pending TEXT, retired_before INTEGER)")
                    self._db.execute("INSERT INTO state VALUES (1, NULL, -1, '', NULL, 0)")
                    self._db.execute("CREATE TABLE seen (event TEXT PRIMARY KEY, date INTEGER NOT NULL)")
                    self._db.execute("PRAGMA user_version=1")
        except BaseException:
            self.close()
            raise
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрепление владельца журнала и провайдера
    #--------------------------------------------------------------------------------------------------------------
    def bind(
        self,
        fingerprint: str,
        reader_key: str | None = None,
    ) -> None:

        """Prevent reuse of another provider's cursor or unfinished command.

        :param fingerprint: Stable nonsecret identity of this provider and source owner.
        :type fingerprint: str

        :param reader_key: Hexadecimal provider lock key, or None for a journal-only fixture.
        :type reader_key: str | None
        """

        # fingerprint — устойчивый отпечаток провайдера и владельца источника.
        # reader_key — ключ блокировки провайдера; None для отдельного теста журнала.

        require_text(fingerprint, "source fingerprint", 256)

        if reader_key is not None and self._provider_lock is None:
            if len(reader_key) != 64 or any(char not in "0123456789abcdef" for char in reader_key):
                raise StoreConflict("invalid provider lock")
            self._provider_lock = ProcessLock(self.path.parent / ("provider-" + reader_key + ".lock"))
            self._provider_lock.open()
        with self._db:
            old = self._db.execute("SELECT fingerprint FROM state WHERE id=1").fetchone()[0]
            if old is not None and old != fingerprint:
                raise StoreConflict("source journal owner changed")
            self._db.execute("UPDATE state SET fingerprint=? WHERE id=1", (fingerprint,))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение позиции и одного незавершённого решения
    #--------------------------------------------------------------------------------------------------------------
    def state(self) -> tuple[int, str, SourcePending | None]:

        """Read the checkpoint and at most one previously bound pending decision.

        :return: Committed local position, opaque cursor and optional pending decision.
        :rtype: tuple[int, str, SourcePending | None]
        """

        position, cursor, payload = self._db.execute(
            "SELECT position, cursor, pending FROM state WHERE id=1").fetchone()
        return position, cursor, None if payload is None else _decode_pending(payload)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка уже обработанного ID события
    #--------------------------------------------------------------------------------------------------------------
    def contains(
        self,
        event_id: str,
    ) -> bool:

        """Identify a provider redelivery even when its opaque cursor disappeared from the cache.

        :param event_id: Opaque provider message identifier.
        :type event_id: str

        :return: True when the queried journal condition holds.
        :rtype: bool
        """

        # event_id — непрозрачный ID сообщения провайдера.

        return self._db.execute("SELECT 1 FROM seen WHERE event=?", (event_id,)).fetchone() is not None
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Сохранение решения до передачи команды в hub
    #--------------------------------------------------------------------------------------------------------------
    def reserve(
        self,
        pending: SourcePending,
    ) -> None:

        """Persist the exact request before hub submission, reserving space for the replay guard.

        :param pending: Previously selected request and its original provider event.
        :type pending: SourcePending
        """

        # pending — выбранный запрос вместе с исходным событием провайдера.

        payload = _encode_pending(pending)
        with self._db:
            position, _, old = self.state()
            # Повтор той же операции после потери ответа допустим. Другое решение
            # не может занять место уже выбранной сессии, даже если она недоступна.
            if old is not None:
                if old != pending:
                    raise StoreConflict("source pending conflict")
                return
            if pending.position != position + 1 or self.contains(pending.event.event_id):
                raise StoreConflict("source position conflict")
            if self._db.execute("SELECT count(*) FROM seen").fetchone()[0] >= self._limit:
                raise StoreFull("source replay capacity")
            self._db.execute("UPDATE state SET pending=? WHERE id=1", (payload,))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Сохранение позиции после решения hub
    #--------------------------------------------------------------------------------------------------------------
    def commit(
        self,
        pending: SourcePending,
    ) -> None:

        """Advance provider position only after the hub committed the same logical position.

        :param pending: Previously selected request and its original provider event.
        :type pending: SourcePending
        """

        # pending — выбранный запрос вместе с исходным событием провайдера.

        with self._db:
            position, cursor, old = self.state()
            if old is None and position == pending.position and cursor == pending.event.event_id:
                return
            if old != pending:
                raise StoreConflict("source pending conflict")
            self._db.execute(
                "INSERT INTO seen VALUES (?, ?)", (pending.event.event_id, pending.event.message_date))
            self._db.execute("UPDATE state SET position=?, cursor=?, pending=NULL WHERE id=1",
                             (pending.position, pending.event.event_id))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проход через уже обработанное событие
    #--------------------------------------------------------------------------------------------------------------
    def skip_duplicate(
        self,
        event_id: str,
    ) -> None:

        """Move through already processed provider history without creating a new logical command.

        :param event_id: Opaque provider message identifier.
        :type event_id: str
        """

        # event_id — непрозрачный ID сообщения провайдера.

        if not self.contains(event_id):
            raise StoreConflict("unknown source duplicate")
        with self._db:
            self._db.execute("UPDATE state SET cursor=? WHERE id=1", (event_id,))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Удаление только устаревшей защиты от повторов
    #--------------------------------------------------------------------------------------------------------------
    def prune(
        self,
        cutoff: int | None,
    ) -> None:

        """Remove replay guards only when trusted UTC proves their messages can no longer be accepted.

        :param cutoff: Exclusive Unix-second pruning boundary, or None without trusted time.
        :type cutoff: int | None
        """

        # cutoff — граница очистки в Unix seconds; None запрещает очистку.

        if cutoff is not None:
            with self._db:
                # Граница необратима: откат показаний внешнего времени после очистки
                # не должен снова сделать допустимыми уже забытые ID сообщений.
                self._db.execute("UPDATE state SET retired_before=max(retired_before, ?)", (cutoff,))
                self._db.execute("DELETE FROM seen WHERE date<?", (cutoff,))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка необратимой границы удалённых событий
    #--------------------------------------------------------------------------------------------------------------
    def retired(
        self,
        message_date: int,
    ) -> bool:

        """Reject dates whose replay evidence was pruned, even after a trusted-clock rollback.

        :param message_date: Integer provider timestamp, never the VM wall clock.
        :type message_date: int

        :return: True when the queried journal condition holds.
        :rtype: bool
        """

        # message_date — целая отметка провайдера, а не системные часы VM.

        cutoff = self._db.execute("SELECT retired_before FROM state WHERE id=1").fetchone()[0]
        return message_date < cutoff
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    def close(self) -> None:

        """Release SQLite and the process lock without deleting durable replay evidence."""

        self._closed = True

        if self._db is not None:
            self._db.close()
            self._db = None

        if self._provider_lock is not None:
            self._provider_lock.close()
            self._provider_lock = None

        if self._lock is not None:
            self._lock.close()
            self._lock = None
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запись ограниченного решения без секретов
#------------------------------------------------------------------------------------------------------------------
def _encode_pending(pending: SourcePending) -> str:

    """Encode a bounded pending decision without credentials or provider-specific payloads.

    :param pending: Previously selected request and its original provider event.
    :type pending: SourcePending

    :return: Bounded JSON representation of the selected source decision.
    :rtype: str
    """

    # pending — выбранный запрос вместе с исходным событием провайдера.

    value = {"position": pending.position, "event": asdict(pending.event), "notice": pending.notice,
             "request": None if pending.request is None else encode_command(pending.request).decode("utf-8")}
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    if len(payload.encode("utf-8")) > 131072:
        raise StoreFull("source payload capacity")
    return payload
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Восстановление решения с прежней сессией
#------------------------------------------------------------------------------------------------------------------
def _decode_pending(payload: str) -> SourcePending:

    """Recover the previously bound session rather than retargeting a command after restart.

    :param payload: Bounded JSON body or a serialized pending decision.
    :type payload: str

    :return: Durable decision bound to the original provider event and target session.
    :rtype: SourcePending
    """

    # payload — ограниченное тело JSON либо записанное решение источника.

    if len(payload.encode("utf-8")) > 131072:
        raise StoreConflict("source payload capacity")
    value = json.loads(payload)
    request = None if value["request"] is None else decode_command(value["request"].encode("utf-8"))

    if request is not None and type(request) is not CommandRequest:
        raise StoreConflict("source request type")
    return SourcePending(position=value["position"], event=SourceEvent(**value["event"]),
                         request=request, notice=value["notice"])
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль source_store не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
