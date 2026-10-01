# Проверки владения logger, локальными файлами и жизненным циклом RemoteWatcher.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Классы:
# -> MemoryChannel: Тестовый канал с управляемым запуском и закрытием.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска работы.
#    Интерфейс:
#    -> open(): Подготовка тестового канала.
#    -> send(): Одна попытка отправки и проверка ответа.
#    -> close(): Закрытие клиента и освобождение ресурсов.
#
# Тесты:
# -> configuration(): Настройки одного тестового получателя.
# -> test_owned_handlers(): Сохранение чужого logger и ротация собственного журнала.
# -> test_failure_rolls_back(): Откат при ошибке открытия файла или канала.
# -> test_async_context_and_cancelled_start(): Асинхронный контекст и отмена запуска.
# -> test_hierarchy_and_callback_lifecycle(): Иерархия logger и защита от взаимного ожидания.
# -> test_invalid_local_settings(): Проверка ограничений локального журнала.
# -> test_unstarted_and_named_logger(): Сохранение настроек именованного logger.
# -> test_closed_file_cannot_reopen(): Защита от запоздавшей записи после закрытия файла.
# -> test_slow_close_does_not_starve_other_channels(): Независимость закрытия разных каналов.
# -> test_cancelled_stop(): Завершение очистки после отмены ожидания остановки.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import io
import logging
import threading
from pathlib import Path

import pytest

from remote_watch import (
    ConsoleConfig,
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Identity,
    RemoteWatcher,
    RotatingFileConfig,
    Route,
    RuntimeConfig,
    RuntimeState,
    WatcherConfig,
)


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Тестовый канал с управляемым запуском и закрытием
#------------------------------------------------------------------------------------------------------------------
class MemoryChannel:
    """Capture delivery and coordinate startup without network or credentials."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Prepare deterministic synchronization points."""

        self.deliveries: list[Delivery] = []
        self.opened = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.fail = False
        self.closed = False
        self.block_close = False
        self.close_entered = threading.Event()
        self.close_release = threading.Event()
        self.close_release.set()
        self.callback = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка тестового канала
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Wait for the test to release startup."""

        self.opened.set()
        while not self.release.is_set():
            await asyncio.sleep(0.001)
        if self.fail:
            raise RuntimeError("synthetic startup error")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправки и проверка ответа
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Record an attempt and optionally call back into the owner.

        :param delivery: Immutable delivery attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        self.deliveries.append(delivery)
        if self.callback is not None:
            self.callback()
        return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие клиента и освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Record resource release."""

        self.close_entered.set()
        while not self.close_release.is_set():
            await asyncio.sleep(0.001)
        if self.block_close:
            await asyncio.Event().wait()
        self.closed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Настройки одного тестового получателя
#------------------------------------------------------------------------------------------------------------------
def configuration(
    identity: Identity,
    channel: MemoryChannel,
    ) -> WatcherConfig:

    """Configure one bounded destination and an ERROR route.

    :param identity: Explicit application identity fixture.
    :type identity: Identity

    :param channel: Fake delivery channel.
    :type channel: MemoryChannel

    :return: The value described by this operation.
    :rtype: WatcherConfig
    """

    # identity - заданные сведения о тестовом приложении.
    # channel - тестовый канал без сетевых соединений.

    return WatcherConfig(identity=identity,
                         destinations=(Destination(destination_id="phone", channel_factory=lambda: channel),),
                         routes=(Route(destination_ids=("phone",)),),
                         runtime=RuntimeConfig(startup_timeout=2, shutdown_timeout=2))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение чужого logger и ротация собственного журнала
