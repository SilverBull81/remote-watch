# Последовательные операции постоянного журнала с ограниченным ожиданием.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Классы:
# -> StoreWorker: Последовательное хранение без блокировки цикла событий.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие принадлежащих объекту ресурсов.
#    -> call(): Отказ при занятости с сохранением владения после отмены caller.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.
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
from typing import TypeVar

from remote_watch.commands.storage import CommandStore, StoreConflict, StoreError, StoreFull
from remote_watch.commands.transport import CommandError

T = TypeVar("T")


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Последовательное хранение без блокировки цикла событий
#------------------------------------------------------------------------------------------------------------------
class StoreWorker:
    """Run at most one synchronous store operation without blocking the command loop."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        store: CommandStore,
        timeout: float,
    ) -> None:

        """Configure a single storage lane without creating threads.

        :param store: Owned bounded persistent journal with a matching generation.
        :type store: CommandStore

        :param timeout: Finite wait limit in seconds.
        :type timeout: float
        """

        # store — принадлежащий объекту журнал с согласованным поколением.
        # timeout — конечный предел ожидания, секунды.

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
    # ИНТЕРФЕЙС : Отказ при занятости с сохранением владения после отмены caller
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

        if self._pending is not None and not self._pending.done():
            raise CommandError("busy")
        future = asyncio.get_running_loop().run_in_executor(self._pool, operation)
        self._pending = future
        future.add_done_callback(_consume)

        try:
            return await asyncio.wait_for(asyncio.shield(future), self.timeout)
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
