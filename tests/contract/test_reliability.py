# Проверки повторов, ограничений работы и асинхронного управления runtime без сети.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Классы:
# -> VirtualClock: Тестовые часы с мгновенным продвижением времени.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> monotonic(): Чтение монотонного времени доставки.
#    -> sleep(): Отменяемое ожидание перед повторной попыткой.
#
# -> PausedClock: Управляемая пауза между попытками для проверки остановки.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> sleep(): Отменяемое ожидание перед повторной попыткой.
#
# -> ScriptedChannel: Тестовый канал с заданной последовательностью ответов.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Подготовка тестового канала и барьера запуска.
#    -> send(): Возврат очередного ответа или ошибки тестового сервиса.
#    -> close(): Фиксация закрытия тестового канала.
#
# Тесты:
# -> make_runtime(): Создание runtime с подменяемым временем и случайными задержками.
# -> finish_deliveries(): Ожидание завершения принятых доставок через тестовый барьер.
#
# Тесты:
# -> test_retry_ids_and_full_jitter(): Сохранение идентификаторов и увеличение номера попытки.
# -> test_terminal_results(): Различение окончательного отказа и исчерпания попыток.
# -> test_retry_after_and_expiry(): Нижняя граница retry-after и общий срок доставки.
# -> test_adapter_error_can_retry(): Повторы после неизвестного исхода и нарушения контракта адаптера.
# -> test_retry_keeps_capacity_and_neighbor_progress(): Сохранение слота при ожидании и независимость получателей.
# -> test_async_context_and_cancelled_start(): Отмена асинхронного запуска с последующим закрытием.
# -> test_retry_order_and_scheduler_error(): Порядок событий и изоляция ошибки генератора задержек.
# -> test_blocking_adapter_exceeds_stop_budget(): Явный отказ остановки при заблокированном чужим кодом loop.
# -> test_atexit_cleanup(): Закрытие runtime при завершении отдельного процесса.
# -> test_cancelled_async_stop(): Завершение остановки при отмене ожидающего приложения.
# -> test_retry_burst_remains_bounded(): Ограничение задач и доставок при одновременных повторах.
# -> test_astop_from_channel(): Асинхронный запрос остановки из рабочего канала.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

import pytest

from remote_watch import (
    Delivery,
    DeliveryClock,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Identity,
    NotificationRuntime,
    RetryPolicy,
    Route,
    RuntimeConfig,
    RuntimeState,
    WatcherConfig,
)

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Тестовые часы с мгновенным продвижением времени
#------------------------------------------------------------------------------------------------------------------
class VirtualClock:
    """Advance delivery time without real backoff waits."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Initialize a deterministic time source."""

        self.now = 10000.0
        self.sleeps: list[float] = []
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение монотонного времени доставки
    #--------------------------------------------------------------------------------------------------------------
    def monotonic(self) -> float:

        """Read virtual delivery time.

        :return: Virtual time in seconds.
        :rtype: float
        """

        return self.now
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Отменяемое ожидание перед повторной попыткой
    #--------------------------------------------------------------------------------------------------------------
    async def sleep(
        self,
        delay: float,
    ) -> None:

        """Advance time and allow other channel tasks to run.

        :param delay: Requested delay.
        :type delay: float
        """

        # delay - проверяемый интервал; реальное время на его ожидание не расходуется.

        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Управляемая пауза между попытками для проверки остановки
