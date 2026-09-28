# Сквозные проверки logging, фоновой отправки, очередей и остановки без сети.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-222548
#
# Классы:
# -> RecordingChannel: Тестовый канал с управляемыми отказами и задержкой.
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта без запуска фоновой работы.
#    Интерфейс:
#    -> open(): Подготовка тестового клиента.
#    -> send(): Тестовая отправка с управляемым результатом.
#    -> close(): Закрытие тестового клиента.
#
# Функции:
# -> make_runtime(): Настройки фоновой отправки для тестовых каналов.
# -> logger(): Изолированный стандартный логгер для тестов.
# -> wait_for_routing(): Ожидание обработки входной очереди через тестовый барьер.
#
# Тесты:
# -> test_logging_end_to_end(): Совместная работа консоли, файла и двух каналов.
# -> test_local_survives_bad_metadata_and_formatting(): Локальное логирование при ошибках подготовки уведомления.
# -> test_slow_destination_and_shutdown(): Независимость получателей и отмена активной отправки.
# -> test_ingress_and_wakeups_are_bounded(): Ограничение входной очереди и числа пробуждений.
# -> test_adapter_failures_and_recursive_logging(): Изоляция отказов и защита двух runtime от рекурсии.
# -> test_lifecycle_and_open_failure(): Границы запуска и откат частично открытых клиентов.
# -> test_redaction_and_size_limits(): Удаление чувствительных данных и контроль размера.
# -> test_default_retry_configuration(): Применение стандартной политики повторов.
# -> test_levels_filters_and_hierarchy(): Стандартные уровни, фильтры и иерархия logging.
# -> test_internal_namespace_and_redactor_recursion(): Исключение внутреннего logging и рекурсии редактора.
# -> test_multiple_producers(): Учёт сообщений нескольких потоков без потери пробуждений.
# -> test_shutdown_counts_queued_deliveries(): Различение активной и ещё не начатой отправки при остановке.
# -> test_stop_from_channel(): Остановка из адаптера без ожидания собственного потока.
# -> test_startup_timeout_cleans_up(): Закрытие частично открытого клиента после таймаута запуска.
# -> test_expiry_and_attempt_timeout(): Истёкшее уведомление и ограничение времени попытки.
# -> test_no_worker_or_logging_changes_on_construction(): Отсутствие изменений logging и потоков при создании.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from remote_watch import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Identity,
    NotificationRuntime,
    RetryPolicy,
    Route,
    RuntimeConfig,
    RuntimeState,
    SnapshotLimits,
    WatcherConfig,
)

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Тестовый канал с управляемыми отказами и задержкой
#------------------------------------------------------------------------------------------------------------------
class RecordingChannel:
    """Record calls and provide a cooperatively blocked send for concurrency tests."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Prepare synchronization primitives without creating an event loop."""

        self.deliveries: list[Delivery] = []
        self.calls: list[tuple[str, int, asyncio.AbstractEventLoop]] = []
        self.entered = threading.Event()
        self.block = False
        self.error = False
        self.invalid_result = False
        self.cancel_self = False
        self.open_error = False
        self.closed = False
        self.cancelled = False
        self.logger: logging.Logger | None = None
        self.gate: asyncio.Event | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка тестового клиента
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Initialize all asynchronous primitives on the worker loop."""

        self.calls.append(("open", threading.get_ident(), asyncio.get_running_loop()))
        self.gate = asyncio.Event()

        if self.open_error:
            raise RuntimeError("synthetic-secret-in-open")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Тестовая отправка с управляемым результатом
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Record an attempt and optionally block, log recursively or fail.

        :param delivery: Delivery under test.
        :type delivery: Delivery

        :return: Successful acceptance unless the scenario requests failure.
        :rtype: DeliveryResult
        """

        # delivery - отправка, которую тест проверяет после завершения worker.

        self.calls.append(("send", threading.get_ident(), asyncio.get_running_loop()))
        self.deliveries.append(delivery)
        self.entered.set()

        if self.logger is not None:
            self.logger.error("channel diagnostic")

        if self.block:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

        if self.error:
            raise RuntimeError("synthetic-secret-in-send")

        if self.invalid_result:
            return None

        if self.cancel_self:
            raise asyncio.CancelledError

        return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие тестового клиента
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Record cleanup, including after a partially failed open."""

        self.calls.append(("close", threading.get_ident(), asyncio.get_running_loop()))
        self.closed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Настройки фоновой отправки для тестовых каналов
#------------------------------------------------------------------------------------------------------------------
def make_runtime(
    identity: Identity,
    channels: tuple[RecordingChannel, ...],
    *,
    capacity: int = 16,
    timeout: float = 1.0,
    ) -> NotificationRuntime:

    """Configure single-attempt fake destinations with bounded queues.

    :param identity: Explicit application identity.
    :type identity: Identity

    :param channels: Independently owned fake channels.
    :type channels: tuple[RecordingChannel, ...]

    :param capacity: Queue and outstanding capacity.
    :type capacity: int

    :param timeout: Shutdown budget in seconds.
    :type timeout: float

    :return: Unstarted runtime using a deterministic UTC clock.
    :rtype: NotificationRuntime
    """

    # identity, channels - приложение и тестовые каналы без сети.
    # capacity, timeout - ограничения для сценариев переполнения и остановки.

    destinations = tuple(
        Destination(destination_id=str(index), channel_factory=lambda channel=channel: channel,
                    outstanding_capacity=capacity, retry=RetryPolicy(max_attempts=1))
        for index, channel in enumerate(channels)
    )
    config = WatcherConfig(
        identity=identity, destinations=destinations,
        routes=(Route(destination_ids=tuple(item.destination_id for item in destinations)),),
        runtime=RuntimeConfig(ingress_capacity=capacity, shutdown_timeout=timeout),
    )
    return NotificationRuntime(config, clock=lambda: datetime(2026, 9, 28, tzinfo=timezone.utc))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Изолированный стандартный логгер для тестов
#------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def logger() -> Iterator[logging.Logger]:

    """Create an isolated standard logger and close its handlers after the test.

    :return: Isolated logging fixture.
    :rtype: Iterator[logging.Logger]
    """

    result = logging.Logger("test.runtime", level=logging.DEBUG)

    try:
        yield result
    finally:
        for handler in result.handlers[:]:
            result.removeHandler(handler)
            handler.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ожидание обработки входной очереди через тестовый барьер
#------------------------------------------------------------------------------------------------------------------
def wait_for_routing(
    runtime: NotificationRuntime,
    ) -> None:

    """Synchronize a test with the worker after already admitted events are dispatched.

    :param runtime: Running runtime under test.
    :type runtime: NotificationRuntime
    """

    # runtime - тестируемый worker; барьер обрабатывается после пробуждения входной очереди.

    #--------------------------------------------------------------------------------------------------------------
    # МЕТОД : Проверка опустошения входной очереди
    #--------------------------------------------------------------------------------------------------------------
    async def barrier() -> None:

        """Yield until the ingress is empty, with a finite outer test deadline."""

        while True:
            with runtime._lock:
                if not runtime._ingress:
                    return

            await asyncio.sleep(0)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run_coroutine_threadsafe(barrier(), runtime._loop).result(timeout=2)
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Совместная работа консоли, файла и двух каналов
#------------------------------------------------------------------------------------------------------------------
def test_logging_end_to_end(
    identity: Identity,
    logger: logging.Logger,
    tmp_path: Path,
    ) -> None:

    """Preserve console/file logging while a LoggerAdapter reaches two channels.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger

    :param tmp_path: Temporary test directory.
    :type tmp_path: pathlib.Path
    """

    # identity, logger, tmp_path - приложение, логгер и каталог локального журнала.

    channels = (RecordingChannel(), RecordingChannel())
    runtime = make_runtime(identity, channels)
    console = io.StringIO()
    local = logging.StreamHandler(console)
    file_handler = logging.FileHandler(tmp_path / "application.log", encoding="utf-8")
    logger.addHandler(local)
    logger.addHandler(file_handler)
    logger.addHandler(runtime.handler)
    logger.addHandler(runtime.handler)
    before = logger.handlers[:]

    with runtime:
        logger.info("local only")
        adapter = logging.LoggerAdapter(logger, {"notify": True, "instance_id": "forged", "tags": ["urgent"]})
        adapter.info("remote %s", "too")

    assert logger.handlers == before
    assert console.getvalue() == "local only\nremote too\n"
    file_handler.flush()
    assert (tmp_path / "application.log").read_text(encoding="utf-8") == console.getvalue()
    assert runtime.state is RuntimeState.CLOSED
    assert runtime.stats().accepted == 2

    for channel in channels:
        assert channel.closed and len(channel.deliveries) == 1
        event = channel.deliveries[0].notification
        assert event.message == "remote too" and event.identity is identity
        assert event.tags == ("urgent",)
        assert len({call[1] for call in channel.calls}) == 1
        assert channel.calls[0][1] != threading.get_ident()
        assert len({id(call[2]) for call in channel.calls}) == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Локальное логирование при ошибках подготовки уведомления
#------------------------------------------------------------------------------------------------------------------
def test_local_survives_bad_metadata_and_formatting(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Contain normalization failures and continue delivery of later valid records.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - приложение и логгер; локальный вывод подключаем после удалённого handler.

    channel = RecordingChannel()
    runtime = make_runtime(identity, (channel,))
    stream = io.StringIO()
    logger.addHandler(runtime.handler)
    logger.addHandler(logging.StreamHandler(stream))

    with runtime:
        logger.error("local survives", extra={"tags": "invalid"})
        logger.error("wrong flag", extra={"notify": 1})
        runtime.handler.handle(logging.LogRecord(None, 40, "", 0, "invalid logger name", (), None))
        runtime.handler.setFormatter(logging.Formatter("%(missing_field)s"))
        logger.error("format failure")
        runtime.handler.setFormatter(None)
        logger.error("valid")

    assert runtime.stats().normalization_failed == 4
    assert "local survives" in stream.getvalue() and "format failure" in stream.getvalue()
    assert [item.notification.message for item in channel.deliveries] == ["valid"]
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимость получателей и отмена активной отправки
#------------------------------------------------------------------------------------------------------------------
def test_slow_destination_and_shutdown(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Keep a healthy destination independent and count a cancelled send as unknown.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - тестовый источник уведомлений.

    slow, healthy = RecordingChannel(), RecordingChannel()
    slow.block = True
    runtime = make_runtime(identity, (slow, healthy), capacity=1, timeout=0.5)
    logger.addHandler(runtime.handler)
    runtime.start()

    try:
        logger.error("first")
        assert slow.entered.wait(2) and healthy.entered.wait(2)
        wait_for_routing(runtime)
        # Барьер окончания первой быстрой отправки освобождает её единственный слот.
        asyncio.run_coroutine_threadsafe(runtime._destinations["1"].queue.join(), runtime._loop).result(2)
        logger.error("second")
        wait_for_routing(runtime)
    finally:
        runtime.stop()

    assert [item.notification.message for item in healthy.deliveries] == ["first", "second"]
    assert runtime.stats().destination_overflow == 1
    assert runtime.stats().shutdown_unknown == 1
    assert slow.cancelled and slow.closed and healthy.closed
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничение входной очереди и числа пробуждений
#------------------------------------------------------------------------------------------------------------------
def test_ingress_and_wakeups_are_bounded(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Fill ingress while a controlled worker barrier prevents consumer progress.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - источник; блокировка loop здесь намеренная и ограничена тестовым барьером.

    runtime = make_runtime(identity, (RecordingChannel(),), capacity=2)
    logger.addHandler(runtime.handler)
    blocked, release = threading.Event(), threading.Event()

    #--------------------------------------------------------------------------------------------------------------
    # МЕТОД : Остановка consumer на время контролируемого заполнения очереди
    #--------------------------------------------------------------------------------------------------------------
    def hold_loop() -> None:

        """Hold the loop only until the test has submitted a bounded burst."""

        blocked.set()
        assert release.wait(2)
    #--------------------------------------------------------------------------------------------------------------

    with runtime:
        runtime._loop.call_soon_threadsafe(hold_loop)

        try:
            assert blocked.wait(2)

            for index in range(50):
                logger.error("event %d", index)

            assert runtime.stats().admitted == 2
            assert runtime.stats().ingress_overflow == 48
            assert runtime._wake_pending
            # Помимо ограниченной очереди проверяем именно очередь callback-ов asyncio.
            callbacks = [handle for handle in runtime._loop._ready if handle._callback == runtime._wake]
            assert len(callbacks) == 1
        finally:
            release.set()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Изоляция отказов и защита двух runtime от рекурсии
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("failure", ["raise", "invalid", "cancel"])
def test_adapter_failures_and_recursive_logging(
    identity: Identity,
    logger: logging.Logger,
    failure: str,
    ) -> None:

    """Contain adapter failures and prevent channel logs from feeding another runtime.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger

    :param failure: Requested adapter failure mode.
    :type failure: str
    """

    # identity, logger, failure - приложение, общий логгер и вид отказа адаптера.

    bad, good = RecordingChannel(), RecordingChannel()
    bad.logger = logger
    bad.error = failure == "raise"
    bad.invalid_result = failure == "invalid"
    bad.cancel_self = failure == "cancel"
    first = make_runtime(identity, (bad,))
    second = make_runtime(identity, (good,))
    logger.addHandler(first.handler)
    logger.addHandler(second.handler)

    with first, second:
        logger.error("application event")
        logger.error("next event")

    assert len(bad.deliveries) == len(good.deliveries) == 2
    assert first.stats().unknown_attempts == first.stats().adapter_errors == 2
    assert first.stats().suppressed == second.stats().suppressed == 2
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Границы запуска и откат частично открытых клиентов
#------------------------------------------------------------------------------------------------------------------
def test_lifecycle_and_open_failure(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Handle inactive logging, idempotent lifecycle and rollback after a failed open.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - приложение и логгер для проверки границ запуска.

    runtime = make_runtime(identity, (RecordingChannel(),))
    logger.addHandler(runtime.handler)
    logger.error("before start")
    runtime.start()
    runtime.start()
    runtime.stop()
    runtime.stop()
    logger.error("after stop")
    assert runtime.stats().not_running == 2

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        runtime.start()

    stopped = make_runtime(identity, (RecordingChannel(),))
    stopped.stop()
    assert stopped.state is RuntimeState.CLOSED and stopped._thread is None
    good, bad = RecordingChannel(), RecordingChannel()
    bad.open_error = True
    failed = make_runtime(identity, (good, bad))

    with pytest.raises(RuntimeError, match="failed to start") as captured:
        failed.start()

    failed.stop()
    assert "synthetic-secret" not in str(captured.value)
    assert good.closed and bad.closed and failed.state is RuntimeState.FAILED
    assert failed.stats().startup_failed == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Удаление чувствительных данных и контроль размера
#------------------------------------------------------------------------------------------------------------------
def test_redaction_and_size_limits(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Redact before truncation and reject metadata exceeding the total budget.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - источник сообщений с искусственным секретом.

    channel = RecordingChannel()
    base = make_runtime(identity, (channel,)).config
    limits = SnapshotLimits(message_max_bytes=18, exception_max_bytes=32)
    config = replace(base, runtime=replace(base.runtime, snapshot_limits=limits))
    runtime = NotificationRuntime(config, redactor=lambda text: text.replace("secret", "[removed]"))
    logger.addHandler(runtime.handler)

    with runtime:
        logger.error("secret %s", "Пример" * 40)
        logger.error("metadata too large", extra={"tags": [str(index) + "x" * 240 for index in range(20)]})

    event = channel.deliveries[0].notification
    assert "secret" not in event.message and "[removed]" in event.message
    assert event.truncated_fields == ("message",)
    assert len(event.message.encode("utf-8")) <= 18
    assert runtime.stats().normalization_failed == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Применение стандартной политики повторов
#------------------------------------------------------------------------------------------------------------------
def test_default_retry_configuration(
    identity: Identity,
    ) -> None:

    """Accept the default retry policy once retry scheduling is available.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - сведения приложения; стандартная политика теперь применяется runtime.

    config = WatcherConfig(
        identity=identity,
        destinations=(Destination(destination_id="one", channel_factory=RecordingChannel),),
    )

    runtime = NotificationRuntime(config)
    assert runtime.config.destinations[0].retry.max_attempts == 3
    runtime.stop()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Стандартные уровни, фильтры и иерархия logging
