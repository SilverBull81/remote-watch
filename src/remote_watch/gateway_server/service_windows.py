# Ограниченные операции SCM, права виртуального аккаунта и автозапуск пользовательского UI.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-154136
#
# Классы:
# -> ServiceInstallError: Безопасная стадия частично завершённой установки
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта.
#
# -> ServiceControlError: Причина отказа проверки владельца без раскрытия путей и текста ОС
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта.
#
# -> WindowsService: Управление только точно совпадающей службой этого сервера
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта.
#    Интерфейс:
#    -> status(): Безопасное состояние SCM, не готовность приложений
#    -> start(): Запрос запуска проверенной службы
#    -> stop(): Запрос мягкой остановки проверенной службы
#    -> install(): Регистрация нового владельца с отдельными правами
#    -> remove(): Удаление остановленной службы без удаления данных
#    -> sync_tray(): Обновление только собственного HKCU Run оператора
#    Служебные методы:
#    -> _sync_entry(): Проверка и изменение своей записи Run
#    -> _open(): Открытие handle с проверкой binary/account
#    -> _has_grant(): Проверка уже выданных прав без повторной записи DACL
#       Вложенные объявления:
#       -> allowed(): Проверка полного разрешения для конкретного файла или каталога
#    -> _grant(): Выдача прав только виртуальному SID на проверенный путь
#
# Функции:
# -> managed(): Гарантированное закрытие native SCM handle
#
# -> checked_tree(): Запрет рекурсивного ACL через reparse point
#
# -> installation_owner(): Удержание блокировки manager без записи в существующий файл службы


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import errno
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .deployment import _path
from .service import ServicePlan, ServiceSpec

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Безопасная стадия частично завершённой установки
#------------------------------------------------------------------------------------------------------------------
class ServiceInstallError(ValueError):
    """
    Retain only a fixed installation phase and numeric OS error.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        stage: str,
        winerror: int | None = None,
        ) -> None:

        """
        Discard raw errors while retaining an actionable installation boundary.

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
# КЛАСС : Причина отказа проверки владельца без раскрытия путей и текста ОС
#------------------------------------------------------------------------------------------------------------------
class ServiceControlError(ValueError):
    """
    Distinguish an active owner from denied access and other lock failures.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        code: str,
        winerror: int | None = None,
        ) -> None:

        """
        Retain only an allowlisted ownership code and a numeric native error.

        :param code: Fixed ownership failure category.
        :type code: str

        :param winerror: Observed native error, or None when unavailable.
        :type winerror: int | None
        """

        # code/winerror — исходные исключения могут содержать приватный путь.
        allowed = {"service_manager_running", "service_control_access", "service_control_io"}
        self.code = code if code in allowed else "service_control_io"
        self.stage = "ownership"
        self.winerror = winerror if type(winerror) is int and 0 <= winerror < 2**31 else None
        super().__init__(self.code)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Гарантированное закрытие native SCM handle
#------------------------------------------------------------------------------------------------------------------
@contextmanager
def managed(
    handle: object,
    ) -> Iterator[object]:

    """
    Close a PySC_HANDLE even on an ownership or permission failure.

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
def checked_tree(
    path: Path,
    ) -> None:

    """
    Check all existing descendants before a recursive permissions operation.

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
# ФУНКЦИЯ : Удержание блокировки manager без записи в существующий файл службы
#------------------------------------------------------------------------------------------------------------------
@contextmanager
def installation_owner(
    path: Path,
    ) -> Iterator[None]:

    """
    Hold the manager's exact OS lock using read access for an existing service-owned file.

    :param path: Previously validated manager lock path in the original control directory.
    :type path: Path

    :return: Context retaining exclusive ownership until installation exits.
    :rtype: Iterator[None]
    """

    # path — оператору намеренно оставлено только чтение control. Для блокировки
    # существующего файла запись не нужна; его содержимое и DACL не изменяются.
    handle = None
    try:
        try:
            handle = path.open("rb")
        except FileNotFoundError:
            # Первая установка создаёт пустой файл без перезаписи существующего.
            # Блокировка диапазона за концом файла поддерживается Windows/POSIX;
            # запись данных не нужна даже при одновременном старте manager.
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.open("a+b")
            except PermissionError as error:
                raise ServiceControlError("service_control_access", getattr(error, "winerror", None)) from None
            except OSError as error:
                raise ServiceControlError("service_control_io", getattr(error, "winerror", None)) from None
        except PermissionError as error:
            raise ServiceControlError("service_control_access", getattr(error, "winerror", None)) from None
        except OSError as error:
            raise ServiceControlError("service_control_io", getattr(error, "winerror", None)) from None

        if handle is not None:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                busy = error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK)
                busy = busy or getattr(error, "winerror", None) == 33
                code = "service_manager_running" if busy else "service_control_io"
                raise ServiceControlError(code, getattr(error, "winerror", None)) from None
        yield
    finally:
        # Дескриптор остаётся открытым во время регистрации и изменения прав.
        # Закрытие освобождает блокировку; удалять или заменять pathname нельзя.
        if handle is not None:
            handle.close()
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Управление только точно совпадающей службой этого сервера
#------------------------------------------------------------------------------------------------------------------
class WindowsService:
    """
    Delegate only service query/start/stop and retain exact deployment ownership checks.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        spec: ServiceSpec,
        ) -> None:

        """
        Load Windows dependencies lazily without touching services or credentials.

        :param spec: Exact expected binary, account and operator binding.
        :type spec: ServiceSpec
        """

        # spec — разрешение управлять одним развёрнутым сервером не распространяется на Caddy.
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
    def status(
        self,
        ) -> dict:

        """
        Read SCM lifecycle without loading linked configs, tokens or journals.

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
    def start(
        self,
        ) -> None:

        """
        Request SCM startup without treating its acceptance as gateway readiness.
        """

        with self._open(16) as handle:
            self.api.StartService(handle, None)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запрос мягкой остановки проверенной службы
    #--------------------------------------------------------------------------------------------------------------
    def stop(
        self,
        ) -> None:

        """
        Request the service's common graceful shutdown path, never terminate by PID.
        """

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

        """
        Create a stopped passwordless service and only then permit automatic boot startup.

        :param plan: Fully validated original paths and bounded ACL targets.
        :type plan: ServicePlan
        """

        # plan — recursive ACL проверяется целиком до первой записи. Не открываем
        # SQLite; manager.lock исключает установку при уже работающем сервере.
        for directory in (plan.environment, *plan.write_dirs):
            checked_tree(directory)
        with installation_owner(plan.deployment.control_dir / "manager.lock"):
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
                        # получают полный доступ F: управляющий процесс защищает их DACL при запуске.
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
                        # Отказ настройки или запуска с корректным статусом STOPPED не
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
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Удаление остановленной службы без удаления данных
    #--------------------------------------------------------------------------------------------------------------
    def remove(
        self,
        ) -> None:

        """
        Delete only the exact stopped service, preserving configs, journals, ACL and CA.
        """

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

        """
        Apply the config's UI choice without restarting the service.

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

        """
        Never replace another logon entry or register a SYSTEM interactive application.

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

        """
        Refuse a same-name foreign service before sending any control.

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
    # СЛУЖЕБНЫЙ МЕТОД : Проверка уже выданных прав без повторной записи DACL
    #--------------------------------------------------------------------------------------------------------------
    def _has_grant(
        self,
        path: Path,
        sid: str,
        rights: str,
        tree: bool,
        ) -> bool:

        """
        Recognize existing explicit service access without rewriting service-owned child ACL.

        :param path: Validated original access target.
        :type path: Path

        :param sid: Exact newly resolved service SID.
        :type sid: str

        :param rights: One of this installer's fixed access patterns.
        :type rights: str

        :param tree: Require matching access on every existing descendant.
        :type tree: bool

        :return: True only when all observed ACL already grant the complete requested access.
        :rtype: bool
        """

        # path/sid/rights/tree — после удаления службы прежний SID может уже иметь
        # все права. Повторный icacls /T тогда не нужен и может получить отказ на
        # файле, созданном службой: оператор читает его, но не меняет его DACL.
        mask = {"RX": 0x1200A9, "(OI)(CI)RX": 0x1200A9, "(OI)(CI)F": 0x1F01FF}.get(rights)
        if mask is None:
            return False

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Проверка полного разрешения для конкретного файла или каталога
        #----------------------------------------------------------------------------------------------------------
        def allowed(
            target: Path,
            ) -> bool:

            """
            Require a sufficient service allow ACE and reject uncertain or denied access.

            :param target: One existing target, never file contents or journal data.
            :type target: Path

            :return: Whether its observed DACL already meets the complete requested grant.
            :rtype: bool
            """

            # target — неизвестные типы ACE и любой deny запрещают быстрый путь.
            # Для каталога проверяем также наследование будущими детьми.
            acl = self.security.GetFileSecurity(str(target), 4).GetSecurityDescriptorDacl()
            if acl is None:
                return False
            matched = False
            for index in range(acl.GetAceCount()):
                (kind, flags), access, principal = acl.GetAce(index)
                if kind != 0:
                    return False
                inherited = not tree or not target.is_dir() or flags & 3 == 3
                if (self.security.ConvertSidToStringSid(principal) == sid and access & mask == mask
                        and not flags & 8 and inherited):
                    matched = True
            return matched
        #----------------------------------------------------------------------------------------------------------

        try:
            if not allowed(path):
                return False
            failures = []
            if tree and path.is_dir():
                for directory, names, files in os.walk(path, followlinks=False, onerror=failures.append):
                    if any(not allowed(Path(directory) / name) for name in (*names, *files)):
                        return False
            return not failures
        except Exception:
            # Недостаток чтения не доказывает наличие разрешения. Обычная выдача
            # прав остаётся единственным путём; отказ сохраняет стадию acl.
            return False
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

        """
        Use one native ACL tool with argument vectors and discard path-bearing output.

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
        if self._has_grant(path, sid, rights, tree):
            return
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

# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль service_windows не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