#------------------------------------------------------------------------------------------------------------------
class PausedClock(VirtualClock):
    """Hold a retry wait until runtime cancellation for bounds and shutdown tests."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Prepare a barrier identifying the retry-wait phase."""

        super().__init__()
        self.waiting = threading.Event()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Отменяемое ожидание перед повторной попыткой
    #--------------------------------------------------------------------------------------------------------------
    async def sleep(
        self,
        delay: float,
    ) -> None:

        """Hold this destination without blocking the worker loop.

        :param delay: Scheduled retry delay.
        :type delay: float
        """

        # delay - запланированная задержка; тест завершит её отменой runtime.

        self.sleeps.append(delay)
        self.waiting.set()
        await asyncio.Event().wait()
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Тестовый канал с заданной последовательностью ответов
#------------------------------------------------------------------------------------------------------------------
class ScriptedChannel:
    """Return a predefined sequence of results without network activity."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        results: tuple[object, ...] = (),
    ) -> None:

        """Prepare scripted results and synchronization barriers.

        :param results: Results or exceptions consumed one per attempt.
        :type results: tuple[object, ...]
        """

        # results - ответы сервиса или исключения; после списка возвращается принятие сообщения.

        self.results = deque(results)
        self.deliveries: list[Delivery] = []
        self.opened = threading.Event()
        self.sent = threading.Event()
        self.closed = False
        self.block_open = False
        self.block_send = False
        self.gate: asyncio.Event | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка тестового канала и барьера запуска
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Prepare a loop-owned gate and optionally block initialization."""

        self.loop = asyncio.get_running_loop()
        self.gate = asyncio.Event()
        self.opened.set()

        if self.block_open:
            await self.gate.wait()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Возврат очередного ответа или ошибки тестового сервиса
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
    ) -> DeliveryResult:

        """Return the next scripted result or raise the next scripted exception.

        :param delivery: Current delivery attempt.
        :type delivery: Delivery

        :return: Scripted provider response.
        :rtype: DeliveryResult
        """

        # delivery - попытка; список позволяет проверить идентификаторы и порядок повторов.

        self.deliveries.append(delivery)
        self.sent.set()

        if self.block_send:
            await self.gate.wait()

        result = self.results.popleft() if self.results else DeliveryResult(
            status=DeliveryStatus.PROVIDER_ACCEPTED,
        )

        if isinstance(result, BaseException):
            raise result

        return result
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Фиксация закрытия тестового канала
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Record successful channel cleanup."""

        self.closed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание runtime с подменяемым временем и случайными задержками
#------------------------------------------------------------------------------------------------------------------
def make_runtime(
    identity: Identity,
    channels: tuple[ScriptedChannel, ...],
    *,
    clock: DeliveryClock | None = None,
    policy: RetryPolicy | None = None,
    capacity: int = 16,
    random_source: Callable[[], float] = lambda: 1.0,
) -> tuple[NotificationRuntime, logging.Logger]:

    """Build an isolated runtime with injectable retry time and randomness.

    :param identity: Explicit application identity.
    :type identity: Identity

    :param channels: Fake destinations.
    :type channels: tuple[ScriptedChannel, ...]

    :param clock: Delivery timing implementation.
    :type clock: DeliveryClock | None

    :param policy: Retry policy or defaults.
    :type policy: RetryPolicy | None

    :param capacity: Outstanding capacity per destination.
    :type capacity: int

    :param random_source: Deterministic jitter fraction source.
    :type random_source: Callable[[], float]

    :return: Unstarted runtime and its isolated application logger.
    :rtype: tuple[NotificationRuntime, logging.Logger]
    """

    # identity, channels - тестовое приложение и независимые получатели.
    # clock, policy, capacity, random_source - воспроизводимые условия повторов и переполнения.

    destinations = tuple(
        Destination(destination_id=str(index), channel_factory=lambda channel=channel: channel,
                    retry=policy or RetryPolicy(), outstanding_capacity=capacity)
        for index, channel in enumerate(channels)
    )
    config = WatcherConfig(
        identity=identity, destinations=destinations,
        routes=(Route(destination_ids=tuple(item.destination_id for item in destinations)),),
        runtime=RuntimeConfig(shutdown_timeout=0.5),
    )
    runtime = NotificationRuntime(config, delivery_clock=clock, random_source=random_source)
    logger = logging.Logger("test.reliability", level=logging.DEBUG)
    logger.addHandler(runtime.handler)
    return runtime, logger
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ожидание завершения принятых доставок через тестовый барьер
#------------------------------------------------------------------------------------------------------------------
def finish_deliveries(
    runtime: NotificationRuntime,
) -> None:

    """Wait for admitted deliveries through a finite test barrier without stopping runtime.

    :param runtime: Running runtime with cooperative fake channels.
    :type runtime: NotificationRuntime
    """

    # runtime - worker, чьи очереди должны завершиться до проверки статистики.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Ожидание маршрутизации и освобождения очередей получателей
    #--------------------------------------------------------------------------------------------------------------
    async def barrier() -> None:

        """Wait until routing and all delivery queues have completed."""

        while True:
            with runtime._lock:
                if not runtime._ingress:
                    break

            await asyncio.sleep(0)

        await asyncio.gather(*(state.queue.join() for state in runtime._destinations.values()))
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run_coroutine_threadsafe(barrier(), runtime._loop).result(timeout=2)
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение идентификаторов и увеличение номера попытки
#------------------------------------------------------------------------------------------------------------------
def test_retry_ids_and_full_jitter(
    identity: Identity,
) -> None:

    """Keep event and delivery identity stable while attempts advance and clocks are virtual.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - приложение; две неудачи завершаются успешной третьей попыткой.

    temporary = DeliveryResult(status=DeliveryStatus.TRANSIENT_FAILURE)
    channel = ScriptedChannel((temporary, temporary))
    clock = VirtualClock()
    runtime, logger = make_runtime(identity, (channel,), clock=clock, random_source=lambda: 0.5)

    with runtime:
        logger.error("retry me")
        finish_deliveries(runtime)

    assert clock.sleeps == [0.5, 1.0]
    assert [item.attempt for item in channel.deliveries] == [1, 2, 3]
    assert len({item.delivery_id for item in channel.deliveries}) == 1
    assert len({id(item.notification) for item in channel.deliveries}) == 1
    assert runtime.stats().attempts == 3 and runtime.stats().retries == 2
    assert runtime.stats("0").accepted == 1 and runtime.stats("0").failed_attempts == 2
    assert runtime.stats("0").admitted == 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Различение окончательного отказа и исчерпания попыток
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("failure", [DeliveryStatus.UNKNOWN, DeliveryStatus.PERMANENT_FAILURE,
                                     DeliveryStatus.TRANSIENT_FAILURE, DeliveryStatus.RATE_LIMITED])
