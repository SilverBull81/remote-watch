# Ограниченные операции SCM, права виртуального аккаунта и автозапуск пользовательского UI.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-124120
#
# Класс:
# -> ServiceInstallError: Безопасная стадия частично завершённой установки.
#    Конструктор:
#    -> __init__(): Сохранение только разрешённой стадии и номера ошибки ОС.
#
# -> WindowsService: Управление только точно совпадающей службой deployment.
#    Конструктор:
#    -> __init__(): Загрузка optional API без изменения ОС.
#    Интерфейс:
#    -> status(): Безопасное состояние SCM, не готовность приложений.
#    -> start(): Запрос запуска проверенной службы.
#    -> stop(): Запрос мягкой остановки проверенной службы.
#    -> install(): Регистрация нового владельца с отдельными правами.
#    -> remove(): Удаление остановленной службы без удаления данных.
#    -> sync_tray(): Обновление только собственного HKCU Run оператора.
#    Служебные методы:
#    -> _open(): Открытие handle с проверкой binary/account.
#    -> _sync_entry(): Проверка и изменение только своей записи Run.
#    -> _grant(): Выдача прав только виртуальному SID на проверенный путь.
#
# Функции:
# -> managed(): Гарантированное закрытие native SCM handle.
# -> checked_tree(): Запрет рекурсивного ACL через reparse point.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from remote_watch.commands.source_store import ProcessLock