#------------------------------------------------------------------------------------------------------------------
def test_owned_handlers(
    identity: Identity,
    tmp_path: Path,
    ) -> None:

    """Preserve foreign handlers and adapters while draining and rotating owned output.

    :param identity: Explicit application identity fixture.
    :type identity: Identity

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path
    """

    # identity - заданные сведения о тестовом приложении.
    # tmp_path - временный каталог теста.

    channel = MemoryChannel()
    console = io.StringIO()
    foreign_stream = io.StringIO()
    logger = logging.Logger("application", logging.DEBUG)
    foreign = logging.StreamHandler(foreign_stream)
    logger.addHandler(foreign)
    adapter = logging.LoggerAdapter(logger, {"topic": "health"})
    path = tmp_path / "application.log"
    watcher = RemoteWatcher(configuration(identity, channel), logger=adapter,
                            console=ConsoleConfig(stream=console),
                            file=RotatingFileConfig(path=path, max_bytes=180, backup_count=2))

    # Конструктор не меняет logger и не открывает файл. Повторный start не дублирует обработчики.
    assert watcher.logger is adapter
    assert logger.handlers == [foreign] and not path.exists()
    with watcher:
        watcher.start()
        assert len(logger.handlers) == 4
        for index in range(12):
            adapter.info("local entry %s", index)
        adapter.error("remote entry")
    watcher.stop()

    assert len(channel.deliveries) == 1
    assert channel.deliveries[0].notification.topic == "health"
    assert channel.closed and logger.handlers == [foreign]
    assert logger.level == logging.DEBUG and logger.propagate
    assert len(list(tmp_path.glob("application.log*"))) == 3
    assert "remote entry" in path.read_text(encoding="utf-8")
    assert not console.closed and not foreign_stream.closed and not foreign._closed
    adapter.error("after stop")
    assert "after stop" in foreign_stream.getvalue()
    assert "after stop" not in console.getvalue()
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        watcher.start()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Откат при ошибке открытия файла или канала
#------------------------------------------------------------------------------------------------------------------
def test_failure_rolls_back(
    identity: Identity,
    tmp_path: Path,
    ) -> None:

    """Clean up a failed channel or file initialization without mutating the caller logger.

    :param identity: Explicit application identity fixture.
    :type identity: Identity

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path
    """

    # identity - заданные сведения о тестовом приложении.
    # tmp_path - временный каталог теста.

    channel = MemoryChannel()
    channel.fail = True
    watcher = RemoteWatcher(configuration(identity, channel), file=RotatingFileConfig(path=tmp_path / "log"))
    with pytest.raises(RuntimeError):
        watcher.start()
    assert channel.closed and watcher.logger.handlers == []
    # На Windows переименование также проверяет, что дескриптор больше не открыт.
    (tmp_path / "log").rename(tmp_path / "closed")

    channel = MemoryChannel()
    watcher = RemoteWatcher(configuration(identity, channel),
                            file=RotatingFileConfig(path=tmp_path / "missing" / "log"))
    with pytest.raises(OSError):
        watcher.start()
    assert not channel.opened.is_set() and watcher.logger.handlers == []
    assert watcher.runtime.state is RuntimeState.CLOSED
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Асинхронный контекст и отмена запуска
#------------------------------------------------------------------------------------------------------------------
def test_async_context_and_cancelled_start(identity: Identity) -> None:

    """Keep the application loop responsive and undo cancelled startup, including repeated cancellation.

    :param identity: Explicit application identity fixture.
    :type identity: Identity
    """

    # identity - заданные сведения о тестовом приложении.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise successful and cancelled asynchronous ownership."""

        channel = MemoryChannel()
        watcher = RemoteWatcher(configuration(identity, channel))
        async with watcher:
            watcher.logger.error("async message")
        assert len(channel.deliveries) == 1 and channel.closed

        channel = MemoryChannel()
        channel.release.clear()
        watcher = RemoteWatcher(configuration(identity, channel))
        task = asyncio.create_task(watcher.astart())
        assert await asyncio.to_thread(channel.opened.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        channel.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert channel.closed and watcher.logger.handlers == []
        assert watcher.runtime.state is RuntimeState.CLOSED
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Иерархия logger и защита от взаимного ожидания
#------------------------------------------------------------------------------------------------------------------
def test_hierarchy_and_callback_lifecycle(identity: Identity) -> None:

    """Route propagated child records and reject callback lifecycle calls without deadlocks.

    :param identity: Explicit application identity fixture.
    :type identity: Identity
    """

    # identity - заданные сведения о тестовом приложении.

    channel = MemoryChannel()
    parent = logging.Logger("app", logging.DEBUG)
    child = logging.Logger("app.child")
    child.parent = parent
    watcher = RemoteWatcher(configuration(identity, channel), logger=parent)
    errors = []

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Попытка управления владельцем из канала
    #--------------------------------------------------------------------------------------------------------------
    def callback() -> None:

        """Attempt forbidden worker-owned lifecycle changes."""

        for operation in (watcher.start, watcher.stop):
            try:
                operation()
            except RuntimeError:
                errors.append(True)
    #--------------------------------------------------------------------------------------------------------------

    channel.callback = callback
    with watcher:
        child.error("child entry")
    assert len(channel.deliveries) == 1 and errors == [True, True]
    assert channel.deliveries[0].notification.logger_name == "app.child"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка ограничений локального журнала
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("settings", [{"max_bytes": 0}, {"backup_count": 0}, {"level": True}, {"path": ""}])
def test_invalid_local_settings(
    settings: dict[str, object],
    tmp_path: Path,
    ) -> None:

    """Reject unbounded rotation and malformed local configuration before side effects.

    :param settings: Client constructor settings.
    :type settings: dict[str, object]

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path
    """

    # settings - настройки создаваемого HTTP-клиента.
    # tmp_path - временный каталог теста.

    values = {"path": tmp_path / "log", **settings}
    with pytest.raises((ValueError, TypeError)):
        RotatingFileConfig(**values)
    assert not (tmp_path / "log").exists()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение настроек именованного logger
#------------------------------------------------------------------------------------------------------------------
def test_unstarted_and_named_logger(identity: Identity) -> None:

    """Reuse a standard named logger without changing its settings and close an unused watcher.

    :param identity: Explicit application identity fixture.
    :type identity: Identity
    """

    # identity - заданные сведения о тестовом приложении.

    logger = logging.getLogger(identity.service)
    before = (logger.level, logger.propagate, logger.handlers[:], logger.filters[:])
    watcher = RemoteWatcher(WatcherConfig(identity=identity))
    assert watcher.logger is logger
    assert before == (logger.level, logger.propagate, logger.handlers, logger.filters)
    watcher.stop()
    watcher.stop()
    with pytest.raises(RuntimeError):
        watcher.start()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Защита от запоздавшей записи после закрытия файла
#------------------------------------------------------------------------------------------------------------------
def test_closed_file_cannot_reopen(
    identity: Identity,
    tmp_path: Path,
    ) -> None:

    """Prevent a concurrent logger call that already selected the file handler from reopening it.

    :param identity: Explicit application identity fixture.
    :type identity: Identity

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path
    """

    # identity - заданные сведения о тестовом приложении.
    # tmp_path - временный каталог теста.

    path = tmp_path / "log"
    watcher = RemoteWatcher(WatcherConfig(identity=identity), file=RotatingFileConfig(path=path))
    with watcher:
        handler = watcher.logger.handlers[-1]
    record = logging.LogRecord("app", logging.ERROR, "", 0, "late entry", (), None)
    handler.handle(record)
    assert handler.stream is None and path.read_bytes() == b""
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимость закрытия разных каналов
#------------------------------------------------------------------------------------------------------------------
def test_slow_close_does_not_starve_other_channels(identity: Identity) -> None:

    """Give every independent channel a chance to close even when a peer exhausts its deadline.

    :param identity: Explicit application identity fixture.
    :type identity: Identity
    """

    # identity - заданные сведения о тестовом приложении.

    healthy = MemoryChannel()
    slow = MemoryChannel()
    slow.block_close = True
    config = WatcherConfig(identity=identity,
                           destinations=(Destination(destination_id="healthy", channel_factory=lambda: healthy),
                                         Destination(destination_id="slow", channel_factory=lambda: slow)),
                           runtime=RuntimeConfig(shutdown_timeout=0.5))
    with RemoteWatcher(config) as watcher:
        pass
    assert healthy.closed and not slow.closed
    assert watcher.runtime.stats("healthy").close_failed == 0
    assert watcher.runtime.stats("slow").close_failed == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Завершение очистки после отмены ожидания остановки
#------------------------------------------------------------------------------------------------------------------
def test_cancelled_stop(
    identity: Identity,
    ) -> None:

    """Finish channel cleanup despite repeated cancellation of asynchronous stop.

    :param identity: Explicit application identity fixture.
    :type identity: Identity
    """

    # identity - заданные сведения о тестовом приложении.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Управляемая отмена во время закрытия канала
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Cancel only after close has started, then release the channel."""

        channel = MemoryChannel()
        channel.close_release.clear()
        watcher = RemoteWatcher(configuration(identity, channel))
        await watcher.astart()

        task = asyncio.create_task(watcher.astop())
        assert await asyncio.to_thread(channel.close_entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        channel.close_release.set()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert channel.closed and watcher.runtime.state is RuntimeState.CLOSED
        assert not watcher.logger.handlers
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_watcher не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
