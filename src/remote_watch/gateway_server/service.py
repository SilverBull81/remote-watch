# Явная установка службы Windows и управление отдельным пользовательским треем.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Классы:
# -> ServiceSpec: Неизменяемая привязка службы к Python, конфигу и оператору
#    Интерфейс:
#    -> arguments(): Аргументы отдельного процесса без shell
#    Специальные методы:
#    -> __post_init__(): Проверка точной привязки службы
#
# -> ServicePlan: Файлы чтения и каталоги записи для отдельного аккаунта службы
#
# Функции:
# -> current_sid(): SID текущего пользователя без имени и пароля
#
# -> make_plan(): Проверка путей до регистрации службы и изменения ACL
#
# -> main(): Явный выбор установки, удаления, управления или пользовательского UI


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path

from .__main__ import DeploymentParser
from .config import DeploymentConfig, load_deployment_config
from .deployment import _path

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Неизменяемая привязка службы к Python, конфигу и оператору
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """
    Bind SCM operations to one exact command and passwordless virtual account.

    :param config: Configuration file bound to this component or service.
    :type config: Path

    :param python: Explicit Python interpreter used by the deployment.
    :type python: Path

    :param operator_sid: Windows SID of the explicitly delegated operator.
    :type operator_sid: str

    :param name: Stable configured command, principal, service or component name.
    :type name: str
    """

    config: Path = field(repr=False)      # Прежний общий JSON, не копия рабочих настроек.
    python: Path = field(repr=False)      # python.exe выбранного установленного venv.
    operator_sid: str = field(repr=False)  # Пользователь, которому делегировано управление.
    name: str = "RemoteWatchGateway"      # Отдельное имя, не имя службы Caddy.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Аргументы отдельного процесса без shell
    #--------------------------------------------------------------------------------------------------------------
    def arguments(
        self,
        action: str,
        ) -> list[str]:

        """
        Build a fixed module invocation with separately quoted arguments.

        :param action: Fixed service CLI action.
        :type action: str

        :return: Argument vector, never executable JSON text.
        :rtype: list[str]
        """

        # action — только внутренний фиксированный режим; пароль/токены сюда не попадают.
        if action not in ("run", "tray"):
            raise ValueError("service_action")
        executable = self.python.with_name("pythonw.exe") if action == "tray" else self.python
        return [str(executable), "-I", "-B", "-m", "remote_watch.gateway_server.service",
                "--config", str(self.config), "--name", self.name,
                "--operator-sid", self.operator_sid, "--" + action]
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка точной привязки службы
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(
        self,
        ) -> None:

        """
        Reject malformed names, SIDs and nonabsolute paths before calling SCM.
        """

        if not re.fullmatch(r"RemoteWatch[A-Za-z0-9_-]{0,48}", self.name):
            raise ValueError("service_name")
        if not re.fullmatch(r"S-1-5-21-(?:[0-9]{1,10}-){3}[0-9]{1,10}", self.operator_sid):
            raise ValueError("operator_sid")
        if any(int(part) > 2**32 - 1 for part in self.operator_sid.split("-")[4:]):
            raise ValueError("operator_sid")
        for path in (self.config, self.python):
            if not isinstance(path, Path) or not path.is_absolute() or any(ord(c) < 32 for c in str(path)):
                raise ValueError("service_path")
        if self.python.name.lower() != "python.exe":
            raise ValueError("service_python")
        # Windows spelling/case одного пути не должны менять exact binding.
        # Нормализуем только два пути; весь ImagePath не сравнивается casefold.
        object.__setattr__(self, "config", Path(os.path.normcase(str(self.config))))
        object.__setattr__(self, "python", Path(os.path.normcase(str(self.python))))
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Файлы чтения и каталоги записи для отдельного аккаунта службы
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ServicePlan:
    """
    Describe bounded ACL targets without opening journals or resolving environment secrets.

    :param spec: Exact configured service invocation and operator binding.
    :type spec: ServiceSpec

    :param deployment: Validated deployment configuration.
    :type deployment: DeploymentConfig

    :param read_files: Files requiring service read or execute access.
    :type read_files: tuple[Path, ...]

    :param write_dirs: Dedicated control, log, command-state and managed-CA directories requiring write access.
    :type write_dirs: tuple[Path, ...]

    :param environment: Fixed environment variables passed to the owned service process.
    :type environment: Path
    """

    spec: ServiceSpec                       # Точная зарегистрированная команда службы.
    deployment: DeploymentConfig = field(repr=False)  # Проверенная структура прежнего deployment.
    read_files: tuple[Path, ...] = field(repr=False)  # Конфиги и binary только для чтения/запуска.
    write_dirs: tuple[Path, ...] = field(repr=False)  # Control, logs, command state и managed CA.
    environment: Path = field(repr=False)   # Только выбранный venv, не глобальный Python.
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : SID текущего пользователя без имени и пароля
#------------------------------------------------------------------------------------------------------------------
def current_sid() -> str:

    """
    Read the current process user's SID through optional Windows APIs.

    :return: SID used for operator delegation and that user's logon entry.
    :rtype: str
    """

    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 8)
    try:
        return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, 1)[0])
    finally:
        token.Close()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка путей до регистрации службы и изменения ACL