def test_terminal_results(
    identity: Identity,
    failure: DeliveryStatus,
) -> None:

    """Distinguish permanent failure from exhaustion of bounded retryable outcomes.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param failure: Repeated provider status.
    :type failure: DeliveryStatus
    """

    # identity, failure - источник и повторяющийся ответ сервиса.

    channel = ScriptedChannel((DeliveryResult(status=failure),) * 3)
    runtime, logger = make_runtime(identity, (channel,), clock=VirtualClock())

    with runtime:
        logger.error("fail")
        finish_deliveries(runtime)

    permanent = failure is DeliveryStatus.PERMANENT_FAILURE
    assert len(channel.deliveries) == (1 if permanent else 3)
    assert runtime.stats().permanent_failure == int(permanent)
    assert runtime.stats().exhausted == int(not permanent)
    assert runtime.stats().unknown_attempts == (3 if failure is DeliveryStatus.UNKNOWN else 0)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Нижняя граница retry-after и общий срок доставки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("retry_after", "expected_calls", "expected_sleeps"), [(4.0, 2, [4.0]), (5.0, 1, [])])
def test_retry_after_and_expiry(
    identity: Identity,
    retry_after: float,
    expected_calls: int,
    expected_sleeps: list[float],
) -> None:

    """Honor retry-after without scheduling an attempt at or beyond expiry.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param retry_after: Provider minimum delay.
    :type retry_after: float

    :param expected_calls: Expected attempt count.
    :type expected_calls: int

    :param expected_sleeps: Expected virtual retry waits.
    :type expected_sleeps: list[float]
    """

    # identity, retry_after, expected_calls, expected_sleeps - сценарий и ожидаемые попытки/задержки.

    clock = VirtualClock()
    channel = ScriptedChannel((DeliveryResult(status=DeliveryStatus.RATE_LIMITED, retry_after=retry_after),))
    runtime, logger = make_runtime(identity, (channel,), clock=clock, policy=RetryPolicy(ttl=5, backoff_cap=2))

    with runtime:
        logger.error("rate limit")
        finish_deliveries(runtime)

    assert len(channel.deliveries) == expected_calls and clock.sleeps == expected_sleeps
    assert runtime.stats().expired == int(expected_calls == 1)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Повторы после неизвестного исхода и нарушения контракта адаптера
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("failure", [RuntimeError("synthetic-secret"), asyncio.CancelledError(), None])
def test_adapter_error_can_retry(
    identity: Identity,
    failure: object,
) -> None:

    """Treat exceptions, spontaneous cancellation and malformed replies as retryable unknowns.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param failure: Invalid adapter outcome.
    :type failure: object
    """

    # identity, failure - приложение и нарушение контракта первой попытки.

    channel = ScriptedChannel((failure,))
    runtime, logger = make_runtime(identity, (channel,), clock=VirtualClock())

    with runtime:
        logger.error("recover")
        finish_deliveries(runtime)

    assert runtime.stats().unknown_attempts == runtime.stats().adapter_errors == 1
    assert runtime.stats().accepted == runtime.stats().retries == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение слота при ожидании и независимость получателей
