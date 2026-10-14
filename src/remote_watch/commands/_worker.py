# Последовательные операции постоянного журнала с ограниченным ожиданием.
#
# Version 1.0.5
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-164146
#
# Классы:
# -> StoreLifecycle: Контракт ресурсов рабочего потока хранения
#    Интерфейс:
#    -> open(): Открытие принадлежащего журналу ресурса
#    -> close(): Закрытие ресурса после последней операции
#
# -> StoreWorker: Последовательное хранение без блокировки цикла событий
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта.
#    Интерфейс:
#    -> journal_status(): Чтение кэша агрегатов без обращения к SQLite
#    -> open(): Открытие принадлежащих объекту ресурсов
#    -> call(): Ограниченное ожидание с сохранением владения после отмены caller
#    -> close(): Остановка фоновой работы и закрытие ресурсов
#    Служебные методы:
#    -> _execute(): Обновление диагностики в той же storage lane после операции
#    -> _completed(): Освобождение исполнителя после фактического завершения операции
#
# Функции:
# -> _consume(): Извлечение поздней ошибки без записи её деталей в лог


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future as ThreadFuture
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from time import monotonic
from typing import Generic, Protocol, TypeVar

from remote_watch._validation import require_int
from remote_watch.commands.journal_stats import JournalStats, validate_stats, validate_status
from remote_watch.commands.storage import StoreConflict, StoreError, StoreFull
from remote_watch.commands.transport import CommandError

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

T = TypeVar("T")

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Контракт ресурсов рабочего потока хранения
#------------------------------------------------------------------------------------------------------------------
class StoreLifecycle(Protocol):
    """
    Describe the resources owned by a bounded storage worker.
    """

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие принадлежащего журналу ресурса
    #--------------------------------------------------------------------------------------------------------------
    def open(
        self,
        ) -> None:

        """
        Open the journal on the worker's thread.
        """

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие ресурса после последней операции
    #--------------------------------------------------------------------------------------------------------------
    def close(
        self,
        ) -> None:

        """
        Close the journal after the last submitted operation.
        """

        ...
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

