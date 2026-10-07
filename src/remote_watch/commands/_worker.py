# Последовательные операции постоянного журнала с ограниченным ожиданием.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261007-235742
#
# Классы:
# -> StoreLifecycle: Контракт ресурсов рабочего потока хранения.
#    Интерфейс:
#    -> open(): Открытие принадлежащего журналу ресурса.
#    -> close(): Закрытие ресурса после последней операции.
#
# -> StoreWorker: Последовательное хранение без блокировки цикла событий.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие принадлежащих объекту ресурсов.
#    -> call(): Ограниченное ожидание с сохранением владения после отмены caller.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.
#    Служебные методы:
#    -> _completed(): Освобождение исполнителя после фактического завершения операции.
#
# Функции:
# -> _consume(): Извлечение поздней ошибки без записи её деталей в лог.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Generic, Protocol, TypeVar

from remote_watch._validation import require_int
from remote_watch.commands.storage import StoreConflict, StoreError, StoreFull
from remote_watch.commands.transport import CommandError

T = TypeVar("T")


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Контракт ресурсов рабочего потока хранения
#------------------------------------------------------------------------------------------------------------------
class StoreLifecycle(Protocol):
    """Describe the resources owned by a bounded storage worker."""

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие принадлежащего журналу ресурса
    #--------------------------------------------------------------------------------------------------------------
    def open(self) -> None:

        """Open the journal on the worker's thread."""

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие ресурса после последней операции
    #--------------------------------------------------------------------------------------------------------------
    def close(self) -> None:

        """Close the journal after the last submitted operation."""

        ...
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


S = TypeVar("S", bound=StoreLifecycle)


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Последовательное хранение без блокировки цикла событий
#------------------------------------------------------------------------------------------------------------------
class StoreWorker(Generic[S]):
    """Run at most one synchronous store operation without blocking the command loop."""


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

        """Configure a single storage lane without creating threads.

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
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Create the one-worker executor and open the owned journal."""

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

        """Reject excess work and retain ownership after timeout or caller cancellation.

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
            if self._waiters >= self._max_waiters:
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
            future = loop.run_in_executor(self._pool, operation)
        except BaseException:
            self._gate.release()
            raise
        self._pending = future
        future.add_done_callback(self._completed)

        try:
            return await asyncio.wait_for(asyncio.shield(future), max(0, deadline - loop.time()))
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

        """Queue only journal closure behind the active job and bound the async wait.

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
    # СЛУЖЕБНЫЙ МЕТОД : Освобождение исполнителя после фактического завершения операции
    #--------------------------------------------------------------------------------------------------------------
    def _completed(
        self,
        future: asyncio.Future,
    ) -> None:

        """Release the storage lane only after the actual executor future completes.

        :param future: Actual storage completion, independent of caller cancellation.
        :type future: asyncio.Future
        """

        # future — запись могла закончиться уже после timeout или отмены caller.
        _consume(future)
        self._gate.release()
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Извлечение поздней ошибки без записи её деталей в лог
#------------------------------------------------------------------------------------------------------------------
def _consume(future: asyncio.Future) -> None:

    """Retrieve late storage failures without logging their private payloads.

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