#------------------------------------------------------------------------------------------------------------------
def test_retry_keeps_capacity_and_neighbor_progress(
    identity: Identity,
) -> None:

    """Retain an outstanding slot during retry wait without delaying a healthy destination.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - приложение с одним ожидающим повтором и независимым здоровым получателем.

    waiting = ScriptedChannel((DeliveryResult(status=DeliveryStatus.UNKNOWN),))
    healthy = ScriptedChannel()
    clock = PausedClock()
    runtime, logger = make_runtime(identity, (waiting, healthy), clock=clock, capacity=1)

    with runtime:
        logger.error("first")
        assert clock.waiting.wait(2) and healthy.sent.wait(2)
        asyncio.run_coroutine_threadsafe(runtime._destinations["1"].queue.join(), runtime._loop).result(2)
        healthy.sent.clear()
        logger.error("second")
        assert healthy.sent.wait(2)
        assert runtime._destinations["0"].outstanding == 1

    assert len(healthy.deliveries) == 2 and len(waiting.deliveries) == 1
    assert runtime.stats("0").destination_overflow == 1
    assert runtime.stats("0").shutdown_discarded_retries == 1
    assert runtime.stats().shutdown_unknown == 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отмена асинхронного запуска с последующим закрытием
#------------------------------------------------------------------------------------------------------------------
def test_async_context_and_cancelled_start(
    identity: Identity,
) -> None:

    """Keep the application loop responsive and clean up a repeatedly cancelled start.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - приложение; open ждёт явного разрешения из работающего loop приложения.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Асинхронный тестовый сценарий
    #--------------------------------------------------------------------------------------------------------------
    async def exercise() -> None:

        """Exercise async context ownership and cancellation with deterministic barriers."""

        channel = ScriptedChannel()
        runtime, logger = make_runtime(identity, (channel,))
        application_loop = asyncio.get_running_loop()

        async with runtime:
            logger.error("async application")

        assert channel.loop is not application_loop and runtime.state is RuntimeState.CLOSED

        channel = ScriptedChannel()
        channel.block_open = True
        runtime, _ = make_runtime(identity, (channel,))
        task = asyncio.create_task(runtime.astart())
        assert await asyncio.to_thread(channel.opened.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        channel.loop.call_soon_threadsafe(channel.gate.set)

        with pytest.raises(asyncio.CancelledError):
            await task

        assert channel.closed and runtime.state is RuntimeState.CLOSED
        assert not runtime._thread.is_alive()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(exercise())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Порядок событий и изоляция ошибки генератора задержек
#------------------------------------------------------------------------------------------------------------------
def test_retry_order_and_scheduler_error(
    identity: Identity,
) -> None:

    """Keep retries before the next queued event and recover from a bad random source.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - источник двух событий; первое должно завершить повторы раньше второго.

    channel = ScriptedChannel((DeliveryResult(status=DeliveryStatus.TRANSIENT_FAILURE),))
    runtime, logger = make_runtime(identity, (channel,), clock=VirtualClock())

    with runtime:
        logger.error("first")
        logger.error("second")
        finish_deliveries(runtime)

    assert [item.notification.message for item in channel.deliveries] == ["first", "first", "second"]
    channel = ScriptedChannel((DeliveryResult(status=DeliveryStatus.UNKNOWN),))
    runtime, logger = make_runtime(identity, (channel,), clock=VirtualClock(), random_source=lambda: float("nan"))

    with runtime:
        logger.error("bad jitter")
        logger.error("next event")
        finish_deliveries(runtime)

    assert runtime.stats().scheduler_errors == 1 and runtime.stats().accepted == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Явный отказ остановки при заблокированном чужим кодом loop
#------------------------------------------------------------------------------------------------------------------
def test_blocking_adapter_exceeds_stop_budget(
    identity: Identity,
) -> None:

    """Report FAILED when a foreign adapter blocks the loop instead of claiming successful stop.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - приложение; нарушение async-контракта удерживается только тестовым барьером.

    channel = ScriptedChannel()
    release = threading.Event()

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Контролируемое нарушение асинхронного контракта адаптера
    #--------------------------------------------------------------------------------------------------------------
    async def blocked_send(
        delivery: Delivery,
    ) -> DeliveryResult:

        """Deliberately block the worker to verify the caller's bounded stop wait.

        :param delivery: Current attempt.
        :type delivery: Delivery

        :return: Acceptance after the test releases the foreign blocking operation.
        :rtype: DeliveryResult
        """

        # delivery - активная попытка; синхронное ожидание здесь намеренно нарушает контракт адаптера.

        channel.sent.set()
        assert release.wait(3)
        return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)
    #--------------------------------------------------------------------------------------------------------------

    channel.send = blocked_send
    runtime, logger = make_runtime(identity, (channel,))
    runtime.start()

    try:
        logger.error("blocking send")
        assert channel.sent.wait(2)

        with pytest.raises(TimeoutError, match="did not stop"):
            runtime.stop()

        assert runtime.state is RuntimeState.FAILED
    finally:
        release.set()
        runtime._thread.join(2)

    assert not runtime._thread.is_alive()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Закрытие runtime при завершении отдельного процесса
