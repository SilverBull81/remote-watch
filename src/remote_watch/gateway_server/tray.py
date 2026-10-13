# Необязательная иконка Windows: состояние manager и общая команда остановки.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261013-124741
#
# Класс:
# -> GatewayTray: Иконка состояния и команда общей остановки в Windows.
#    Конструктор:
#    -> __init__(): Создание объекта без запуска рабочих ресурсов.
#    Интерфейс:
#    -> start(): Подготовка ресурсов и запуск фоновой работы.
#    -> update(): Обновление подписи иконки по состоянию сервера.
#    -> ready(): Проверка живого цикла трея без обращения к GUI.
#    -> close(): Освобождение только собственных ресурсов.
#    Служебные методы:
#    -> _request_stop(): Передача команды трея в общий механизм остановки.
#    -> _setup(): Подтверждение видимости иконки.
#    -> _run(): Цикл сообщений Windows без вывода приватных исключений.
#    -> _stop_icon(): Остановка GUI без вывода исключения backend.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Иконка состояния и команда общей остановки в Windows
#------------------------------------------------------------------------------------------------------------------
class GatewayTray:

    """Keep GUI ownership separate from the asyncio supervisor and optional on headless hosts."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        stop: Callable[[], None],
        icon_path: Path | None = None,
    ) -> None:

        """Retain only a thread-safe stop callback.

        :param stop: Schedule common manager shutdown from the tray thread.
        :type stop: Callable[[], None]

        :param icon_path: Custom local ICO or PNG file, or None for the packaged icon.
        :type icon_path: Path | None
        """

        # stop — иконка не убивает PID; остановка совпадает с CLI и сигналом ОС.
        # icon_path — файл читается только при явном запуске GUI.
        self._stop = stop
        self._icon_path = icon_path
        self._icon = None
        self._thread = None
        self._ready = threading.Event()
        self._state = "starting"
        self._failed = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка ресурсов и запуск фоновой работы
    #--------------------------------------------------------------------------------------------------------------
    def start(self) -> None:

        """Start the optional Windows UI without blocking its host console."""

        if os.name != "nt":
            raise RuntimeError("tray_requires_windows")
        import pystray
        from PIL import Image

        # Ресурс поставляется в wheel: папка исходного проекта для трея не нужна.
        # convert загружает пиксели до закрытия файла; backend не удерживает дескриптор.
        source = self._icon_path or files("remote_watch.gateway_server").joinpath("assets/remote_watch.ico")
        with source.open("rb") as stream, Image.open(stream) as original:
            bitmap = original.convert("RGBA")

        self._icon = pystray.Icon("remote-watch-gateway", bitmap, "Remote Watch: starting",
                                  menu=pystray.Menu(pystray.MenuItem("Stop gateway server", self._request_stop)))
        self._thread = threading.Thread(target=self._run, name="gateway-tray", daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or not self._thread.is_alive():
            self.close()
            raise RuntimeError("tray_unavailable")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Обновление подписи иконки по состоянию сервера
    #--------------------------------------------------------------------------------------------------------------
    def update(
        self,
        state: str,
    ) -> None:

        """Update the tooltip from fixed supervisor state only.

        :param state: Sanitized manager lifecycle state.
        :type state: str
        """

        # state — никакие идентификаторы чатов или токены в UI не передаются.
        if state != self._state and self._icon is not None:
            try:
                self._icon.title = "Remote Watch: " + state
                self._state = state
            except Exception:
                self._failed = True
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка живого цикла трея без обращения к GUI
    #--------------------------------------------------------------------------------------------------------------
    @property
    def ready(self) -> bool:

        """Read cached GUI readiness without calling the native backend.

        :return: Whether the initialized tray thread is alive and has not failed.
        :rtype: bool
        """

        return self._ready.is_set() and not self._failed and self._thread is not None and self._thread.is_alive()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Освобождение только собственных ресурсов
    #--------------------------------------------------------------------------------------------------------------
    def close(self) -> None:

        """Stop the icon and bound its thread join."""

        if self._icon is not None:
            # Неисправный GUI не должен удерживать остановку серверных процессов.
            stopper = threading.Thread(target=self._stop_icon, name="gateway-tray-stop", daemon=True)
            stopper.start()
            stopper.join(2)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(1)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Передача команды трея в общий механизм остановки
    #--------------------------------------------------------------------------------------------------------------
    def _request_stop(
        self,
        icon: object,
        item: object,
    ) -> None:

        """Dispatch the menu action without doing process work on the GUI thread.

        :param icon: pystray callback argument.
        :type icon: object

        :param item: Selected menu item.
        :type item: object
        """

        # icon/item — содержимое меню фиксировано; общий stop безопасен из другого потока.
        self._stop()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Подтверждение видимости иконки
    #--------------------------------------------------------------------------------------------------------------
    def _setup(
        self,
        icon: object,
    ) -> None:

        """Signal readiness only after the icon becomes visible.

        :param icon: Initialized pystray icon.
        :type icon: object
        """

        # icon — подтверждение запуска GUI, не готовности gateway listeners.
        icon.visible = True
        self._ready.set()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Цикл сообщений Windows без вывода приватных исключений
    #--------------------------------------------------------------------------------------------------------------
    def _run(self) -> None:

        """Run the Windows message loop without printing third-party exceptions."""

        try:
            self._icon.run(setup=self._setup)
        except Exception:
            self._failed = True
            self._ready.set()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Остановка GUI без вывода исключения backend
    #--------------------------------------------------------------------------------------------------------------
    def _stop_icon(self) -> None:

        """Keep a failing native stop operation away from the supervisor loop."""

        try:
            self._icon.stop()
        except Exception:
            self._failed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tray не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
