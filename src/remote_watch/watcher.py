# Подключение фоновой доставки и локальных журналов к обычному logging.Logger.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Классы:
# -> ConsoleConfig: Настройки необязательного вывода в консоль.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек без открытия ресурсов.
#
# -> RotatingFileConfig: Настройки локального журнала с ротацией.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек без открытия ресурсов.
#
# -> _OwnedRotatingFileHandler: Файловый обработчик без повторного открытия после остановки.
#    Интерфейс:
#    -> emit(): Запись только в действующий файловый обработчик.
#
# -> RemoteWatcher: Владелец фоновой доставки и выбранных локальных обработчиков.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска работы.
#    Интерфейс:
#    -> start(): Создание обработчиков и запуск фоновой доставки.
#    -> astart(): Асинхронный запуск с откатом при отмене.
#    -> stop(): Снятие обработчиков и завершение принятой работы.
#    -> astop(): Асинхронная остановка с завершением очистки.
#    Специальные методы:
#    -> __enter__(): Запуск при входе в контекст.
#    -> __exit__(): Остановка при выходе из контекста.
#    -> __aenter__(): Асинхронный вход в контекст.
#    -> __aexit__(): Асинхронный выход из контекста.
#    Служебные методы:
#    -> _check_context(): Запрет управления lifecycle из внутренних callback.
#    -> _release_handlers(): Закрытие только принадлежащих объекту обработчиков.
#    -> _call(): Выполнение lifecycle вне цикла приложения.
#    -> _finish(): Ожидание очистки с учётом повторной отмены.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from remote_watch._validation import require_int
from remote_watch.config import WatcherConfig
from remote_watch.notifications._context import delivery_context
from remote_watch.notifications.runtime import NotificationRuntime


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки необязательного вывода в консоль
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class ConsoleConfig:
    """Configure an optional stderr or caller-owned stream handler."""

    level: int = logging.INFO                               # Минимальный уровень для консоли.
    format: str = "%(levelname)s %(name)s: %(message)s"     # Шаблон стандартного logging.Formatter.
    stream: TextIO | None = field(default=None, repr=False)     # Поток приложения; None выбирает stderr.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек без открытия ресурсов
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate settings without writing to the stream."""

        require_int(self.level, "level", 0)
        if not isinstance(self.format, str):
            raise TypeError("format must be a string")
        logging.Formatter(self.format)

        if self.stream is not None and not all(callable(getattr(self.stream, name, None))
                                               for name in ("write", "flush")):
            raise TypeError("stream must support write and flush")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки локального журнала с ротацией
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class RotatingFileConfig:
    """Configure a bounded rotating UTF-8 log; the parent directory must exist."""

    path: str | Path                                        # Путь к журналу, отдельному для процесса.
    level: int = logging.DEBUG                              # Минимальный уровень для файла.
    max_bytes: int = 10 * 1024 * 1024                       # Порог ротации основного журнала.
    backup_count: int = 3                                   # Число сохраняемых предыдущих файлов.
    format: str = "%(asctime)s %(levelname)s %(name)s: %(message)s"     # Шаблон строки журнала.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек без открытия ресурсов
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate rotation and freeze an absolute path without opening files."""

        if not isinstance(self.path, (str, Path)) or not str(self.path).strip():
            raise ValueError("path must be a nonempty string or Path")

        # Относительный путь относится к каталогу при настройке, а не при последующем запуске.
        object.__setattr__(self, "path", Path(self.path).absolute())
        require_int(self.level, "level", 0)
        require_int(self.max_bytes, "max_bytes")
        require_int(self.backup_count, "backup_count")
        if not isinstance(self.format, str):
            raise TypeError("format must be a string")
        logging.Formatter(self.format)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Файловый обработчик без повторного открытия после остановки
#------------------------------------------------------------------------------------------------------------------
class _OwnedRotatingFileHandler(RotatingFileHandler):
    """Prevent a late emit from reopening a closed append-mode file."""

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запись только в действующий файловый обработчик
    #--------------------------------------------------------------------------------------------------------------
    def emit(
        self,
        record: logging.LogRecord,
        ) -> None:

        """Write only while open; logging.Handler.handle already holds the handler lock.

        :param record: Standard logging record.
        :type record: logging.LogRecord
        """

        # record - запись стандартного logging.

        # close и handle используют один lock. Стандартный FileHandler в режиме append
        # иначе открыл бы файл снова, если logging успел выбрать handler перед его снятием.
        if not self._closed:
            super().emit(record)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Владелец фоновой доставки и выбранных локальных обработчиков