#------------------------------------------------------------------------------------------------------------------
def make_plan(
    spec: ServiceSpec,
    ) -> ServicePlan:

    """
    Validate explicit local ACL targets without writing files or reading environment tokens.

    :param spec: Exact service binding chosen by the operator.
    :type spec: ServiceSpec

    :return: Immutable installation plan, with original journal and CA locations.
    :rtype: ServicePlan
    """

    # spec — сначала проверяем весь план. Смена аккаунта не должна сопровождаться
    # переносом БД или регистрацией службы, ссылающейся на другой пустой state_dir.
    config = load_deployment_config(spec.config)
    # В существующей установке на LV папку logs также использует внешний Caddy.
    # Управляющий процесс целиком заменяет права своей папки: её нельзя делить с Caddy,
    # иначе смена service account отрежет внешнего writer при ротации его файла.
    if config.log_dir == spec.config.parent / "logs":
        raise ValueError("service_shared_logs")
    if config.log_dir.exists():
        for index, entry in enumerate(config.log_dir.iterdir()):
            if index >= 32 or not re.fullmatch(r"gateway_server\.jsonl(?:\.(?:[1-9]|1[0-9]|20))?", entry.name):
                raise ValueError("service_shared_logs")
    get_drive = None
    if os.name == "nt":
        import ctypes

        get_drive = ctypes.WinDLL("kernel32").GetDriveTypeW
        get_drive.argtypes = [ctypes.c_wchar_p]
        get_drive.restype = ctypes.c_uint
    environment = spec.python.parent.parent
    if not (environment / "pyvenv.cfg").is_file() or not spec.python.is_file():
        raise ValueError("service_requires_venv")
    files = [spec.config, spec.python]
    directories = [config.control_dir, config.log_dir]
    for component in (config.notifications, config.commands):
        if component is not None:
            files.append(component.config)
    if config.commands is not None:
        from remote_watch.gateway.command_config import _gateway

        directories.append(_gateway(config.commands.config, False).state_dir)
    if config.caddy.mode == "managed":
        files.extend((config.caddy.executable, config.caddy.config))
        directories.append(config.caddy.data_dir)
    if config.application.ui == "tray":
        files.append(spec.python.with_name("pythonw.exe"))
        if config.application.icon_path is not None:
            files.append(config.application.icon_path)

    # Нельзя выдавать рекурсивные права на диск/корень installation или на venv
    # через state_dir. Junction/symlink также не должны расширять явный список.
    for target in (*files, *directories, environment):
        if (str(target).startswith(("\\\\", "//")) or target == Path(target.anchor)
                or get_drive is not None and get_drive(str(target.anchor)) != 3):
            raise ValueError("service_local_path")
        _path(target)
    if any(not p.is_file() for p in files):
        raise ValueError("service_file_missing")
    if any(p.exists() and not p.is_dir() for p in directories):
        raise ValueError("service_directory_invalid")
    for directory in directories:
        if (directory == spec.config.parent or directory in spec.config.parents
                or any(directory == p or directory in p.parents for p in (*files, environment))):
            raise ValueError("service_directory_overlap")
    return ServicePlan(spec, config, tuple(dict.fromkeys(files)), tuple(dict.fromkeys(directories)), environment)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Явный выбор установки, удаления, управления или пользовательского UI
