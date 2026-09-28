# Фоновая отправка уведомлений: ограниченные очереди, один поток и отдельный asyncio loop.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-140516
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
#    -> __init__(): Подготовка состояния объекта без запуска фоновой работы.
#    Интерфейс:
#    -> state(): Чтение текущего состояния runtime.
#    -> stats(): Независимая копия счётчиков.
#    -> start(): Запуск фонового потока и подготовка клиентов.
#    -> stop(): Ожидание завершения в пределах общего срока.
#    Специальные методы:
#    -> __enter__(): Запуск при входе в контекст.
#    -> __exit__(): Остановка при выходе из контекста.
#    Служебные методы:
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
#    -> _cleanup(): Отмена остатка работы и закрытие клиентов.
#
# Функции:
# -> utc_now(): Текущее время UTC с часовым поясом.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from types import TracebackType
from uuid import uuid4

from ._context import delivery_context
from ._validation import require_callback
from .channels import NotificationChannel
from .config import Destination, WatcherConfig
from .delivery import Delivery, DeliveryResult, DeliveryStatus
from .events import Notification
from .logging_handler import NotificationHandler
from .routing import PolicyRouter

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
    expired: int = 0                        # Доставки, срок которых истёк до начала попытки.
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
    outstanding: int = 0                            # Очередь вместе с активной попыткой.
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
    """Own one thread, one loop and bounded single-attempt delivery queues.

    This stage requires max_attempts=1 explicitly. Retry scheduling and asynchronous
    lifecycle wrappers are deferred. Channel factories must be quick and synchronous;
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
        ) -> None:

        """Prepare local state without starting threads or constructing channels.

        :param config: Validated application configuration.
        :type config: WatcherConfig

        :param redactor: Optional synchronous text redactor used by the handler.
        :type redactor: Callable[[str], str] | None

        :param clock: Aware UTC clock for event timestamps; deadlines use monotonic time.
        :type clock: Callable[[], datetime]
        """

        # config - неизменяемые настройки приложения и получателей.
        # redactor - редактор сообщений; применяется до помещения данных в очередь.
        # clock - часы для дат уведомлений; тесты могут передать фиксированное время.

        if not isinstance(config, WatcherConfig):
            raise TypeError("config must be WatcherConfig")

        if any(destination.retry.max_attempts != 1 for destination in config.destinations):
            raise ValueError("retry scheduling is not implemented; set max_attempts=1 for this stage")

        require_callback(clock, 0, "clock", allow_async=False)
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
    def stats(self) -> RuntimeStats:

        """Read independent cumulative counters safely from any thread.

        :return: Immutable counter snapshot.
        :rtype: RuntimeStats
        """

        with self._lock:
            return RuntimeStats(**self._counts)
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
                try:
                    self._thread.start()
                except Exception:
                    self._state = RuntimeState.FAILED
                    self._counts["startup_failed"] += 1
                    self._done.set()
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
    # СЛУЖЕБНЫЙ МЕТОД : Изменение счётчика под блокировкой
    #--------------------------------------------------------------------------------------------------------------
    def _count(
        self,
        name: str,
        amount: int = 1,
        ) -> None:

        """Increment a fixed internal counter.

        :param name: Declared RuntimeStats field name.
        :type name: str

        :param amount: Counter increment.
        :type amount: int
        """

        # name, amount - имя заранее объявленного счётчика и величина изменения.

        with self._lock:
            self._counts[name] += amount
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
    def _request_stop(self) -> None:

        """Close admission and wake the worker without joining it."""

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
                        self._count("destination_overflow")
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
                    self._count("routed")
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

        # state - состояние одного получателя; слот занят и в очереди, и во время отправки.

        while True:
            delivery, deadline = await state.queue.get()

            try:
                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    self._count("expired")
                    continue

                try:
                    result = await asyncio.wait_for(
                        state.channel.send(delivery),
                        min(remaining, state.destination.retry.attempt_timeout),
                    )

                    if not isinstance(result, DeliveryResult):
                        raise TypeError("channel returned an invalid result")
                except asyncio.CancelledError:
                    # Самостоятельный CancelledError адаптера не должен навсегда остановить его очередь.
                    if not self._cancelling:
                        self._count("adapter_errors")
                        self._count("unknown_attempts")
                        continue

                    # Отмена во время остановки не доказывает, что сервис ничего не получил.
                    self._count("shutdown_unknown")
                    raise
                except asyncio.TimeoutError:
                    self._count("unknown_attempts")
                    continue
                except Exception:
                    self._count("adapter_errors")
                    self._count("unknown_attempts")
                    continue

                if result.status is DeliveryStatus.PROVIDER_ACCEPTED:
                    self._count("accepted")
                elif result.status is DeliveryStatus.UNKNOWN:
                    self._count("unknown_attempts")
                else:
                    # Повторы появятся на следующем этапе; здесь каждая доставка имеет ровно одну попытку.
                    self._count("failed_attempts")
            finally:
                state.outstanding -= 1
                state.queue.task_done()
                del delivery
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
                self._count("shutdown_discarded_deliveries")

        # При ошибке запуска используем остаток startup budget; при остановке — общий shutdown budget.
        deadline = self._stop_deadline if self._stop_deadline is not None else self._startup_deadline

        for state in reversed(tuple(self._destinations.values())):
            try:
                await asyncio.wait_for(state.channel.close(), max(0.0, deadline - time.monotonic()))
            except (Exception, asyncio.CancelledError):
                self._count("close_failed")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.runtime не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