#------------------------------------------------------------------------------------------------------------------
class RemoteWatcher:
    """Own notification delivery and explicitly requested local handlers.

    Logging remains on logger, which is never replaced by a custom logging API.
    An existing logger keeps its level, propagation, filters and foreign handlers.
    Without one, logging.getLogger(service) supplies a standard named logger.
    Its effective level is inherited unless the application configures it explicitly.
    Lifecycle belongs to application code, never channel or redactor callbacks.
    Instances are single-use; start/stop are idempotent within their current phase.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: WatcherConfig,
        *,
        logger: logging.Logger | logging.LoggerAdapter | None = None,
        console: ConsoleConfig | None = None,
        file: RotatingFileConfig | None = None,
        redactor: Callable[[str], str] | None = None,
        ) -> None:

        """Prepare ownership without attaching handlers, opening files or starting threads.

        :param config: Validated application settings.
        :type config: WatcherConfig

        :param logger: Existing logger or adapter; None selects the service logger.
        :type logger: logging.Logger | logging.LoggerAdapter | None

        :param console: Optional console settings.
        :type console: ConsoleConfig | None

        :param file: Optional rotating file settings.
        :type file: RotatingFileConfig | None

        :param redactor: Optional remote text redactor.
        :type redactor: Callable[[str], str] | None
        """

        # config - настройки приложения и получателей.
        # logger - logger приложения или его адаптер.
        # console - настройки дополнительного вывода в консоль.
        # file - настройки дополнительного журнала.
        # redactor - функция удаления секретов из удалённого текста.

        if logger is not None and not isinstance(logger, (logging.Logger, logging.LoggerAdapter)):
            raise TypeError("logger must be Logger or LoggerAdapter")
        if console is not None and not isinstance(console, ConsoleConfig):
            raise TypeError("console must be ConsoleConfig")
        if file is not None and not isinstance(file, RotatingFileConfig):
            raise TypeError("file must be RotatingFileConfig")

        self.runtime = NotificationRuntime(config, redactor=redactor)
        self.logger = logger if logger is not None else logging.getLogger(config.identity.service)
        target = self.logger
        while isinstance(target, logging.LoggerAdapter):
            target = target.logger
        self._target = target
        self._console = console
        self._file = file
        self._handlers: list[logging.Handler] = []
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Создание обработчиков и запуск фоновой доставки
    #--------------------------------------------------------------------------------------------------------------
    def start(self) -> None:

        """Open local files, start delivery and attach owned handlers atomically for lifecycle callers."""

        self._check_context()
        with self._lock:
            if self._closed:
                raise RuntimeError("a closed watcher cannot be restarted")
            if self._started:
                return

            try:
                # Локальные ошибки пути выявляются до запуска сетевых клиентов.
                # Список владения заполняется сразу, чтобы откат закрывал даже частично созданный набор.
                self._handlers.append(self.runtime.handler)
                if self._console is not None:
                    handler = logging.StreamHandler(self._console.stream)
                    self._handlers.append(handler)
                    handler.setLevel(self._console.level)
                    handler.setFormatter(logging.Formatter(self._console.format))

                if self._file is not None:
                    handler = _OwnedRotatingFileHandler(self._file.path, maxBytes=self._file.max_bytes,
                                                        backupCount=self._file.backup_count,
                                                        encoding="utf-8", errors="backslashreplace")
                    self._handlers.append(handler)
                    handler.setLevel(self._file.level)
                    handler.setFormatter(logging.Formatter(self._file.format))

                self.runtime.start()
                for handler in self._handlers:
                    self._target.addHandler(handler)
                self._started = True
            except BaseException:
                self._closed = True
                try:
                    self.runtime.stop()
                finally:
                    self._release_handlers()
                raise
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Асинхронный запуск с откатом при отмене
    #--------------------------------------------------------------------------------------------------------------
    async def astart(self) -> None:

        """Start off the application loop and roll back if the caller cancels."""

        self._check_context()
        await self._call(self.start, stop_on_cancel=True)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Снятие обработчиков и завершение принятой работы
    #--------------------------------------------------------------------------------------------------------------
    def stop(self) -> None:

        """Detach owned handlers, drain delivery and close only owned local resources."""

        self._check_context()
        with self._lock:
            # Сначала прекращаем приём через logger, затем даём уже принятым сообщениям завершиться.
            for handler in self._handlers:
                self._target.removeHandler(handler)
            self._closed = True
            try:
                self.runtime.stop()
            finally:
                self._release_handlers()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Асинхронная остановка с завершением очистки
    #--------------------------------------------------------------------------------------------------------------
    async def astop(self) -> None:

        """Finish cleanup even if the awaiting application task is cancelled."""

        self._check_context()
        await self._call(self.stop, stop_on_cancel=False)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Запуск при входе в контекст
    #--------------------------------------------------------------------------------------------------------------
    def __enter__(self) -> RemoteWatcher:

        """Start and return the owner.

        :return: This running watcher.
        :rtype: RemoteWatcher
        """

        self.start()
        return self
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Остановка при выходе из контекста
    #--------------------------------------------------------------------------------------------------------------
    def __exit__(
        self,
        *args: object,
        ) -> None:

        """Stop on normal completion or an application exception.

        :param args: Context manager exception details.
        :type args: object
        """

        # args - сведения об исключении при выходе из контекста.

        self.stop()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Асинхронный вход в контекст
    #--------------------------------------------------------------------------------------------------------------
    async def __aenter__(self) -> RemoteWatcher:

        """Start without blocking the application loop.

        :return: This running watcher.
        :rtype: RemoteWatcher
        """

        await self.astart()
        return self
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Асинхронный выход из контекста
    #--------------------------------------------------------------------------------------------------------------
    async def __aexit__(
        self,
        *args: object,
        ) -> None:

        """Drain without blocking the application loop.

        :param args: Context manager exception details.
        :type args: object
        """

        # args - сведения об исключении при выходе из контекста.

        await self.astop()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Запрет управления lifecycle из внутренних callback
    #--------------------------------------------------------------------------------------------------------------
    def _check_context(self) -> None:

        """Reject lifecycle calls from delivery or normalization callbacks before locking."""

        # Такой вызов мог бы ждать поток, который сейчас выполняет сам callback.
        if delivery_context.get():
            raise RuntimeError("watcher lifecycle cannot be called from delivery or redactor callbacks")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Закрытие только принадлежащих объекту обработчиков
    #--------------------------------------------------------------------------------------------------------------
    def _release_handlers(self) -> None:

        """Detach and close owned handlers while holding the lifecycle lock."""

        first_error: Exception | None = None
        for handler in reversed(self._handlers):
            self._target.removeHandler(handler)
            # FileHandler.close использует тот же lock, что emit: текущая запись успеет завершиться.
            # StreamHandler.close не закрывает пользовательский поток или stderr.
            try:
                handler.close()
            except Exception as error:
                # Ошибка flush одного файла не должна оставить остальные ресурсы без закрытия.
                if first_error is None:
                    first_error = error
        self._handlers.clear()
        if first_error is not None:
            raise first_error
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Выполнение lifecycle вне цикла приложения
    #--------------------------------------------------------------------------------------------------------------
    async def _call(
        self,
        action: Callable[[], None],
        *,
        stop_on_cancel: bool,
        ) -> None:

        """Shield the finite synchronous lifecycle operation and complete cancellation cleanup.

        :param action: Synchronous lifecycle operation.
        :type action: Callable[[], None]

        :param stop_on_cancel: Whether cancelled startup requires stopping.
        :type stop_on_cancel: bool
        """

        # action - операция запуска или остановки.
        # stop_on_cancel - необходимость отката отменённого запуска.

        task = asyncio.create_task(asyncio.to_thread(action))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Отмена ожидания не останавливает to_thread. Дожидаемся операции, включая повторную отмену.
            await self._finish(task)
            if stop_on_cancel:
                await self._finish(asyncio.create_task(asyncio.to_thread(self.stop)))
            raise
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Ожидание очистки с учётом повторной отмены
    #--------------------------------------------------------------------------------------------------------------
    async def _finish(
        self,
        task: asyncio.Task[None],
        ) -> None:

        """Wait through repeated cancellation and consume a cleanup task's exception.

        :param task: Offloaded lifecycle task.
        :type task: asyncio.Task[None]
        """

        # task - задача ожидания операции в отдельном потоке.

        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Сохраняем CancelledError исходного вызова. Ошибка остановки видна также в runtime.state.
        if not task.cancelled():
            task.exception()
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.watcher не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
