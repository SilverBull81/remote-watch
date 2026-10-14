# Отдельный интерактивный controller службы, не владеющий gateway и журналами команд.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-164146
#
# Функции:
# -> launch_tray(): Запрос запуска контроллера в сеансе выбранного оператора
#
# -> tray_settings(): Чтение только выбранных полей UI без связанных конфигов
#
# -> run_tray(): Наблюдение за службой и запрос остановки без запуска второго сервера


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import threading
from pathlib import Path

from remote_watch.commands.source_store import ProcessLock

from .config import _read_json
from .processes import private_directory
from .service import ServiceSpec, current_sid
from .service_windows import WindowsService
from .tray import GatewayTray

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрос запуска контроллера в сеансе выбранного оператора
#------------------------------------------------------------------------------------------------------------------
def launch_tray(
    spec: ServiceSpec,
    ) -> str:

    """
    Request a hidden controller from the operator's start CLI without crossing user sessions.

    :param spec: Exact service and operator binding already accepted by SCM.
    :type spec: ServiceSpec

    :return: Fixed launch-request status; never a claim that the GUI or gateways are ready.
    :rtype: str
    """

    # spec — служба не получает право создавать GUI чужого пользователя. Только
    # CLI самого оператора может запустить его контроллер в текущем сеансе.

    if not tray_settings(spec.config)[0]:
        return "headless"

    session = ctypes.c_uint32()
    if (os.name != "nt" or current_sid() != spec.operator_sid
            or not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
            or session.value == 0):
        return "operator_session_required"

    subprocess.Popen(spec.arguments("tray"), cwd=spec.config.parent, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return "launch_requested"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение только выбранных полей UI без связанных конфигов
#------------------------------------------------------------------------------------------------------------------
def tray_settings(
    path: Path,
    ) -> tuple[bool, Path | None]:

    """
    Read only UI preferences without resolving credentials or opening runtime state.

    :param path: Existing deployment config, not a component configuration.
    :type path: Path

    :return: Enabled flag and optional absolute icon path.
    :rtype: tuple[bool, Path | None]
    """

    # path — изменение режима не требует рестарта службы. Контроллер не проверяет
    # notification/command конфиги и не требует работоспособности провайдеров.

    root = _read_json(path)
    if type(root) is not dict or type(root.get("application", {})) is not dict:
        raise ValueError("tray_config")

    app = root.get("application", {})
    ui = app.get("ui", "headless")
    if ui not in ("tray", "headless"):
        raise ValueError("tray_config")

    icon = app.get("icon_path")
    if icon is not None and (type(icon) is not str or not icon.strip() or "\x00" in icon):
        raise ValueError("tray_config")

    return ui == "tray", (path.parent / icon).resolve() if icon else None
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Наблюдение за службой и запрос остановки без запуска второго сервера
#------------------------------------------------------------------------------------------------------------------
def run_tray(
    spec: ServiceSpec,
    ) -> int:

    """
    Observe one service in the operator's interactive session without becoming a gateway owner.

    :param spec: Exact service binding registered for the current operator.
    :type spec: ServiceSpec

    :return: Zero after disabled UI or requested controller exit.
    :rtype: int
    """

    # spec — пароль, control token, application Identity и журналы не нужны GUI.

    enabled, icon = tray_settings(spec.config)
    if not enabled:
        return 0

    if current_sid() != spec.operator_sid:
        raise ValueError("operator_sid")

    session = ctypes.c_uint32()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)) or session.value == 0:
        raise ValueError("tray_requires_session")

    root = Path(os.environ["LOCALAPPDATA"]) / "RemoteWatch" / "tray"
    private_directory(root)
    digest = hashlib.sha256(str(spec.config).casefold().encode()).hexdigest()[:24]
    owner = ProcessLock(root / f"{spec.name}-{session.value}-{digest}.lock")
    try:
        owner.open()
    except Exception:
        # Второй запуск иконки в том же сеансе не создаёт второй controller.
        return 0

    close = threading.Event()
    stop = threading.Event()
    tray = GatewayTray(stop.set, icon_path=icon, title_prefix="Remote Watch service: ")
    try:
        backend = WindowsService(spec)
        tray.start(visible=False)
        while not close.is_set() and tray.ready:
            config_error = False
            try:
                enabled = tray_settings(spec.config)[0]
            except Exception:
                # Не закрываем исправный UI, если JSON временно не читается
                # во время редактирования: Stop должен остаться доступным.
                config_error = True
            try:
                if stop.is_set():
                    stop.clear()
                    backend.stop()
                state = backend.status()["state"]
            except Exception as error:
                state = "not_found" if getattr(error, "winerror", None) == 1060 else "unavailable"
            tray.update("configuration_error" if config_error else state)
            # Скрываем именно иконку: оставшийся контроллер увидит следующий start
            # из CLI или services.msc. UI=headless также не останавливает службу.
            tray.set_visible(enabled and state not in ("stopped", "not_found"))
            close.wait(2)
        return 0
    finally:
        tray.close()
        owner.close()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль service_tray не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