#------------------------------------------------------------------------------------------------------------------
def main(
    argv: Sequence[str] | None = None,
    ) -> int:

    """
    Keep service registration separate from deploy and normal foreground startup.

    :param argv: Explicit arguments, or None for the process command line.
    :type argv: Sequence[str] | None

    :return: Zero on success, one on a safely classified failure.
    :rtype: int
    """

    # argv — аргументы argparse не печатаются при ошибке: оператор мог вставить
    # туда токен. Ни install, ни remove не являются действием по умолчанию.
    parser = DeploymentParser(description="Windows service: install/remove/start/stop/status/tray.")
    parser.add_argument("--version", action="version", version=version("remote-watch"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--name", default="RemoteWatchGateway")
    parser.add_argument("--operator-sid")
    parser.add_argument("--dry-run", action="store_true")
    group = parser.add_mutually_exclusive_group(required=True)
    for action in ("install", "remove", "start", "stop", "status", "run", "tray", "sync-tray"):
        group.add_argument("--" + action, dest="action", action="store_const", const=action)
    args = parser.parse_args(argv)
    if args.dry_run and args.action not in ("install", "remove", "sync-tray"):
        parser.error("dry_run")
    try:
        if os.name != "nt":
            raise ValueError("service_requires_windows")
        python = Path(sys.executable).absolute().with_name("python.exe")
        spec = ServiceSpec(Path(os.path.abspath(args.config)), python,
                           args.operator_sid or current_sid(), args.name)
        plan = make_plan(spec) if args.action in ("install", "sync-tray") else None
        if args.dry_run:
            print(json.dumps({"action": args.action, "dry_run": True, "writes": False,
                "read_files": len(plan.read_files) if plan else 0,
                "write_directories": len(plan.write_dirs) if plan else 0,
                "tray": plan.deployment.application.ui if plan else "unchanged"}), flush=True)
            return 0
        if args.action == "run":
            from .service_host import run_service

            return run_service(spec)
        if args.action == "tray":
            from .service_tray import run_tray

            return run_tray(spec)
        from .service_windows import WindowsService

        if args.action == "install" and plan.deployment.application.ui == "tray":
            from importlib.util import find_spec

            if find_spec("pystray") is None or find_spec("PIL") is None:
                raise ImportError("tray_extra_required")
        backend = WindowsService(spec)
        if args.action == "install":
            backend.install(plan)
        elif args.action == "sync-tray":
            backend.sync_tray(plan.deployment.application.ui == "tray")
        elif args.action == "remove":
            backend.remove()
        elif args.action == "start":
            backend.start()
        elif args.action == "stop":
            backend.stop()
        else:
            print(json.dumps(backend.status()), flush=True)
            return 0
        print(json.dumps({"action": args.action, "accepted": True}), flush=True)
        return 0
    except Exception as error:
        from .service_windows import ServiceInstallError

        # Ошибки внешних API содержат пути/имена. Разрешаем лишь фиксированные коды
        # нашей утилиты, а от Windows оставляем только числовой winerror.
        codes = {"service_requires_windows", "service_requires_venv", "service_local_path", "service_file_missing",
                 "service_directory_overlap", "service_directory_invalid", "service_name", "operator_sid",
                 "service_path", "service_python", "service_shared_logs",
                 "service_exists", "service_foreign", "service_running", "service_not_found", "service_acl",
                 "service_tray_entry", "service_install_partial", "service_manager_running", "service_linked_path"}
        code = str(error) if type(error) is ValueError and str(error) in codes else (
            "dependency_missing" if isinstance(error, ImportError) else "service_operation_failed")
        winerror = getattr(error, "winerror", None)
        if type(error) is ServiceInstallError:
            code = "service_install_partial"
        elif winerror == 5:
            code = "service_access_denied"
        elif winerror == 1060:
            code = "service_not_found"
        print(json.dumps({"code": code, "stage": error.stage if type(error) is ServiceInstallError else None,
                          "winerror": winerror if type(winerror) is int else None}), flush=True)
        return 1
#------------------------------------------------------------------------------------------------------------------

# ТОЧКА ВХОДА : Явные операции службы
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