S = TypeVar("S", bound=StoreLifecycle)

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Последовательное хранение без блокировки цикла событий
#------------------------------------------------------------------------------------------------------------------
class StoreWorker(Generic[S]):
    """
    Run at most one synchronous store operation without blocking the command loop.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        store: S,
        timeout: float,
        *,
        max_waiters: int = 0,
        ) -> None:

        """
        Configure a single storage lane without creating threads.

        :param store: Owned bounded persistent journal with a matching generation.
        :type store: S

        :param timeout: Finite wait limit in seconds.
        :type timeout: float

        :param max_waiters: Bounded async waiters; zero preserves immediate rejection.
        :type max_waiters: int
        """

        # store — принадлежащий объекту журнал с согласованным поколением.
        # timeout — конечный предел ожидания, секунды.

        # max_waiters — очередь только в event loop, не в ThreadPoolExecutor.

        require_int(max_waiters, "storage waiters", 0)
        if max_waiters > 256:
            raise ValueError("invalid storage waiter limit")

        self._max_waiters = max_waiters
        self._waiters = 0
        self._gate = asyncio.Lock()
        self.store = store
        self.timeout = timeout
        self._pool: ThreadPoolExecutor | None = None
        self._pending: asyncio.Future | None = None
        self._closing = False
        self._journal_lock = threading.Lock()
        self._journal_snapshot: JournalStats | None = None
        self._journal_at: float | None = None
        self._journal_next = 0.0
        self._journal_state = "not_observed"
        self._observing = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение кэша агрегатов без обращения к SQLite
    #--------------------------------------------------------------------------------------------------------------
    def journal_status(
        self,
        ) -> dict:

        """
        Read a bounded cached observation safely even from another application thread.

        :return: Fixed state, monotonic age and optional last successful aggregate.
        :rtype: dict
        """

        with self._journal_lock:
            age = (None if self._journal_at is None else
                   min(2**31 - 1, max(0, int(monotonic() - self._journal_at))))
            return validate_status({"state": "closed" if self._closing else self._journal_state,
                "age_seconds": age, "snapshot": None if self._journal_snapshot is None
                else asdict(self._journal_snapshot)})
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def open(
        self,
        ) -> None:

        """
        Create the one-worker executor and open the owned journal.
        """

        if self._pool is not None or self._closing:
            raise CommandError("closed")

        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remote-watch-command-store")

        try:
            await self.call(self.store.open)
        except BaseException:
            await self.close(self.timeout)
            raise
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ограниченное ожидание с сохранением владения после отмены caller
    #--------------------------------------------------------------------------------------------------------------
    async def call(
        self,
        operation: Callable[[], T],
        ) -> T:

        """
        Reject excess work and retain ownership after timeout or caller cancellation.

        :param operation: Single operation executed within the documented limits.
        :type operation: Callable[[], T]

        :return: Result of the single completed storage operation.
        :rtype: T
        """

        # operation — одна операция в пределах доступной ёмкости.

        if self._closing or self._pool is None:
            raise CommandError("closed")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        if self._gate.locked() or self._waiters:
            # Если запись уже закончена, но ещё читается сводка, разрешаем одному
            # caller дождаться её без обычного немедленного busy клиента/source.
            with self._journal_lock:
                waiting_limit = self._max_waiters or int(self._observing)
            if self._waiters >= waiting_limit:
                reason = "storage_busy" if self._max_waiters == 0 else "storage_queue_full"
                raise CommandError("busy", busy_reason=reason)
            self._waiters += 1
            try:
                await asyncio.wait_for(self._gate.acquire(), max(0, deadline - loop.time()))
            except asyncio.TimeoutError:
                raise CommandError("busy", busy_reason="storage_wait_timeout") from None
            finally:
                self._waiters -= 1
        else:
            await self._gate.acquire()

        # Ожидание съедает исходный бюджет. После отмены/закрытия отложенный
        # caller не должен незаметно отправить новую операцию в executor.
        try:
            if self._closing or self._pool is None:
                raise CommandError("closed")
            if loop.time() >= deadline:
                raise CommandError("busy", busy_reason="storage_wait_timeout")
            completed: ThreadFuture[T] = ThreadFuture()
            completed_at: list[float] = []
            operation_deadline = monotonic() + max(0, deadline - loop.time())
            future = loop.run_in_executor(self._pool, lambda: self._execute(operation, completed, completed_at))
        except BaseException:
            self._gate.release()
            raise

        self._pending = future
        future.add_done_callback(self._completed)

        try:
            try:
                return await asyncio.wait_for(asyncio.shield(future), max(0, deadline - loop.time()))
            except asyncio.TimeoutError:
                # Наблюдение может задержаться в файловой системе после commit.
                # Возвращаем только исход, полученный ДО срока реальной операции;
                # executor/gate остаются закреплены за ещё выполняющимся чтением.
                if completed.done() and completed_at[0] <= operation_deadline:
                    return completed.result()
                raise
        except StoreFull:
            raise CommandError("capacity") from None
        except (StoreConflict, ValueError, TypeError):
            raise CommandError("conflict") from None
        except (StoreError, asyncio.TimeoutError):
            raise CommandError("unavailable") from None
        except CommandError:
            raise
        except Exception:
            raise CommandError("unavailable") from None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(
        self,
        timeout: float,
        ) -> None:

        """
        Queue only journal closure behind the active job and bound the async wait.

        :param timeout: Finite wait limit in seconds.
        :type timeout: float
        """

        # timeout — конечный предел ожидания, секунды.

        if self._closing:
            return

        self._closing = True
        pool = self._pool

        if pool is None:
            return

        # Единственная разрешённая отложенная операция — закрытие после текущей записи.
        # Timeout не освобождает занятый рабочий поток и не отменяет уже выполняющийся commit.
        closed = asyncio.get_running_loop().run_in_executor(pool, self.store.close)
        closed.add_done_callback(_consume)
        pool.shutdown(wait=False)

        try:
            await asyncio.wait_for(asyncio.shield(closed), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._pool = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Обновление диагностики в той же storage lane после операции
    #--------------------------------------------------------------------------------------------------------------
    def _execute(
        self,
        operation: Callable[[], T],
        completed: ThreadFuture[T],
        completed_at: list[float],
        ) -> T:

        """
        Observe the owned journal periodically without adding executor submissions or changing results.

        :param operation: Original serialized storage operation.
        :type operation: Callable[[], T]

        :param completed: Thread-safe original result, independent of subsequent diagnostic work.
        :type completed: ThreadFuture[T]

        :param completed_at: Single monotonic completion time published before completed is set.
        :type completed_at: list[float]

        :return: Original operation result, including unchanged exception semantics.
        :rtype: T
        """

        # operation/completed/completed_at — диагностический сбой после commit не должен превратить
        # успешную запись в ошибку и спровоцировать повтор бизнес-действия.

        try:
            result = operation()
        except BaseException as error:
            completed_at.append(monotonic())
            with self._journal_lock:
                self._observing = True
            completed.set_exception(error)
            raise
        else:
            completed_at.append(monotonic())
            with self._journal_lock:
                self._observing = True
            completed.set_result(result)
            return result
        finally:
            now = monotonic()
            if now >= self._journal_next:
                self._journal_next = now + 5.0
                try:
                    reader = getattr(self.store, "journal_stats", None)
                    sample = None if reader is None else reader()
                    if sample is not None and type(sample) is not JournalStats:
                        raise ValueError("invalid journal sample")
                    if sample is not None:
                        validate_stats(asdict(sample))
                    with self._journal_lock:
                        self._journal_state = "unsupported" if sample is None else "available"
                        if sample is not None:
                            self._journal_snapshot = sample
                            self._journal_at = monotonic()
                except (Exception, asyncio.CancelledError):
                    # Только фиксированная категория; старый снимок не выдаётся
                    # за новый, его возраст и state явно показывают отказ наблюдения.
                    with self._journal_lock:
                        self._journal_state = "unavailable"
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Освобождение исполнителя после фактического завершения операции
    #--------------------------------------------------------------------------------------------------------------
    def _completed(
        self,
        future: asyncio.Future,
        ) -> None:

        """
        Release the storage lane only after the actual executor future completes.

        :param future: Actual storage completion, independent of caller cancellation.
        :type future: asyncio.Future
        """

        # future — запись могла закончиться уже после timeout или отмены caller.

        _consume(future)
        with self._journal_lock:
            self._observing = False

        self._gate.release()
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Извлечение поздней ошибки без записи её деталей в лог
#------------------------------------------------------------------------------------------------------------------
def _consume(
    future: asyncio.Future,
    ) -> None:

    """
    Retrieve late storage failures without logging their private payloads.

    :param future: Possibly cancelled asynchronous storage result.
    :type future: asyncio.Future
    """

    # future — завершение операции хранения, в том числе после отмены caller.

    if not future.cancelled():
        future.exception()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль _worker не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