#------------------------------------------------------------------------------------------------------------------
def test_levels_filters_and_hierarchy(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Respect standard logger filtering and preserve parent propagation.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated parent logger.
    :type logger: logging.Logger
    """

    # identity, logger - приложение и родительский логгер с удалённым обработчиком.

    channel = RecordingChannel()
    runtime = make_runtime(identity, (channel,))
    child = logging.Logger("test.runtime.child", level=logging.ERROR)
    child.parent = logger
    logger.addHandler(runtime.handler)

    with runtime:
        child.info("logger threshold", extra={"notify": True})
        child.error("explicitly disabled", extra={"notify": False})
        child.addFilter(lambda record: record.msg != "filtered")
        child.error("filtered")
        child.error("accepted")
        child.propagate = False
        child.addHandler(logging.NullHandler())
        child.error("no propagation")

    assert [item.notification.message for item in channel.deliveries] == ["accepted"]
    assert child.level == logging.ERROR and len(child.filters) == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Исключение внутреннего logging и рекурсии редактора
#------------------------------------------------------------------------------------------------------------------
def test_internal_namespace_and_redactor_recursion(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Suppress internal names and recursive records emitted during normalization.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - приложение и логгер, который также вызывает редактор текста.

    channel = RecordingChannel()

    #--------------------------------------------------------------------------------------------------------------
    # МЕТОД : Проверка логирования из редактора текста
    #--------------------------------------------------------------------------------------------------------------
    def redact(
        text: str,
        ) -> str:

        """Log once from the redactor without recursive notification delivery.

        :param text: Source text.
        :type text: str

        :return: Unchanged text.
        :rtype: str
        """

        # text - текст, обработка которого намеренно создаёт ещё одну запись logging.

        logger.error("redactor diagnostic")
        return text
    #--------------------------------------------------------------------------------------------------------------

    runtime = NotificationRuntime(make_runtime(identity, (channel,)).config, redactor=redact)
    logger.addHandler(runtime.handler)

    with runtime:
        for name in ("remote_watch.internal", "remote_watch.internal.http"):
            runtime.handler.handle(logging.LogRecord(name, 50, "", 0, "internal", (), None))

        logger.error("application")

    assert len(channel.deliveries) == 1 and runtime.stats().suppressed == 3
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Учёт сообщений нескольких потоков без потери пробуждений
#------------------------------------------------------------------------------------------------------------------
def test_multiple_producers(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Keep concurrent producers accounted for without losing wakeups.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - общий источник для нескольких реальных потоков.

    channel = RecordingChannel()
    runtime = make_runtime(identity, (channel,), capacity=128)
    logger.addHandler(runtime.handler)
    barrier = threading.Barrier(5, timeout=2)

    #--------------------------------------------------------------------------------------------------------------
    # МЕТОД : Порция сообщений из отдельного потока
    #--------------------------------------------------------------------------------------------------------------
    def produce() -> None:

        """Submit a small burst after all producer threads are ready."""

        barrier.wait()

        for index in range(20):
            logger.error("event %d", index)
    #--------------------------------------------------------------------------------------------------------------

    threads = [threading.Thread(target=produce) for _ in range(4)]

    with runtime:
        for thread in threads:
            thread.start()

        barrier.wait()

        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()

    assert runtime.stats().admitted == runtime.stats().accepted == 80
    assert len({item.delivery_id for item in channel.deliveries}) == 80
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Различение активной и ещё не начатой отправки при остановке
#------------------------------------------------------------------------------------------------------------------
def test_shutdown_counts_queued_deliveries(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Distinguish a cancelled active attempt from work that never started.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - источник; первая отправка удерживает очередь до остановки.

    channel = RecordingChannel()
    channel.block = True
    runtime = make_runtime(identity, (channel,), capacity=3, timeout=0.5)
    logger.addHandler(runtime.handler)

    with runtime:
        logger.error("active")
        assert channel.entered.wait(2)
        logger.error("queued one")
        logger.error("queued two")
        wait_for_routing(runtime)

    assert runtime.stats().shutdown_unknown == 1
    assert runtime.stats().shutdown_discarded_deliveries == 2
    assert runtime.stats().routed == 3
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Остановка из адаптера без ожидания собственного потока
#------------------------------------------------------------------------------------------------------------------
def test_stop_from_channel(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Allow a channel to request shutdown without joining its own worker.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - источник для сценария остановки из пользовательского адаптера.

    channel = RecordingChannel()
    runtime = make_runtime(identity, (channel,))
    original = channel.send

    #--------------------------------------------------------------------------------------------------------------
    # МЕТОД : Запрос остановки из адаптера
    #--------------------------------------------------------------------------------------------------------------
    async def send_and_stop(
        delivery: Delivery,
        ) -> DeliveryResult:

        """Request stop from the worker and finish the active send.

        :param delivery: Current delivery.
        :type delivery: Delivery

        :return: Channel response.
        :rtype: DeliveryResult
        """

        # delivery - активная отправка, из которой запрашивается остановка.

        runtime.stop()
        return await original(delivery)
    #--------------------------------------------------------------------------------------------------------------

    channel.send = send_and_stop
    logger.addHandler(runtime.handler)

    with runtime:
        logger.error("stop request")
        assert channel.entered.wait(2)

    assert runtime.state is RuntimeState.CLOSED and channel.closed
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Закрытие частично открытого клиента после таймаута запуска
#------------------------------------------------------------------------------------------------------------------
def test_startup_timeout_cleans_up(
    identity: Identity,
    ) -> None:

    """Cancel a cooperative open and retain time for closing the partial client.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - приложение для проверки таймаута запуска без сетевого соединения.

    channel = RecordingChannel()
    config = make_runtime(identity, (channel,)).config
    # Проверяем отмену зависшего open, а не точность планировщика ОС на границе десятков миллисекунд.
    # При прежних 0.2 с резерв очистки составлял лишь 40 мс, что делало тест нестабильным под нагрузкой.
    config = replace(config, runtime=replace(config.runtime, startup_timeout=1.0))
    open_started = threading.Event()

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Ожидание отмены по сроку запуска
    #--------------------------------------------------------------------------------------------------------------
    async def blocked_open() -> None:

        """Wait indefinitely until the startup deadline cancels initialization."""

        open_started.set()
        await asyncio.Event().wait()
    #--------------------------------------------------------------------------------------------------------------

    channel.open = blocked_open
    runtime = NotificationRuntime(config)

    with pytest.raises(RuntimeError, match="failed to start"):
        runtime.start()

    runtime.stop()
    assert open_started.is_set()
    assert channel.closed and runtime.state is RuntimeState.FAILED
    assert not runtime._thread.is_alive()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Истёкшее уведомление и ограничение времени попытки
#------------------------------------------------------------------------------------------------------------------
def test_expiry_and_attempt_timeout(
    identity: Identity,
    logger: logging.Logger,
    ) -> None:

    """Skip an already expired event and cancel a cooperative send at its attempt deadline.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param logger: Isolated logger fixture.
    :type logger: logging.Logger
    """

    # identity, logger - источник; прошлый монотонный срок задаём явно, без ожидания истечения TTL.

    channel = RecordingChannel()
    channel.block = True
    config = make_runtime(identity, (channel,)).config
    retry = RetryPolicy(max_attempts=1, connect_timeout=0.01, attempt_timeout=0.05)
    config = replace(config, destinations=(replace(config.destinations[0], retry=retry),))
    runtime = NotificationRuntime(config)
    logger.addHandler(runtime.handler)

    with runtime:
        logger.error("attempt timeout")
        assert channel.entered.wait(2)
        asyncio.run_coroutine_threadsafe(runtime._destinations["0"].queue.join(), runtime._loop).result(2)
        old = channel.deliveries[0].notification
        runtime._submit(old, time.monotonic() - 1000)

    assert len(channel.deliveries) == 1 and channel.cancelled
    assert runtime.stats().unknown_attempts == 1
    assert runtime.stats().expired == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отсутствие изменений logging и потоков при создании
#------------------------------------------------------------------------------------------------------------------
def test_no_worker_or_logging_changes_on_construction(
    identity: Identity,
    ) -> None:

    """Leave global logging and application event loops untouched during construction.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - сведения о приложении; создание runtime не должно создавать поток или loop.

    threads = set(threading.enumerate())
    handlers = logging.getLogger().handlers[:]
    runtime = make_runtime(identity, (RecordingChannel(),))
    assert set(threading.enumerate()) == threads
    assert logging.getLogger().handlers == handlers
    assert runtime._loop is None
    runtime.stop()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль tests/contract/test_runtime.py не предназначен для прямого запуска. Используйте pytest.',
    )
#------------------------------------------------------------------------------------------------------------------
