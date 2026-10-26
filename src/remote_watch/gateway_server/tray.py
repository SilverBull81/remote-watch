# Необязательная иконка Windows: сведения о gateway и общая команда остановки.
#
# Version 1.0.9
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261026-121655
#
# Классы:
# -> GatewayTray: Иконка состояния и команда общей остановки в Windows
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта.
#    Интерфейс:
#    -> start(): Подготовка ресурсов и запуск фоновой работы
#    -> set_visible(): Показ и скрытие иконки без завершения пользовательского контроллера
#    -> update(): Сохранение состояния без изменения постоянной подписи
#    -> ready(): Проверка живого цикла трея без обращения к GUI
#    -> close(): Освобождение только собственных ресурсов
#    Служебные методы:
#    -> _request_stop(): Передача команды трея в общий механизм остановки
#    -> _setup(): Подтверждение видимости иконки
#    -> _run(): Цикл сообщений Windows без вывода приватных исключений
#    -> _stop_icon(): Остановка GUI без вывода исключения backend
#
# Функции:
# -> default_icon(): Поиск стандартной иконки в исходном проекте либо установленном окружении


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from datetime import date
from importlib.metadata import distribution, version
from pathlib import Path

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Иконка состояния и команда общей остановки в Windows
#------------------------------------------------------------------------------------------------------------------
class GatewayTray:
    """
    Keep GUI ownership separate from the asyncio supervisor and optional on headless hosts.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        stop: Callable[[], None],
        icon_path: Path | None = None,
        ) -> None:

        """
        Retain only a thread-safe stop callback.

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
        self._visible = True
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка ресурсов и запуск фоновой работы
    #--------------------------------------------------------------------------------------------------------------
    def start(
        self,
        visible: bool = True,
        ) -> None:

        """
        Start the optional Windows UI without blocking its host console.

        :param visible: Initial icon visibility; a hidden controller still owns its GUI loop.
        :type visible: bool
        """

        # visible — служебный контроллер запускает скрытый цикл и показывает иконку
        # лишь после чтения SCM, чтобы при stopped не было краткой вспышки в трее.

        self._visible = visible
        if os.name != "nt":
            raise RuntimeError("tray_requires_windows")

        import pystray
        from PIL import Image

        # Ресурс поставляется в wheel: папка исходного проекта для трея не нужна.
        # convert загружает пиксели до закрытия файла; backend не удерживает дескриптор.
        source = self._icon_path or default_icon()
        with source.open("rb") as stream, Image.open(stream) as original:
            bitmap = original.convert("RGBA")

        # About — статическое подменю с версией и описанием, без отдельного окна
        # и без обращения к конфигам или сети из GUI-потока.
        about = pystray.Menu(
            pystray.MenuItem("Remote Watch Gateway (v." + version("remote-watch") + ")", None, enabled=False),
            pystray.MenuItem("Notifications and application commands.", None, enabled=False),
            pystray.MenuItem(f"(C) SilverBull, {date.today().year}", None, enabled=False),
        )
        items = [pystray.MenuItem("About", about),
                 pystray.MenuItem("Stop Server", self._request_stop)]
        self._icon = pystray.Icon("remote-watch-gateway", bitmap, "Remote Watch Gateway",
                                  menu=pystray.Menu(*items))
        self._thread = threading.Thread(target=self._run, name="gateway-tray", daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or not self._thread.is_alive():
            self.close()
            raise RuntimeError("tray_unavailable")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Показ и скрытие иконки без завершения пользовательского контроллера
    #--------------------------------------------------------------------------------------------------------------
    def set_visible(
        self,
        visible: bool,
        ) -> None:

        """
        Change icon visibility while retaining the single initialized GUI loop.

        :param visible: Whether the icon should be present in this user's tray.
        :type visible: bool
        """

        # visible — изменение не запускает manager и не отправляет команду службе.

        if visible != self._visible and self._icon is not None:
            try:
                self._icon.visible = visible
                self._visible = visible
            except Exception:
                self._failed = True
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Сохранение состояния без изменения постоянной подписи
    #--------------------------------------------------------------------------------------------------------------
    def update(
        self,
        state: str,
        ) -> None:

        """
        Retain observed lifecycle state without changing the static gateway tooltip.

        :param state: Sanitized manager lifecycle state.
        :type state: str
        """

        # state — состояние остаётся доступно контроллеру; постоянная подпись
        # иконки не выдаёт RUNNING службы за готовность всех каналов gateway.

        self._state = state
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка живого цикла трея без обращения к GUI
    #--------------------------------------------------------------------------------------------------------------
    @property
    def ready(
        self,
        ) -> bool:

        """
        Read cached GUI readiness without calling the native backend.

        :return: Whether the initialized tray thread is alive and has not failed.
        :rtype: bool
        """

        return self._ready.is_set() and not self._failed and self._thread is not None and self._thread.is_alive()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Освобождение только собственных ресурсов
    #--------------------------------------------------------------------------------------------------------------
    def close(
        self,
        ) -> None:

        """
        Stop the icon and bound its thread join.
        """

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

        """
        Dispatch the menu action without doing process work on the GUI thread.

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

        """
        Signal message-loop readiness after applying the requested initial visibility.

        :param icon: Initialized pystray icon.
        :type icon: object
        """

        # icon — подтверждение запуска GUI, не готовности gateway listeners.

        icon.visible = self._visible
        self._ready.set()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Цикл сообщений Windows без вывода приватных исключений
    #--------------------------------------------------------------------------------------------------------------
    def _run(
        self,
        ) -> None:

        """
        Run the Windows message loop without printing third-party exceptions.
        """

        try:
            self._icon.run(setup=self._setup)
        except Exception:
            self._failed = True
            self._ready.set()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Остановка GUI без вывода исключения backend
    #--------------------------------------------------------------------------------------------------------------
    def _stop_icon(
        self,
        ) -> None:

        """
        Keep a failing native stop operation away from the supervisor loop.
        """

        try:
            self._icon.stop()
        except Exception:
            self._failed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Поиск стандартной иконки в исходном проекте либо установленном окружении
#------------------------------------------------------------------------------------------------------------------
def default_icon() -> Path:

    """
    Locate the canonical checkout icon or its installed data-file copy.

    :return: Absolute icon path independent of the current working directory.
    :rtype: Path
    """

    # В checkout логотип хранится только в корне logo. В wheel setuptools переносит
    # этот же файл в каталог данных Python-окружения, не создавая копии внутри src.

    checkout = Path(__file__).resolve().parents[3]
    source = checkout / "logo" / "remote_watch.ico"
    if (checkout / "pyproject.toml").is_file() and source.is_file():
        return source

    # pip --target помещает data-files прямо под target, иногда без записи в RECORD.
    target = Path(__file__).resolve().parents[2] / "share" / "remote-watch" / "logo" / "remote_watch.ico"
    if target.is_file():
        return target

    # Для обычной установки и --user RECORD указывает фактическое размещение data-files.
    # sys.prefix здесь недостаточен: пользовательская установка может лежать вне venv.
    package = distribution("remote-watch")
    for entry in package.files or ():
        if entry.as_posix().endswith("share/remote-watch/logo/remote_watch.ico"):
            return Path(package.locate_file(entry)).resolve()

    raise FileNotFoundError("default_gateway_icon_missing")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tray не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