from .deployment import _path
from .service import ServicePlan, ServiceSpec


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Безопасная стадия частично завершённой установки
#------------------------------------------------------------------------------------------------------------------
class ServiceInstallError(ValueError):
    """Retain only a fixed installation phase and numeric OS error."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        stage: str,
        winerror: int | None = None,
    ) -> None:

        """Discard raw errors while retaining an actionable installation boundary.

        :param stage: Library-owned account/acl/security/recovery/tray/autostart phase.
        :type stage: str
        :param winerror: Numeric native failure, without its localized message.
        :type winerror: int | None
        """

        # stage/winerror — исходный путь, команда и текст исключения не сохраняются.
        self.stage = (stage if stage in ("account", "acl", "security", "recovery", "tray", "autostart")
                      else "unknown")
        self.winerror = winerror if type(winerror) is int and 0 <= winerror < 2**31 else None
        super().__init__("service_install_partial")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Гарантированное закрытие native SCM handle
#------------------------------------------------------------------------------------------------------------------
@contextmanager
def managed(handle: object) -> Iterator[object]:

    """Close a PySC_HANDLE even on an ownership or permission failure.

    :param handle: Owned pywin32 SCM handle; it is not a context manager itself.
    :type handle: object
    :return: Handle valid only during the enclosing operation.
    :rtype: Iterator[object]
    """

    # handle — PySC_HANDLE.Close отличается от Python file.close/context protocol.
    try:
        yield handle
    finally:
        handle.Close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрет рекурсивного ACL через reparse point
#------------------------------------------------------------------------------------------------------------------
def checked_tree(path: Path) -> None:

    """Check all existing descendants before a recursive permissions operation.

    :param path: Explicit environment or runtime directory, never an inferred disk root.
    :type path: Path
    """

    # path — followlinks=False недостаточно для всех Windows junction: проверяем
    # сам reparse attribute каждого потомка до вызова icacls с /T.
    _path(path)
    if not path.exists():
        return
    for directory, names, files in os.walk(path, followlinks=False):
        for name in (*names, *files):
            info = (Path(directory) / name).lstat()
            if getattr(info, "st_file_attributes", 0) & 0x400 or (Path(directory) / name).is_symlink():
                raise ValueError("service_linked_path")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Управление только точно совпадающей службой deployment
#------------------------------------------------------------------------------------------------------------------
class WindowsService:
    """Delegate only service query/start/stop and retain exact deployment ownership checks."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        spec: ServiceSpec,
    ) -> None:

        """Load Windows dependencies lazily without touching services or credentials.

        :param spec: Exact expected binary, account and operator binding.
        :type spec: ServiceSpec
        """

        # spec — разрешение управлять одним deployment не распространяется на Caddy.
        import win32security
        import win32service

        self.spec = spec
        self.api = win32service
        self.security = win32security
        self.binary = subprocess.list2cmdline(spec.arguments("run"))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Безопасное состояние SCM, не готовность приложений
    #--------------------------------------------------------------------------------------------------------------
    def status(self) -> dict:

        """Read SCM lifecycle without loading linked configs, tokens or journals.

        :return: Fixed state and numeric exit status only.
        :rtype: dict
        """

        with self._open(4) as handle:
            value = self.api.QueryServiceStatusEx(handle)
        states = {1: "stopped", 2: "starting", 3: "stopping", 4: "running", 5: "continuing",
                  6: "pausing", 7: "paused"}
        return {"state": states.get(value["CurrentState"], "unknown"),
                "win32_exit_code": value["Win32ExitCode"], "service_exit_code": value["ServiceSpecificExitCode"]}
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запрос запуска проверенной службы
    #--------------------------------------------------------------------------------------------------------------
    def start(self) -> None:

        """Request SCM startup without treating its acceptance as gateway readiness."""

        with self._open(16) as handle:
            self.api.StartService(handle, None)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запрос мягкой остановки проверенной службы
    #--------------------------------------------------------------------------------------------------------------
    def stop(self) -> None:

        """Request the service's common graceful shutdown path, never terminate by PID."""

        with self._open(32 | 4) as handle:
            if self.api.QueryServiceStatus(handle)[1] != 1:
                self.api.ControlService(handle, 1)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Регистрация нового владельца с отдельными правами
    #--------------------------------------------------------------------------------------------------------------
    def install(
        self,
        plan: ServicePlan,
    ) -> None:

        """Create a stopped passwordless service and only then permit automatic boot startup.

        :param plan: Fully validated original paths and bounded ACL targets.
        :type plan: ServicePlan
        """

        # plan — recursive ACL проверяется целиком до первой записи. Не открываем
        # SQLite; manager.lock нужен лишь для исключения действующего foreground owner.
        for directory in (plan.environment, *plan.write_dirs):
            checked_tree(directory)
        owner = ProcessLock(plan.deployment.control_dir / "manager.lock")
        try:
            owner.open()
        except Exception:
            raise ValueError("service_manager_running") from None
        try:
            with managed(self.api.OpenSCManager(None, None, 3)) as manager:
                try:
                    existing = self.api.OpenService(manager, self.spec.name, 1)
                except Exception as error:
                    if getattr(error, "winerror", None) != 1060:
                        raise
                else:
                    existing.Close()
                    raise ValueError("service_exists")
                with managed(self.api.CreateService(manager, self.spec.name, "Remote Watch Gateway", 0xF01FF,
                        16, 3, 1, self.binary, None, 0, None, "NT SERVICE\\" + self.spec.name, None)) as handle:
                    entry_changed = False
                    stage = "account"
                    try:
                        sid = self.security.LookupAccountName(None, "NT SERVICE\\" + self.spec.name)[0]
                        sid_text = self.security.ConvertSidToStringSid(sid)
                        # Доступ к коду — RX, к конфигам — R. Только runtime dirs
                        # получают F, поскольку manager защищает их DACL при запуске.
                        stage = "acl"
                        self._grant(plan.spec.config.parent, sid_text, "RX", False)
                        self._grant(plan.environment, sid_text, "(OI)(CI)RX", True)
                        for path in plan.read_files:
                            self._grant(path, sid_text, "RX", False)
                        for directory in plan.write_dirs:
                            directory.mkdir(parents=True, exist_ok=True)
                            self._grant(directory, sid_text, "(OI)(CI)F", True)
                        stage = "security"
                        descriptor = self.security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                            "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;CCLCRPWPLORC;;;" + self.spec.operator_sid + ")", 1)
                        self.api.SetServiceObjectSecurity(handle, 4, descriptor)
                        stage = "recovery"
                        self.api.ChangeServiceConfig2(handle, 2, {"ResetPeriod": 86400,
                            "RebootMsg": None, "Command": None, "Actions": [(1, 60000), (1, 300000), (0, 0)]})
                        # Configuration/startup failure с корректным STOPPED не
                        # считается crash. Последний NONE предотвращает вечный restart.
                        self.api.ChangeServiceConfig2(handle, 4, False)
                        self.api.ChangeServiceConfig2(handle, 7, int((plan.deployment.lifecycle.shutdown_timeout
                            + plan.deployment.lifecycle.kill_timeout + 15) * 1000))
                        stage = "tray"
                        entry_changed = self._sync_entry(plan.deployment.application.ui == "tray")
                        stage = "autostart"
                        self.api.ChangeServiceConfig(handle, -1, 2, -1, None, None, 0, None, None, None, None)
                    except Exception as error:
                        if entry_changed:
                            try:
                                self._sync_entry(False)
                            except Exception:
                                pass
                        self.api.DeleteService(handle)
                        raise ServiceInstallError(stage, getattr(error, "winerror", None)) from None
        finally:
            owner.close()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Удаление остановленной службы без удаления данных
    #--------------------------------------------------------------------------------------------------------------
    def remove(self) -> None:

        """Delete only the exact stopped service, preserving configs, journals, ACL and CA."""

        with self._open(0x10000 | 4) as handle:
            if self.api.QueryServiceStatus(handle)[1] != 1:
                raise ValueError("service_running")
            self._sync_entry(False)
            self.api.DeleteService(handle)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Обновление только собственного HKCU Run оператора
    #--------------------------------------------------------------------------------------------------------------
    def sync_tray(
        self,
        enabled: bool,
    ) -> None:

        """Apply the config's UI choice without restarting the service.

        :param enabled: Whether to register this operator's logon controller.
        :type enabled: bool
        """

        # enabled — прежде чем менять автозапуск, доказываем exact binding службы.
        with self._open(4):
            self._sync_entry(enabled)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка и изменение своей записи Run
    #--------------------------------------------------------------------------------------------------------------
    def _sync_entry(
        self,
        enabled: bool,
    ) -> bool:

        """Never replace another logon entry or register a SYSTEM interactive application.

        :param enabled: Desired state of the exact operator's Run value.
        :type enabled: bool
        :return: Whether this operation created its own new Run value.
        :rtype: bool
        """

        # enabled — используется загруженный hive выбранного пользователя. Вход
        # под другим admin account не должен тихо прописать трей не тому человеку.
        import winreg

        location = self.spec.operator_sid + r"\Software\Microsoft\Windows\CurrentVersion\Run"
        expected = subprocess.list2cmdline(self.spec.arguments("tray"))
        try:
            with winreg.OpenKey(winreg.HKEY_USERS, self.spec.operator_sid, 0, winreg.KEY_READ):
                pass
        except FileNotFoundError:
            if not enabled:
                return False
            raise
        with winreg.CreateKeyEx(winreg.HKEY_USERS, location, 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            try:
                existing, kind = winreg.QueryValueEx(key, self.spec.name)
            except FileNotFoundError:
                existing, kind = None, winreg.REG_SZ
            if existing is not None and (existing != expected or kind != winreg.REG_SZ):
                raise ValueError("service_tray_entry")
            if enabled:
                winreg.SetValueEx(key, self.spec.name, 0, winreg.REG_SZ, expected)
            elif existing is not None:
                winreg.DeleteValue(key, self.spec.name)
            return enabled and existing is None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Открытие handle с проверкой binary/account
    #--------------------------------------------------------------------------------------------------------------
    def _open(
        self,
        rights: int,
    ) -> object:

        """Refuse a same-name foreign service before sending any control.

        :param rights: Minimal additional access for the selected operation.
        :type rights: int
        :return: Caller-owned verified PySC_HANDLE context manager.
        :rtype: object
        """

        # rights — QUERY_CONFIG добавляется для exact проверки, CHANGE_CONFIG нет.
        with managed(self.api.OpenSCManager(None, None, 1)) as manager:
            handle = self.api.OpenService(manager, self.spec.name, rights | 1)
        try:
            config = self.api.QueryServiceConfig(handle)
            if config[0] != 16 or config[3] != self.binary or config[7].casefold() != (
                    "NT SERVICE\\" + self.spec.name).casefold():
                raise ValueError("service_foreign")
            return managed(handle)
        except BaseException:
            handle.Close()
            raise
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Выдача прав только виртуальному SID на проверенный путь
    #--------------------------------------------------------------------------------------------------------------
    def _grant(
        self,
        path: Path,
        sid: str,
        rights: str,
        tree: bool,
    ) -> None:

        """Use one native ACL tool with argument vectors and discard path-bearing output.

        :param path: Explicit checked local path.
        :type path: Path
        :param sid: Windows-resolved virtual service SID.
        :type sid: str
        :param rights: Library-owned fixed ACL permission expression.
        :type rights: str
        :param tree: Whether the previously checked tree needs recursive grants.
        :type tree: bool
        """

        # path/sid/rights/tree — не shell-текст. Ошибки icacls могут содержать пути,
        # поэтому вывод не пересылается ни в CLI, ни в уведомления владельцу.
        import win32api

        command = [str(Path(win32api.GetSystemDirectory()) / "icacls.exe"), str(path),
                   "/grant", "*" + sid + ":" + rights, "/Q", "/L"]
        if tree:
            command.append("/T")
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError("service_acl")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль service_windows не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
