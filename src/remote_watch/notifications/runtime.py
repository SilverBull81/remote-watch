# Фоновая отправка уведомлений: ограниченные очереди, один поток и отдельный asyncio loop.
#
# Version 1.0.7
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Классы:
# -> RuntimeState: Состояния фоновой отправки.
#
# -> RuntimeStats: Счётчики уведомлений, доставок и ошибок.
#
# -> _DestinationState: Рабочая очередь и клиент одного получателя.
#
# -> NotificationRuntime: Владелец фонового потока и каналов отправки.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> state(): Чтение текущего состояния runtime.
#    -> stats(): Независимая копия счётчиков.
#    -> start(): Запуск фонового потока и подготовка клиентов.
#    -> astart(): Асинхронный запуск без блокировки loop приложения.
#    -> stop(): Ожидание завершения в пределах общего срока.
#    -> astop(): Асинхронная остановка без блокировки loop приложения.
#    Специальные методы:
#    -> __enter__(): Запуск при входе в контекст.
#    -> __exit__(): Остановка при выходе из контекста.
#    -> __aenter__(): Вход в асинхронный контекст.
#    -> __aexit__(): Выход из асинхронного контекста.
#    Служебные методы:
#    -> _finish_lifecycle(): Завершение уже запущенного lifecycle при отмене вызывающей задачи.
#    -> _lifecycle_call(): Ожидание синхронного lifecycle вне loop приложения.
#    -> _count(): Изменение счётчика под блокировкой.
#    -> _accepting(): Проверка возможности приёма новых записей.
#    -> _submit(): Приём уведомления без ожидания свободного места.
#    -> _schedule_wake_locked(): Объединение пробуждений рабочего потока.
#    -> _wake(): Передача сигнала consumer в рабочем loop.
#    -> _request_stop(): Закрытие приёма и запрос на остановку.
#    -> _thread_main(): Владение рабочим loop и изоляция внутренних записей logging.
#    -> _open_channels(): Создание и подготовка клиентов в рабочем потоке.
#    -> _run(): Полный цикл фоновой отправки и освобождения ресурсов.
#    -> _drain_deadline(): Срок обработки очереди с резервом на закрытие клиентов.
#    -> _dispatch(): Передача уведомлений в ограниченные очереди получателей.
#    -> _send_loop(): Последовательные попытки отправки одному получателю.
#    -> _attempt(): Одна ограниченная попытка с безопасной классификацией ошибок.
#    -> _deliver(): Повторы одной доставки без освобождения её слота.
#    -> _delivery_now(): Проверенное показание монотонных часов доставки.
#    -> _cleanup(): Отмена остатка работы и закрытие клиентов.
#    -> _close_channel(): Закрытие одного клиента в пределах общего срока.
#
# Функции:
# -> utc_now(): Текущее время UTC с часовым поясом.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import inspect
import math
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from enum import Enum
from types import TracebackType
from uuid import uuid4

