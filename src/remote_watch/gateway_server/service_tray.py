# Отдельный интерактивный controller службы, не владеющий gateway и журналами команд.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-124120
#
# Функции:
# -> tray_settings(): Чтение только выбранных полей UI без связанных конфигов.
# -> run_tray(): SCM-наблюдение и запрос stop без запуска manager.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import ctypes
import hashlib
import os
import threading
from pathlib import Path

from remote_watch.commands.source_store import ProcessLock

from .config import _read_json
from .processes import private_directory
from .service import ServiceSpec, current_sid
from .service_windows import WindowsService
from .tray import GatewayTray


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение только выбранных полей UI без связанных конфигов
#------------------------------------------------------------------------------------------------------------------
def tray_settings(path: Path) -> tuple[bool, Path | None]:

    """Read only UI preferences without resolving credentials or opening runtime state.

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
# ФУНКЦИЯ : SCM-наблюдение и запрос stop без запуска manager
#------------------------------------------------------------------------------------------------------------------
def run_tray(spec: ServiceSpec) -> int:

    """Observe one service in the operator's interactive session without becoming a gateway owner.

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
    tray = GatewayTray(stop.set, icon_path=icon, exit_callback=close.set,
                       title_prefix="Remote Watch service: ")
    try:
        backend = WindowsService(spec)
        tray.start()
        while not close.is_set() and tray.ready:
            config_error = False
            try:
                if not tray_settings(spec.config)[0]:
                    break
            except Exception:
                # Не закрываем исправный UI, если JSON временно не читается
                # во время редактирования: Stop должен остаться доступным.
                config_error = True
            try:
                if stop.is_set():
                    stop.clear()
                    backend.stop()
                state = backend.status()["state"]
            except Exception:
                state = "unavailable"
            tray.update("configuration_error" if config_error else state)
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