#------------------------------------------------------------------------------------------------------------------
def test_atexit_cleanup(
    tmp_path: Path,
) -> None:

    """Close an omitted explicit-stop runtime at normal process exit without network activity.

    :param tmp_path: Temporary directory for a child-process cleanup marker.
    :type tmp_path: pathlib.Path
    """

    # tmp_path - маркер закрытия создаётся отдельным процессом, чтобы не запускать atexit основного pytest.

    marker = tmp_path / "closed.txt"
    code = '''
from pathlib import Path
from remote_watch import Identity, Destination, NotificationRuntime, WatcherConfig
class Channel:
    async def open(self): pass
    async def send(self, delivery): raise AssertionError('not used')
    async def close(self): Path(MARKER).write_text('closed')
identity = Identity(service='test', environment='test', region='test', host='test', instance_id='one')
runtime = NotificationRuntime(WatcherConfig(identity=identity,
    destinations=(Destination(destination_id='one', channel_factory=Channel),)))
runtime.start()
'''.replace("MARKER", repr(str(marker)))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=5)
    assert result.returncode == 0 and not result.stderr
    assert marker.read_text() == "closed"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Завершение остановки при отмене ожидающего приложения
#------------------------------------------------------------------------------------------------------------------
def test_cancelled_async_stop(
    identity: Identity,
) -> None:

    """Finish owned shutdown before propagating cancellation of astop.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - приложение; активная попытка будет отменена после истечения срока обработки очереди.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Асинхронный тестовый сценарий
    #--------------------------------------------------------------------------------------------------------------
    async def exercise() -> None:

        """Cancel the waiting caller while the background stop continues."""

        channel = ScriptedChannel()
        channel.block_send = True
        runtime, logger = make_runtime(identity, (channel,))
        await runtime.astart()
        logger.error("active send")
        assert await asyncio.to_thread(channel.sent.wait, 2)
        task = asyncio.create_task(runtime.astop())

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Ожидание закрытия приёма без блокировки loop
        #----------------------------------------------------------------------------------------------------------
        async def wait_stopping() -> None:

            """Wait for the offloaded stop to close admission."""

            while runtime.state is RuntimeState.RUNNING:
                await asyncio.sleep(0)
        #----------------------------------------------------------------------------------------------------------

        await asyncio.wait_for(wait_stopping(), 2)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert runtime.state is RuntimeState.CLOSED and channel.closed
        assert runtime.stats().shutdown_unknown == 1
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(exercise())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничение задач и доставок при одновременных повторах
#------------------------------------------------------------------------------------------------------------------
def test_retry_burst_remains_bounded(
    identity: Identity,
) -> None:

    """Bound simultaneous retry waits, retained deliveries and task count during a burst.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - приложение с несколькими одновременно ожидающими повтора каналами.

    channels = tuple(ScriptedChannel((DeliveryResult(status=DeliveryStatus.TRANSIENT_FAILURE),)) for _ in range(4))
    runtime, logger = make_runtime(identity, channels, clock=PausedClock(), capacity=2)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка числа задач и незавершённых доставок
    #--------------------------------------------------------------------------------------------------------------
    async def inspect_bound() -> tuple[int, tuple[int, ...]]:

        """Observe the bound after the admitted burst has been routed.

        :return: Task count and outstanding deliveries by destination.
        :rtype: tuple[int, tuple[int, ...]]
        """

        while True:
            with runtime._lock:
                ready = not runtime._ingress and runtime._counts["retry_waits"] == len(channels)

            if ready:
                outstanding = tuple(state.outstanding for state in runtime._destinations.values())
                return len(asyncio.all_tasks()), outstanding

            await asyncio.sleep(0)
    #--------------------------------------------------------------------------------------------------------------

    with runtime:
        for _ in range(20):
            logger.error("initial burst")

        before = asyncio.run_coroutine_threadsafe(inspect_bound(), runtime._loop).result(2)

        for _ in range(40):
            logger.error("continued burst")

        after = asyncio.run_coroutine_threadsafe(inspect_bound(), runtime._loop).result(2)
        assert before == after
        assert after[0] <= 3 * len(channels) + 4
        assert after[1] == (2,) * len(channels)

    assert runtime.stats().shutdown_discarded_retries == len(channels)
    assert runtime.stats().shutdown_discarded_deliveries == len(channels)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Асинхронный запрос остановки из рабочего канала