from remote_watch._validation import require_callback
from remote_watch.config import DeliveryMode, Destination, WatcherConfig
from remote_watch.events import Notification
from remote_watch.notifications._context import delivery_context
from remote_watch.notifications._retry import retry_delay
from remote_watch.notifications._shutdown import register, unregister
from remote_watch.notifications.channels import NotificationChannel
from remote_watch.notifications.delivery import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    ResultSource,
)
from remote_watch.notifications.handler import NotificationHandler
from remote_watch.notifications.routing import PolicyRouter
from remote_watch.notifications.timing import DeliveryClock, SystemDeliveryClock

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Состояния фоновой отправки
#------------------------------------------------------------------------------------------------------------------
class RuntimeState(str, Enum):
    """Expose the lifecycle of a single-use notification runtime."""

    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    CLOSED = "closed"
    FAILED = "failed"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Счётчики уведомлений, доставок и ошибок
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RuntimeStats:
    """Snapshot cumulative counters; event and delivery units are documented separately."""

    attempts: int = 0                       # Начатые попытки отправки, включая первую.
    retries: int = 0                        # Начатые повторные попытки.
    retry_waits: int = 0                    # Назначенные ожидания перед повтором.
    permanent_failure: int = 0              # Доставки с окончательным отказом сервиса.
    exhausted: int = 0                      # Доставки, исчерпавшие число попыток.
    scheduler_errors: int = 0               # Ошибки подменяемых часов или генератора задержек.
    shutdown_discarded_retries: int = 0     # Доставки, отменённые между попытками.
    admitted: int = 0                       # Уведомления, принятые входной очередью.
    suppressed: int = 0                     # Записи, исключённые флагом, контекстом или правилами.
    normalization_failed: int = 0           # Записи с ошибкой подготовки данных.
    not_running: int = 0                    # Записи, пришедшие вне рабочего состояния.
    ingress_overflow: int = 0               # Уведомления, отклонённые полной входной очередью.
    routed: int = 0                         # Доставки, принятые очередями получателей.
    destination_overflow: int = 0           # Доставки, отклонённые лимитом получателя.
    accepted: int = 0                       # Попытки, принятые сервисом доставки.
    failed_attempts: int = 0                # Попытки с известным отказом сервиса.
    unknown_attempts: int = 0               # Попытки с неизвестным исходом, включая таймаут.
    adapter_errors: int = 0                 # Исключения и неверные результаты адаптера.
    expired: int = 0                        # Доставки, для которых уже не хватает оставшегося времени.
    shutdown_discarded_events: int = 0      # Остаток входной очереди при остановке.
    shutdown_discarded_deliveries: int = 0  # Доставки, отменённые до начала попытки.
    shutdown_unknown: int = 0               # Активные попытки, отменённые при остановке.
    startup_failed: int = 0                 # Неудачные запуски runtime.
    close_failed: int = 0                   # Каналы, которые не удалось штатно закрыть.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Рабочая очередь и клиент одного получателя
