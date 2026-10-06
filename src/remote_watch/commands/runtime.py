# Подключение командного dispatcher к синхронному и асинхронному приложению.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-163320
#
# Классы:
# -> CommandRuntime: Владение циклом команд при выбранном режиме запуска.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> start(): Запуск объекта и подготовка состояния.
#    -> astart(): Запуск с привязкой async callbacks к циклу приложения.
#    -> stop(): Остановка приёма команд и принадлежащих объекту ресурсов.
#    -> astop(): Асинхронная остановка командного исполнителя.
#    Служебные методы:
#    -> _startup_tls(): Безопасная TLS-причина в верхней ошибке запуска.
#    -> _serve(): Работа отдельного командного цикла синхронного приложения.
#
# Функции:
# -> _startup_cause(): Безопасная классификация причины отказа командного потока.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import sqlite3
import ssl
import threading

from remote_watch._validation import require_number
from remote_watch.commands.dispatcher import CommandDispatcher, command_context, is_async
from remote_watch.commands.storage import StoreError
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Владение циклом команд при выбранном режиме запуска
#------------------------------------------------------------------------------------------------------------------
class CommandRuntime:
    """Bridge explicit watcher lifecycle to application-loop or synchronous command ownership."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        dispatcher: CommandDispatcher,
        *,
        startup_timeout: float = 10.0,
    ) -> None:

        """Retain the dispatcher without creating threads or an event loop.

        :param dispatcher: Owned executor with a fixed application registry.
        :type dispatcher: CommandDispatcher

        :param startup_timeout: Maximum synchronous startup wait, seconds.
        :type startup_timeout: float
        """

        # dispatcher — исполнитель с неизменяемым реестром приложения.
        # startup_timeout — конечное ожидание синхронного запуска, секунды.

        require_number(startup_timeout, "command startup timeout")

        if startup_timeout > 30:
            raise ValueError("invalid command startup timeout")
        self.dispatcher = dispatcher
        self._startup_timeout = startup_timeout
        self._mode: str | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._error: BaseException | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск объекта и подготовка состояния
    #--------------------------------------------------------------------------------------------------------------
    def start(self) -> None:

        """Start a dedicated command loop only for a registry containing synchronous callbacks."""

        if command_context.get():
            raise RuntimeError("command lifecycle cannot run inside a callback")

        if any(is_async(spec.callback) for spec in self.dispatcher.registry.specs):
            raise ValueError("async command callbacks require watcher.astart on the application loop")

        if self._mode == "sync" and not self._stopping.is_set():
            if not self._ready.wait(self._startup_timeout) or self._error is not None:
                raise CommandError("unavailable", **self._startup_tls()) from self._error
            return

        if self._mode is not None or self._stopping.is_set():
            raise CommandError("closed")
        self._mode = "sync"
        self._thread = threading.Thread(target=self._serve, name="remote-watch-commands", daemon=True)
        self._thread.start()

        if not self._ready.wait(self._startup_timeout) or self._error is not None:
            self.stop()
            raise CommandError("unavailable", **self._startup_tls()) from self._error
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск с привязкой async callbacks к циклу приложения
    #--------------------------------------------------------------------------------------------------------------
    async def astart(self) -> None:

        """Bind all asynchronous callbacks to the loop calling this method."""

        if self._mode == "async" and not self._stopping.is_set():
            if asyncio.get_running_loop() is not self._loop:
                raise RuntimeError("command runtime belongs to another event loop")
            if not self._ready.is_set():
                raise CommandError("busy")
            return

        if self._mode is not None or self._stopping.is_set():
            raise CommandError("closed")
        self._mode = "async"
        self._loop = asyncio.get_running_loop()
        await self.dispatcher.start()
        self._ready.set()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка приёма команд и принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    def stop(self) -> None:

        """Request bounded shutdown of the synchronous owner's loop."""

        if command_context.get():
            raise RuntimeError("command lifecycle cannot run inside a callback")

        if self._mode == "async":
            raise RuntimeError("use watcher.astop for application-loop commands")
        self._stopping.set()

        if self._loop is not None and self._stop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                # Цикл мог закрыться после проверки: флаг остановки уже выставлен.
                if not self._loop.is_closed():
                    raise

        if self._thread is not None:
            self._thread.join(self.dispatcher._shutdown_timeout + 1)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Асинхронная остановка командного исполнителя
    #--------------------------------------------------------------------------------------------------------------
    async def astop(self) -> None:

        """Close the dispatcher on its application loop or offload a synchronous stop."""

        if self._mode == "sync":
            await asyncio.to_thread(self.stop)
            return

        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("command runtime belongs to another event loop")
        self._stopping.set()
        await self.dispatcher.close()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Безопасная TLS-причина в верхней ошибке запуска
    #--------------------------------------------------------------------------------------------------------------
    def _startup_tls(self) -> dict[str, str | int | None]:

        """Expose sanitized TLS details on the outer synchronous startup error.

        :return: Bounded metadata copied from the already sanitized worker failure.
        :rtype: dict[str, str | int | None]
        """

        # Причина уже очищена в рабочем потоке; конструктор снова проверит поля.
        if type(self._error) is CommandError:
            return {"error_kind": self._error.error_kind, "verify_code": self._error.verify_code}
        return {}
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Работа отдельного командного цикла синхронного приложения
    #--------------------------------------------------------------------------------------------------------------
    def _serve(self) -> None:

        """Own network and heartbeat work independently of the notification runtime."""


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Запуск dispatcher и обработка остановки во время старта
        #----------------------------------------------------------------------------------------------------------
        async def serve() -> None:

            """Open the dispatcher and handle a stop requested during startup."""

            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()

            try:
                await self.dispatcher.start()
                self._ready.set()
                if not self._stopping.is_set():
                    await self._stop.wait()
            finally:
                await self.dispatcher.close()
        #----------------------------------------------------------------------------------------------------------


        try:
            asyncio.run(serve())
        except BaseException as error:
            # Через границу потоков переносим только безопасную классификацию.
            # Исходный traceback удерживает объекты приложения, а его текст может
            # содержать секреты даже при безопасном сообщении верхнего CommandError.
            self._error = _startup_cause(error)
        finally:
            self._ready.set()
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Безопасная классификация причины отказа командного потока
#------------------------------------------------------------------------------------------------------------------
def _startup_cause(error: BaseException) -> BaseException:

    """Copy only a known failure category without retaining the original exception.

    :param error: Worker failure whose text, attributes and traceback must remain private.
    :type error: BaseException

    :return: Fresh exception with a fixed message and no original exception chain.
    :rtype: BaseException
    """

    # error — исходная ошибка; не копируем args, notes, пути, URL или имя зависимости.

    if type(error) is CommandError:
        # Даже публичные атрибуты могли быть изменены пользовательским transport.
        code = error.code if type(error.code) is str else "unavailable"
        return CommandError(code, http_status=error.http_status,
                            error_kind=error.error_kind, verify_code=error.verify_code)

    # Порядок важен: частные подклассы проверяются раньше общих OSError/ImportError.
    # Созданные исключения ещё не выбрасывались и не содержат traceback или context.
    categories = (
        (ModuleNotFoundError, "command dependency unavailable"),
        (ImportError, "command dependency import failed"),
        (ssl.SSLCertVerificationError, "command TLS certificate verification failed"),
        (ssl.SSLError, "command TLS failed"),
        (TimeoutError, "command startup timed out"),
        (ConnectionError, "command connection failed"),
        (sqlite3.Error, "command storage failed"),
        (StoreError, "command storage failed"),
        (OSError, "command operating system operation failed"),
        (ValueError, "command configuration invalid"),
    )
    for category, message in categories:
        if isinstance(error, category):
            return category(message)

    return RuntimeError("command startup failed")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль runtime не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