#------------------------------------------------------------------------------------------------------------------
def test_astop_from_channel(
    identity: Identity,
) -> None:

    """Request async shutdown from a channel without offloading a self-join.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - приложение; astop вызывается внутри loop доставки, а не loop приложения.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Асинхронный тестовый сценарий
    #--------------------------------------------------------------------------------------------------------------
    async def exercise() -> None:

        """Run a channel-owned stop request and observe successful cleanup."""

        channel = ScriptedChannel()
        runtime, logger = make_runtime(identity, (channel,))
        original = channel.send

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Завершение активной отправки после запроса остановки
        #----------------------------------------------------------------------------------------------------------
        async def stop_and_send(
            delivery: Delivery,
        ) -> DeliveryResult:

            """Request stop and return the active attempt result.

            :param delivery: Active attempt.
            :type delivery: Delivery

            :return: Successful channel result.
            :rtype: DeliveryResult
            """

            # delivery - попытка, завершение которой ещё успевает войти в обработку очереди при остановке.

            await runtime.astop()
            return await original(delivery)
        #----------------------------------------------------------------------------------------------------------

        channel.send = stop_and_send

        async with runtime:
            logger.error("stop from worker")
            assert await asyncio.to_thread(channel.sent.wait, 2)

        assert runtime.state is RuntimeState.CLOSED and channel.closed
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(exercise())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль tests/contract/test_reliability.py не предназначен для прямого запуска. Используйте pytest.',
    )
#------------------------------------------------------------------------------------------------------------------