#------------------------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class _DestinationState:
    """Keep bounded delivery state owned exclusively by the worker loop."""

    destination: Destination                        # Настройки получателя.
    channel: NotificationChannel                    # Клиент, созданный в рабочем потоке.
    queue: asyncio.Queue[tuple[Delivery, float]]    # Доставки и их сроки по монотонным часам.
    outstanding: int = 0                            # Очередь, активная отправка и ожидание повтора.
    task: asyncio.Task[None] | None = None          # Единственный исполнитель для получателя.
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Текущее время UTC с часовым поясом
#------------------------------------------------------------------------------------------------------------------
def utc_now() -> datetime:

    """Read an aware UTC timestamp.

    :return: Current UTC time.
    :rtype: datetime
    """

    return datetime.now(timezone.utc)
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Владелец фонового потока и каналов отправки
#------------------------------------------------------------------------------------------------------------------
class NotificationRuntime:
    """Own one thread, one loop and bounded delivery queues including retries.

    Channel factories must be quick and synchronous;
    channel methods must be asynchronous and cooperate with cancellation.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: WatcherConfig,
        *,
        redactor: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] = utc_now,
        delivery_clock: DeliveryClock | None = None,
        random_source: Callable[[], float] = random.random,
    ) -> None:

        """Prepare local state without starting threads or constructing channels.

        :param config: Validated application configuration.
        :type config: WatcherConfig

        :param redactor: Optional synchronous text redactor used by the handler.
        :type redactor: Callable[[str], str] | None

        :param clock: Aware UTC clock for event timestamps; deadlines use monotonic time.
        :type clock: Callable[[], datetime]

        :param delivery_clock: Injectable monotonic delivery clock and cancellable waits.
        :type delivery_clock: DeliveryClock | None

        :param random_source: Nonblocking random fraction source for full jitter.
        :type random_source: Callable[[], float]
        """

        # config - неизменяемые настройки приложения и получателей.
        # redactor - редактор сообщений; применяется до помещения данных в очередь.
        # clock - часы для дат уведомлений; тесты могут передать фиксированное время.
        # delivery_clock, random_source - подменяемые часы доставки и источник случайных задержек.

        if not isinstance(config, WatcherConfig):
            raise TypeError("config must be WatcherConfig")

        require_callback(clock, 0, "clock", allow_async=False)
        require_callback(random_source, 0, "random_source", allow_async=False)
        self._delivery_clock = delivery_clock if delivery_clock is not None else SystemDeliveryClock()
        require_callback(self._delivery_clock.monotonic, 0, "delivery_clock.monotonic", allow_async=False)

        if not inspect.iscoroutinefunction(self._delivery_clock.sleep):
            raise TypeError("delivery_clock.sleep must be asynchronous")

        self._random_source = random_source
        self.config = config
        self.session_id = uuid4().hex
        self.notification_ttl = max((item.retry.ttl for item in config.destinations), default=300.0)
        self._utc_now = clock
        self._router = PolicyRouter(config.routes)

        # Короткая блокировка защищает только состояние, счётчики и входную очередь.
        # Приём не ждёт места; сетевые операции и пользовательский код выполняются вне блокировки.
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._state = RuntimeState.CREATED
        self._counts = {item.name: 0 for item in fields(RuntimeStats)}
        self._destination_counts = {item.destination_id: self._counts.copy() for item in config.destinations}
        self._ingress: deque[tuple[Notification, float]] = deque()
        self._wake_pending = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._signal: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._done = threading.Event()
        self._stop_requested = False
        self._cancelling = False
        self._stop_deadline: float | None = None
        self._startup_deadline = 0.0
        self._destinations: dict[str, _DestinationState] = {}
        self.handler = NotificationHandler(self, redactor=redactor)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение текущего состояния runtime
    #--------------------------------------------------------------------------------------------------------------
    @property
    def state(self) -> RuntimeState:

        """Read the current lifecycle state.

        :return: Current state.
        :rtype: RuntimeState
        """

        with self._lock:
            return self._state
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Независимая копия счётчиков
    #--------------------------------------------------------------------------------------------------------------
    def stats(
        self,
        destination_id: str | None = None,
    ) -> RuntimeStats:

        """Read independent cumulative counters safely from any thread.

        :param destination_id: Destination identifier, or None for aggregate counters.
        :type destination_id: str | None

        :return: Immutable counter snapshot.
        :rtype: RuntimeStats
        """

        # destination_id - получатель для детализации; без него возвращается общая статистика.

        with self._lock:
            counts = self._counts if destination_id is None else self._destination_counts[destination_id]
            return RuntimeStats(**counts)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск фонового потока и подготовка клиентов
    #--------------------------------------------------------------------------------------------------------------
    def start(self) -> None:

        """Start the worker and wait for channel initialization within startup_timeout."""

        if threading.current_thread() is self._thread:
            raise RuntimeError("start cannot be called from a channel")

        with self._lifecycle_lock:
            with self._lock:
                if self._state is RuntimeState.RUNNING:
                    return

                if self._state is not RuntimeState.CREATED:
                    raise RuntimeError("a closed or failed runtime cannot be restarted")

                self._startup_deadline = time.monotonic() + self.config.runtime.startup_timeout
                self._thread = threading.Thread(target=self._thread_main, name="remote-watch", daemon=True)
                register(self)
                try:
                    self._thread.start()
                except Exception:
                    self._state = RuntimeState.FAILED
                    self._counts["startup_failed"] += 1
                    self._done.set()
                    unregister(self)
                    raise RuntimeError("notification worker could not be started") from None

            self._ready.wait(max(0.0, self._startup_deadline - time.monotonic()))

            with self._lock:
                if self._state is RuntimeState.RUNNING:
                    return

                if self._state is not RuntimeState.FAILED:
                    self._state = RuntimeState.FAILED
                    self._counts["startup_failed"] += 1

                self._stop_requested = True
                self._schedule_wake_locked()

            # Текст исходного исключения адаптера может содержать адреса и ключи доступа.
            raise RuntimeError("notification runtime failed to start") from None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Асинхронный запуск без блокировки loop приложения
    #--------------------------------------------------------------------------------------------------------------
    async def astart(self) -> None:

        """Start without blocking the application loop; cancellation also stops the runtime."""

        if threading.current_thread() is self._thread:
            raise RuntimeError("astart cannot be called from a channel")

        await self._lifecycle_call(self.start, stop_on_cancel=True)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ожидание завершения в пределах общего срока
    #--------------------------------------------------------------------------------------------------------------
    def stop(self) -> None:

        """Drain and close within shutdown_timeout, raising if the worker cannot finish."""

        # Адаптер может запросить остановку из send(). Он не должен ожидать завершения самого себя.
        if threading.current_thread() is self._thread:
            self._request_stop()
            return

        with self._lifecycle_lock:
            self._request_stop()

            if self._thread is None or self._thread.ident is None:
                return

            deadline = self._stop_deadline or (time.monotonic() + self.config.runtime.shutdown_timeout)
            self._thread.join(max(0.0, deadline - time.monotonic()))

            if self._thread.is_alive():
                with self._lock:
                    self._state = RuntimeState.FAILED

                raise TimeoutError("notification worker did not stop within shutdown_timeout")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Асинхронная остановка без блокировки loop приложения
    #--------------------------------------------------------------------------------------------------------------
    async def astop(self) -> None:

        """Stop without blocking the application loop, finishing cleanup even if cancelled."""

        if threading.current_thread() is self._thread:
            self._request_stop()
            return

        await self._lifecycle_call(self.stop, stop_on_cancel=False)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Запуск при входе в контекст
    #--------------------------------------------------------------------------------------------------------------
    def __enter__(self) -> NotificationRuntime:

        """Start a runtime context.

        :return: This runtime.
        :rtype: NotificationRuntime
        """

        self.start()
        return self
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Остановка при выходе из контекста
    #--------------------------------------------------------------------------------------------------------------
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:

        """Stop the context without suppressing application exceptions.

        :param exc_type: Active exception type, if any.
        :type exc_type: type[BaseException] | None

        :param exc_value: Active exception instance, if any.
        :type exc_value: BaseException | None

        :param traceback: Active traceback, if any.
        :type traceback: TracebackType | None
        """

        # exc_type, exc_value, traceback - стандартные аргументы контекстного менеджера.

        self.stop()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Вход в асинхронный контекст
    #--------------------------------------------------------------------------------------------------------------
    async def __aenter__(self) -> NotificationRuntime:

        """Start an asynchronous runtime context.

        :return: This runtime.
        :rtype: NotificationRuntime
        """

        await self.astart()
        return self
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Выход из асинхронного контекста
    #--------------------------------------------------------------------------------------------------------------
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:

        """Stop without suppressing application exceptions.

        :param exc_type: Active exception type, if any.
        :type exc_type: type[BaseException] | None

        :param exc_value: Active exception instance, if any.
        :type exc_value: BaseException | None

        :param traceback: Active traceback, if any.
        :type traceback: TracebackType | None
        """

        # exc_type, exc_value, traceback - стандартные сведения об исключении приложения.

        await self.astop()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Завершение уже запущенного lifecycle при отмене вызывающей задачи
    #--------------------------------------------------------------------------------------------------------------
    async def _finish_lifecycle(
        self,
        task: asyncio.Task[None],
    ) -> None:

        """Wait for an owned lifecycle operation despite repeated caller cancellation.

        :param task: Shielded start/stop task that must be observed to completion.
        :type task: asyncio.Task[None]
        """

        # task - операция с собственным конечным сроком ожидания фонового потока.

        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue

        task.result()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Ожидание синхронного lifecycle вне loop приложения
    #--------------------------------------------------------------------------------------------------------------
    async def _lifecycle_call(
        self,
        operation: Callable[[], None],
        *,
        stop_on_cancel: bool,
    ) -> None:

        """Offload a bounded blocking operation and preserve ownership on cancellation.

        :param operation: Synchronous start or stop.
        :type operation: Callable[[], None]

        :param stop_on_cancel: Whether a cancelled start must be followed by stop.
        :type stop_on_cancel: bool
        """

        # operation - ожидание запуска или остановки; каналами по-прежнему владеет только worker.
        # stop_on_cancel - отменённый запуск не должен оставлять работающий runtime без владельца.

        task = asyncio.create_task(asyncio.to_thread(operation))

        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Отмена ожидания не останавливает to_thread. Дожидаемся его результата без блокировки loop.
            try:
                await self._finish_lifecycle(task)
            except Exception:
                pass

            if stop_on_cancel:
                cleanup = asyncio.create_task(asyncio.to_thread(self.stop))

                try:
                    await self._finish_lifecycle(cleanup)
                except Exception:
                    pass

            raise
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Изменение счётчика под блокировкой
    #--------------------------------------------------------------------------------------------------------------
    def _count(
        self,
        name: str,
        amount: int = 1,
        *,
        destination_id: str | None = None,
    ) -> None:

        """Increment a fixed internal counter.

        :param name: Declared RuntimeStats field name.
        :type name: str

        :param amount: Counter increment.
        :type amount: int

        :param destination_id: Optional destination receiving the same increment.
        :type destination_id: str | None
        """

        # name, amount - имя заранее объявленного счётчика и величина изменения.
        # destination_id - получатель; число наборов счётчиков ограничено настройками.

        with self._lock:
            self._counts[name] += amount

            if destination_id is not None:
                self._destination_counts[destination_id][name] += amount
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка возможности приёма новых записей
    #--------------------------------------------------------------------------------------------------------------
    def _accepting(self) -> bool:

        """Check whether normalization may begin.

        :return: Whether the runtime currently accepts events.
        :rtype: bool
        """

        return self.state is RuntimeState.RUNNING
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Приём уведомления без ожидания свободного места
    #--------------------------------------------------------------------------------------------------------------
    def _submit(
        self,
        notification: Notification,
        received_at: float,
    ) -> None:

        """Admit a prepared event or count a drop without waiting for space.

        :param notification: Detached notification created by this runtime's handler.
        :type notification: Notification

        :param received_at: Monotonic timestamp before formatting began.
        :type received_at: float
        """

        # notification - данные после подготовки в handler; исходная запись здесь не удерживается.
        # received_at - начало подготовки записи; форматирование тоже расходует срок отправки.

        with self._lock:
            # Повторная проверка закрывает гонку между долгим форматированием и stop().
            if self._state is not RuntimeState.RUNNING:
                self._counts["not_running"] += 1
                return

            if len(self._ingress) >= self.config.runtime.ingress_capacity:
                self._counts["ingress_overflow"] += 1
                return

            self._ingress.append((notification, received_at))
            self._counts["admitted"] += 1
            self._schedule_wake_locked()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Объединение пробуждений рабочего потока
    #--------------------------------------------------------------------------------------------------------------
    def _schedule_wake_locked(self) -> None:

        """Schedule at most one outstanding cross-thread wakeup while holding the lock."""

        # Один callback будит consumer для всей накопленной порции, а не для каждого сообщения.
        if not self._wake_pending and self._loop is not None:
            self._wake_pending = True

            try:
                self._loop.call_soon_threadsafe(self._wake)
            except RuntimeError:
                # При неудачном запуске loop мог уже закрыться, а finally рабочего потока ещё ждёт lock.
                self._wake_pending = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Передача сигнала consumer в рабочем loop
    #--------------------------------------------------------------------------------------------------------------
    def _wake(self) -> None:

        """Deliver a coalesced wakeup on the worker loop."""

        with self._lock:
            self._wake_pending = False

        if self._signal is not None:
            self._signal.set()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Закрытие приёма и запрос на остановку
    #--------------------------------------------------------------------------------------------------------------
    def _request_stop(
        self,
        *,
        deadline: float | None = None,
    ) -> None:

        """Close admission and wake the worker without joining it.

        :param deadline: Optional stricter real monotonic deadline for process exit.
        :type deadline: float | None
        """

        # deadline - общий срок atexit; обычная остановка использует настройки runtime.

        with self._lock:
            if self._state is RuntimeState.CREATED and self._thread is None:
                self._state = RuntimeState.CLOSED
                self._done.set()
                return

            if self._done.is_set():
                return

            if self._state is not RuntimeState.FAILED:
                self._state = RuntimeState.STOPPING

            if self._stop_deadline is None:
                self._stop_deadline = time.monotonic() + self.config.runtime.shutdown_timeout

            if deadline is not None:
                self._stop_deadline = min(self._stop_deadline, deadline)

            self._stop_requested = True
            self._schedule_wake_locked()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Владение рабочим loop и изоляция внутренних записей logging
    #--------------------------------------------------------------------------------------------------------------
    def _thread_main(self) -> None:

        """Own the worker loop and exclude all its logging from notification pipelines."""

        token = delivery_context.set(True)

        try:
            asyncio.run(self._run())
        except BaseException:
            # Любой выход фонового loop с ошибкой означает FAILED; исходное исключение не публикуем.
            with self._lock:
                if self._state is RuntimeState.CREATED:
                    self._counts["startup_failed"] += 1

                self._state = RuntimeState.FAILED
        finally:
            with self._lock:
                self._loop = None
                self._wake_pending = False

                if self._state is not RuntimeState.FAILED:
                    self._state = RuntimeState.CLOSED

            self._ready.set()
            self._done.set()
            delivery_context.reset(token)
            unregister(self)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Создание и подготовка клиентов в рабочем потоке
    #--------------------------------------------------------------------------------------------------------------
    async def _open_channels(self) -> None:

        """Construct and open clients on the worker, retaining partial opens for cleanup."""

        for destination in self.config.destinations:
            channel = destination.channel_factory()

            if any(
                not inspect.iscoroutinefunction(getattr(channel, name, None))
                for name in ("open", "send", "close")
            ):
                raise TypeError("channel must implement async open/send/close")

            if any(state.channel is channel for state in self._destinations.values()):
                raise ValueError("each destination must own a separate channel instance")

            state = _DestinationState(destination, channel, asyncio.Queue(destination.outstanding_capacity))
            self._destinations[destination.destination_id] = state
            await channel.open()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Полный цикл фоновой отправки и освобождения ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def _run(self) -> None:

        """Open channels, route events and close all owned resources."""

        self._signal = asyncio.Event()

        with self._lock:
            self._loop = asyncio.get_running_loop()

        try:
            # Резерв на закрытие нужен и при зависшем open, иначе он израсходует весь срок запуска.
            reserve = min(0.1, self.config.runtime.startup_timeout * 0.2)
            open_timeout = max(0.0, self._startup_deadline - time.monotonic() - reserve)
            await asyncio.wait_for(self._open_channels(), open_timeout)

            with self._lock:
                if self._stop_requested or time.monotonic() >= self._startup_deadline:
                    raise RuntimeError("startup deadline exceeded")

                self._state = RuntimeState.RUNNING

            # Число исполнителей фиксировано числом получателей, а не количеством сообщений.
            for state in self._destinations.values():
                state.task = asyncio.create_task(self._send_loop(state))

            self._ready.set()
            await self._dispatch()

            # Часть общего срока оставляем для отмены активных отправок и закрытия клиентов.
            remaining = max(0.0, self._drain_deadline() - time.monotonic())

            try:
                await asyncio.wait_for(
                    asyncio.gather(*(state.queue.join() for state in self._destinations.values())),
                    remaining,
                )
            except asyncio.TimeoutError:
                pass
        finally:
            # Закрываем приём до очистки, включая неожиданный сбой consumer.
            with self._lock:
                self._stop_requested = True

                if self._state is RuntimeState.RUNNING:
                    self._state = RuntimeState.STOPPING

                self._counts["shutdown_discarded_events"] += len(self._ingress)
                self._ingress.clear()

            await self._cleanup()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Срок обработки очереди с резервом на закрытие клиентов
    #--------------------------------------------------------------------------------------------------------------
    def _drain_deadline(self) -> float:

        """Reserve a small part of the stop budget for cancellation and close.

        :return: Absolute monotonic drain deadline.
        :rtype: float
        """

        deadline = self._stop_deadline

        if deadline is None:
            return float("inf")

        return deadline - min(0.1, self.config.runtime.shutdown_timeout * 0.2)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Передача уведомлений в ограниченные очереди получателей
    #--------------------------------------------------------------------------------------------------------------
    async def _dispatch(self) -> None:

        """Route bounded batches and yield to per-destination senders between batches."""

        while True:
            await self._signal.wait()
            self._signal.clear()

            for _ in range(64):
                with self._lock:
                    if not self._ingress or time.monotonic() >= self._drain_deadline():
                        break

                    notification, admitted_at = self._ingress.popleft()

                selected = self._router.select(notification)

                if not selected:
                    self._count("suppressed")

                for destination_id in selected:
                    state = self._destinations[destination_id]

                    if state.outstanding >= state.destination.outstanding_capacity:
                        self._count("destination_overflow", destination_id=destination_id)
                        continue

                    # Deadline переводится в монотонное время один раз; последующая коррекция UTC его не продлит.
                    ttl = min(
                        (notification.expires_at - notification.created_at).total_seconds(),
                        state.destination.retry.ttl,
                    )
                    delivery = Delivery(
                        notification=notification, destination_id=destination_id, delivery_id=uuid4().hex,
                    )
                    state.queue.put_nowait((delivery, admitted_at + ttl))
                    state.outstanding += 1
                    self._count("routed", destination_id=destination_id)
                    del delivery

                # Не удерживаем последний обработанный снимок во время ожидания следующей записи.
                del notification

            with self._lock:
                if self._stop_requested and (not self._ingress or time.monotonic() >= self._drain_deadline()):
                    return

                if self._ingress:
                    self._signal.set()

            await asyncio.sleep(0)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Последовательные попытки отправки одному получателю
    #--------------------------------------------------------------------------------------------------------------
    async def _send_loop(
        self,
        state: _DestinationState,
    ) -> None:

        """Send sequentially for one destination without blocking other destinations.

        :param state: Loop-owned channel and queue state.
        :type state: _DestinationState
        """

        # state - состояние одного получателя; слот занят в очереди, при отправке и в паузе между попытками.

        while True:
            delivery, deadline = await state.queue.get()

            try:
                await self._deliver(state, delivery, deadline)
            except Exception:
                # Ошибка пользовательского источника времени или случайных чисел не убивает очередь.
                self._count("scheduler_errors", destination_id=state.destination.destination_id)
            finally:
                state.outstanding -= 1
                state.queue.task_done()
                del delivery
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Одна ограниченная попытка с безопасной классификацией ошибок
    #--------------------------------------------------------------------------------------------------------------
    async def _attempt(
        self,
        state: _DestinationState,
        delivery: Delivery,
        timeout: float,
    ) -> DeliveryResult:

        """Perform one attempt, converting adapter failures into an unknown outcome.

        :param state: Destination channel state.
        :type state: _DestinationState

        :param delivery: Stable delivery with the current attempt number.
        :type delivery: Delivery

        :param timeout: Remaining attempt budget in real seconds.
        :type timeout: float

        :return: Validated provider result or a safe unknown outcome.
        :rtype: DeliveryResult
        """

        # state, delivery, timeout - получатель, текущая попытка и оставшееся время.

        destination_id = state.destination.destination_id
        self._count("attempts", destination_id=destination_id)

        if delivery.attempt > 1:
            self._count("retries", destination_id=destination_id)

        try:
            # Relay должен ограничить срок gateway остатком TTL/остановки, а не исходной политикой.
            bounded = replace(delivery, remaining_timeout=timeout)
            result = await asyncio.wait_for(state.channel.send(bounded), timeout)

            if not isinstance(result, DeliveryResult):
                raise TypeError("channel returned an invalid result")

            return result
        except asyncio.CancelledError:
            if self._cancelling:
                self._count("shutdown_unknown", destination_id=destination_id)
                raise

            # Самостоятельный CancelledError адаптера не должен остановить все будущие отправки.
            self._count("adapter_errors", destination_id=destination_id)
        except asyncio.TimeoutError:
            pass
        except Exception:
            self._count("adapter_errors", destination_id=destination_id)

        source = ResultSource.RELAY if state.destination.mode is DeliveryMode.RELAY else ResultSource.PROVIDER
        return DeliveryResult(status=DeliveryStatus.UNKNOWN, source=source)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Повторы одной доставки без освобождения её слота
    #--------------------------------------------------------------------------------------------------------------
    async def _deliver(
        self,
        state: _DestinationState,
        delivery: Delivery,
        deadline: float,
    ) -> None:

        """Keep a delivery at the queue head through bounded attempts and retry waits.

        :param state: Destination channel and retry policy.
        :type state: _DestinationState

        :param delivery: Initial delivery whose identifier must remain stable.
        :type delivery: Delivery

        :param deadline: Absolute expiry according to the delivery clock.
        :type deadline: float
        """

        # state, delivery - получатель и доставка; delivery_id сохраняется между повторами.
        # deadline - один срок на всю доставку, а не новый срок для каждой попытки.

        policy = state.destination.retry
        destination_id = state.destination.destination_id

        while True:
            ttl_remaining = deadline - self._delivery_now()
            stop_remaining = self._drain_deadline() - time.monotonic()

            if min(ttl_remaining, stop_remaining) <= 0:
                reason = "expired" if ttl_remaining <= stop_remaining else (
                    "shutdown_discarded_retries" if delivery.attempt > 1 else "shutdown_discarded_deliveries"
                )
                self._count(reason, destination_id=destination_id)
                return

            timeout = min(ttl_remaining, stop_remaining, policy.attempt_timeout)
            result = await self._attempt(state, delivery, timeout)

            if result.status is DeliveryStatus.PROVIDER_ACCEPTED:
                self._count("accepted", destination_id=destination_id)
                return

            if result.status is DeliveryStatus.UNKNOWN:
                self._count("unknown_attempts", destination_id=destination_id)
            else:
                self._count("failed_attempts", destination_id=destination_id)

            if result.status is DeliveryStatus.PERMANENT_FAILURE:
                self._count("permanent_failure", destination_id=destination_id)
                return

            if delivery.attempt >= policy.max_attempts:
                self._count("exhausted", destination_id=destination_id)
                return

            delay = retry_delay(policy, delivery.attempt, result.retry_after, self._random_source())
            ttl_remaining = deadline - self._delivery_now()
            stop_remaining = self._drain_deadline() - time.monotonic()

            # Не держим заведомо бесполезный таймер, если следующая попытка уже не поместится в срок.
            if delay >= min(ttl_remaining, stop_remaining):
                reason = "expired" if ttl_remaining <= stop_remaining else "shutdown_discarded_retries"
                self._count(reason, destination_id=destination_id)
                return

            self._count("retry_waits", destination_id=destination_id)

            try:
                # Один ожидающий исполнитель на получателя; новые задачи для будущих повторов не создаются.
                wake_at = self._delivery_now() + delay
                await self._delivery_clock.sleep(delay)

                # Таймер может проснуться раньше срока из-за точности часов. Нижнюю границу retry-after сохраняем.
                while True:
                    remaining_wait = wake_at - self._delivery_now()

                    if remaining_wait <= 0:
                        break

                    await self._delivery_clock.sleep(remaining_wait)
            except asyncio.CancelledError:
                if self._cancelling:
                    self._count("shutdown_discarded_retries", destination_id=destination_id)
                    raise

                raise RuntimeError("delivery clock cancelled its own wait") from None

            delivery = replace(delivery, attempt=delivery.attempt + 1)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверенное показание монотонных часов доставки
    #--------------------------------------------------------------------------------------------------------------
    def _delivery_now(self) -> float:

        """Read a finite numeric delivery timestamp without accepting malformed clock values.

        :return: Monotonic delivery time.
        :rtype: float
        """

        value = self._delivery_clock.monotonic()

        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("delivery clock must return finite numeric time")

        return float(value)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Отмена остатка работы и закрытие клиентов
    #--------------------------------------------------------------------------------------------------------------
    async def _cleanup(self) -> None:

        """Cancel remaining attempts, count discarded work and close initialized channels."""

        self._cancelling = True
        tasks = [state.task for state in self._destinations.values() if state.task is not None]

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for state in self._destinations.values():
            while not state.queue.empty():
                state.queue.get_nowait()
                state.queue.task_done()
                state.outstanding -= 1
                self._count("shutdown_discarded_deliveries", destination_id=state.destination.destination_id)

        # При ошибке запуска используем остаток startup budget; при остановке — общий shutdown budget.
        deadline = self._stop_deadline if self._stop_deadline is not None else self._startup_deadline

        # Независимые клиенты закрываем параллельно: медленный close не должен лишать
        # остальных возможности освободить соединения. Число задач ограничено max_destinations.
        # Небольшой остаток оставляем для завершения loop и потока после отмены close.
        remaining = max(0.0, deadline - time.monotonic())
        close_deadline = deadline - min(0.05, remaining * 0.2)
        await asyncio.gather(*(
            self._close_channel(state, close_deadline) for state in self._destinations.values()
        ))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Закрытие одного клиента в пределах общего срока
    #--------------------------------------------------------------------------------------------------------------
    async def _close_channel(
        self,
        state: _DestinationState,
        deadline: float,
    ) -> None:

        """Close an independent channel without consuming another channel's opportunity to close.

        :param state: Owned destination state.
        :type state: _DestinationState

        :param deadline: Shared monotonic cleanup deadline.
        :type deadline: float
        """

        # state - канал и счётчики одного получателя.
        # deadline - общий срок завершения закрытия клиентов.

        try:
            await asyncio.wait_for(state.channel.close(), max(0.0, deadline - time.monotonic()))
        except (Exception, asyncio.CancelledError):
            self._count("close_failed", destination_id=state.destination.destination_id)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.notifications.runtime не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
