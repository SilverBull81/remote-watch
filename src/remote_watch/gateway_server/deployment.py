# Подготовка файлов установки без запуска серверов и перезаписи рабочих данных.
#
# Version 1.0.6
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-185741
#
# Классы:
# -> DeployOptions: Параметры подготовки установки
#
# -> DeployError: Безопасная ошибка подготовки установки
#
# Функции:
# -> _path(): Проверка пути без перехода через ссылки и junction
#
# -> _resource(): Чтение встроенного шаблона
#
# -> _json(): Единое представление JSON
#
# -> _reference(): Проверка ссылки без чтения конфигурации
#
# -> prepare_files(): Подготовка согласованных файлов установки
#
# -> _compare(): Ограниченное сравнение без показа приватного содержимого
#
# -> _write_new(): Создание файла без возможности перезаписи
#
# -> deploy(): Предварительное сравнение и создание отсутствующих файлов


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import sys
from dataclasses import dataclass, field
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from remote_watch.gateway_server.config import _origin
from remote_watch.gateway_server.tray import default_icon

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Параметры подготовки установки
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class DeployOptions:
    """
    Describe an offline deployment without credentials or live runtime ownership.

    :param directory: Root directory resolved when configuration is created.
    :type directory: Path

    :param endpoint: Provider or gateway base endpoint without credentials.
    :type endpoint: str

    :param python: Explicit Python interpreter used by the deployment.
    :type python: Path

    :param components: Enabled gateway components.
    :type components: str

    :param notifications_config: Existing notification gateway configuration file.
    :type notifications_config: Path | None

    :param commands_config: Existing command gateway configuration file.
    :type commands_config: Path | None

    :param notification_port: Internal notification gateway loopback TCP port.
    :type notification_port: int

    :param command_port: Internal command gateway loopback TCP port.
    :type command_port: int

    :param caddy_mode: Explicit choice of external or managed Caddy.
    :type caddy_mode: str

    :param caddy_executable: Installed Caddy executable for managed operation.
    :type caddy_executable: Path | None

    :param caddy_config: Existing Caddy configuration, or None to generate a template.
    :type caddy_config: Path | None

    :param caddy_data: Persistent Caddy storage directory containing the original CA.
    :type caddy_data: Path | None

    :param ui: Headless or interactive Windows tray mode.
    :type ui: str
    """

    directory: Path = field(repr=False)                  # Папка создаваемого комплекта.
    endpoint: str = field(repr=False)                    # Общий внешний HTTPS-адрес клиентов.
    python: Path = field(default_factory=lambda: Path(sys.executable), repr=False)  # Выбранный интерпретатор.
    components: str = "both"                             # notifications, commands либо оба компонента.
    notifications_config: Path | None = field(default=None, repr=False)  # Ссылка на прежний конфиг уведомлений.
    commands_config: Path | None = field(default=None, repr=False)  # Ссылка на прежний конфиг команд.
    notification_port: int = 8765                        # Внутренний loopback-порт уведомлений.
    command_port: int = 8766                             # Внутренний loopback-порт команд.
    caddy_mode: str = "external"                         # Владение Caddy задаётся явно.
    caddy_executable: Path | None = field(default=None, repr=False)  # Локальный Caddy для managed-режима.
    caddy_config: Path | None = field(default=None, repr=False)  # Прежний Caddyfile; None — новый шаблон.
    caddy_data: Path | None = field(default=None, repr=False)  # Явное постоянное хранилище Caddy.
    ui: str = "headless"                                 # headless или Windows tray.
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Безопасная ошибка подготовки установки
#------------------------------------------------------------------------------------------------------------------
class DeployError(ValueError):
    """
    Carry only a fixed deployment failure code, never an original exception.
    """
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка пути без перехода через ссылки и junction
#------------------------------------------------------------------------------------------------------------------
def _path(
    value: Path,
    ) -> Path:

    """
    Reject link ancestors before any directory creation or file access.

    :param value: Explicit operator-owned local path.
    :type value: Path

    :return: Absolute path without symbolic links or Windows reparse points.
    :rtype: Path
    """

    # value — проверяем каждый существующий родитель, включая dangling symlink.

    if any(ord(char) < 32 for char in str(value)):
        raise DeployError("directory_invalid")

    path = Path(os.path.abspath(value))
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DeployError("linked_path")

    return path
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Чтение встроенного шаблона
#------------------------------------------------------------------------------------------------------------------
def _resource(
    name: str,
    ) -> str:

    """
    Read a fixed packaged template independently of the source checkout.

    :param name: Library-owned template basename.
    :type name: str

    :return: Template text decoded without a possible BOM.
    :rtype: str
    """

    # name — только константа библиотеки, не путь из пользовательского конфига.

    return files("remote_watch.gateway_server").joinpath("templates", name).read_text(encoding="utf-8-sig")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Единое представление JSON
#------------------------------------------------------------------------------------------------------------------
def _json(
    value: object,
    ) -> bytes:

    """
    Serialize configuration with readable Unicode and Windows-friendly line endings.

    :param value: Generated configuration or manifest object.
    :type value: object

    :return: UTF-8 JSON without BOM and with CRLF endings.
    :rtype: bytes
    """

    # value — только подготовленная структура, без автоматического чтения чужих файлов.

    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").replace("\n", "\r\n").encode("utf-8")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка ссылки без чтения конфигурации
#------------------------------------------------------------------------------------------------------------------
def _reference(
    value: Path,
    ) -> str:

    """
    Keep an existing file at its original location and preserve its relative-path base.

    :param value: Existing operator-selected component file or executable.
    :type value: Path

    :return: Absolute reference to an existing regular file.
    :rtype: str
    """

    # value — конфиги и CA не копируются; их содержимое deploy не требуется.
    # Python внутри Linux venv обычно является symlink. Это ссылка для чтения/запуска,
    # а не место записи; сохраняем путь venv, иначе потеряем его site-packages.

    path = Path(os.path.abspath(value))
    if any(ord(char) < 32 for char in str(path)):
        raise DeployError("reference_missing")

    if not path.is_file():
        raise DeployError("reference_missing")

    return str(path)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка согласованных файлов установки
#------------------------------------------------------------------------------------------------------------------
def prepare_files(
    options: DeployOptions,
    ) -> dict[str, bytes]:

    """
    Render an offline installation kit with explicit external and internal endpoints.

    :param options: Explicit deployment choices, without provider credentials.
    :type options: DeployOptions

    :return: Relative library-owned filenames mapped to complete file contents.
    :rtype: dict[str, bytes]
    """

    # options — сначала проверяем все аргументы; до конца этой функции записи нет.

    _origin(options.endpoint)
    root = _path(options.directory)
    if root == Path(root.anchor) or (root.exists() and not root.is_dir()):
        raise DeployError("directory_invalid")

    python = _reference(options.python)
    if options.components not in ("both", "notifications", "commands") or options.ui not in ("headless", "tray"):
        raise DeployError("options_invalid")

    if options.caddy_mode not in ("external", "managed"):
        raise DeployError("options_invalid")

    for port in (options.notification_port, options.command_port):
        if type(port) is not int or not 1 <= port <= 65535:
            raise DeployError("port_invalid")

    if options.components == "both" and options.notification_port == options.command_port:
        raise DeployError("port_conflict")

    external_port = urlsplit(options.endpoint).port or 443
    active_ports = [port for name, port in (("notifications", options.notification_port),
                                          ("commands", options.command_port))
                    if options.components in ("both", name)]
    if (external_port in active_ports
            or (options.caddy_mode == "managed" and 2019 in [external_port, *active_ports])):
        raise DeployError("port_conflict")

    package_version = version("remote-watch")
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,63}", package_version):
        raise DeployError("version_invalid")

    # Внешний Caddy, включая уже установленную службу, не становится нашим ребёнком.
    caddy = {"mode": options.caddy_mode}
    if options.caddy_mode == "managed":
        if options.caddy_executable is None or options.caddy_data is None:
            raise DeployError("managed_paths_required")
        caddy.update(executable=_reference(options.caddy_executable),
                     config=_reference(options.caddy_config) if options.caddy_config else str(root / "Caddyfile"),
                     data_dir=str(_path(options.caddy_data)))
    elif any(value is not None for value in (options.caddy_executable, options.caddy_config, options.caddy_data)):
        raise DeployError("external_paths_unused")

    deployment = json.loads(_resource("gateway_server.json"))
    deployment.update(endpoint=options.endpoint.rstrip("/"), caddy=caddy)
    deployment["application"]["ui"] = options.ui

    # Сервер получает собственную копию логотипа рядом с конфигом. Путь остаётся
    # относительным, чтобы комплект можно было перенести вместе с папкой logo.
    result = {"logo/remote_watch.ico": default_icon().read_bytes()}
    routes = []
    for component, reference, port, template in (
        ("notifications", options.notifications_config, options.notification_port, "notification_gateway.json"),
        ("commands", options.commands_config, options.command_port, "command_gateway.json"),
    ):
        if options.components not in ("both", component):
            if reference is not None:
                raise DeployError("disabled_reference")
            deployment.pop(component, None)
            continue
        filename = template.replace(".json", ".local.json")
        deployment[component] = {"config": _reference(reference) if reference else filename,
                                 "host": "127.0.0.1", "port": port}
        if reference is None:
            result[filename] = _json(json.loads(_resource(template)))
        route = "/v1/commands/*" if component == "commands" else "/v1/notifications"
        routes.append(f"    handle {route} {{\n        reverse_proxy 127.0.0.1:{port}\n    }}")
        if component == "notifications":
            routes.append(f"    handle /v1/notifications/probe {{\n        reverse_proxy 127.0.0.1:{port}\n    }}")
        client_name = "command_client.json" if component == "commands" else "notification_client.json"
        client = json.loads(_resource(client_name))
        client["endpoint"] = options.endpoint.rstrip("/")
        result["clients/" + client_name.replace(".json", ".example.json")] = _json(client)

    result["gateway_server.local.json"] = _json(deployment)

    # Примеры клиентов при миграции не извлекают секреты/Identity из рабочих конфигов.
    # Для существующих приложений сохраняются их настройки; примеры служат только памяткой.
    storage = str(_path(options.caddy_data) if options.caddy_data else root / "caddy-data").replace("\\", "/")
    log_path = str(root / "logs/caddy-runtime.log").replace("\\", "/")
    if any(char in storage + log_path for char in "{}\r\n\x00"):
        raise DeployError("caddy_path_invalid")

    replacements = {"@@ENDPOINT@@": options.endpoint.rstrip("/"), "@@ROUTES@@": "\n".join(routes),
                    "@@STORAGE@@": json.dumps(storage, ensure_ascii=False),
                    "@@LOG@@": json.dumps(log_path, ensure_ascii=False)}
    caddy_text = _resource("Caddyfile.template")
    for key, value in replacements.items():
        caddy_text = caddy_text.replace(key, value)

    caddy_name = ("Caddyfile" if options.caddy_mode == "managed" and not options.caddy_config
                  else "Caddyfile.example")
    result[caddy_name] = caddy_text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")

    # Пути не вставляются в исполняемый код без quoting. Скрипты работают из любой cwd.
    for action in ("start", "stop", "status", "check-config"):
        for suffix, quote in (("ps1", "'" + python.replace("'", "''") + "'"), ("sh", shlex.quote(python))):
            text = _resource("launcher." + suffix).replace("@@PYTHON@@", quote).replace("@@ACTION@@", action)
            encoding = "utf-8-sig" if suffix == "ps1" else "utf-8"
            text = text.replace("\r\n", "\n")
            if suffix == "ps1":
                text = text.replace("\n", "\r\n")
            result[action + "." + suffix] = text.encode(encoding)

    # Doctor по умолчанию offline; аргументы --online/--report оператор задаёт явно.
    for suffix, quote in (("ps1", "'" + python.replace("'", "''") + "'"), ("sh", shlex.quote(python))):
        text = _resource("doctor." + suffix).replace("@@PYTHON@@", quote).replace("\r\n", "\n")
        result["doctor." + suffix] = (text.replace("\n", "\r\n").encode("utf-8-sig")
                                       if suffix == "ps1" else text.encode("utf-8"))

    doctor = json.loads(_resource("gateway_doctor.client.json"))
    doctor["endpoint"] = options.endpoint.rstrip("/")
    for name in ("notifications", "commands"):
        if options.components not in ("both", name):
            doctor.pop(name)

    result["clients/gateway_doctor.client.example.json"] = _json(doctor)

    # Служба регистрируется отдельной явной командой. Обычный deploy создаёт
    # только launchers/инструкцию, не меняет SCM, registry и ACL рабочего сервера.
    for action in ("install", "remove", "start", "stop", "status", "sync-tray"):
        name = "service-status" if action == "status" else action if action == "sync-tray" else action + "-service"
        for suffix in ("ps1", "cmd"):
            quote = ("'" + python.replace("'", "''") + "'" if suffix == "ps1" else
                     '"' + python.replace("%", "%%") + '"')
            text = _resource("service." + suffix).replace("@@PYTHON@@", quote).replace("@@ACTION@@", "--" + action)
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
            result[name + "." + suffix] = text.encode("utf-8-sig" if suffix == "ps1" else "utf-8")

    tray_text = _resource("tray.ps1").replace("@@PYTHON@@",
        "'" + str(options.python.with_name("pythonw.exe")).replace("'", "''") + "'")
    result["tray.ps1"] = tray_text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8-sig")

    # systemd не использует shell quoting: %, обратные слеши и кавычки кодируются
    # по синтаксису unit. User/Group оператор указывает до явной установки на Linux.
    unit = _resource("systemd.service")
    for key, value in (("DIRECTORY", str(root)), ("PYTHON", python),
                       ("CONFIG", str(root / "gateway_server.local.json"))):
        quoted = '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
        if key != "DIRECTORY":
            quoted = quoted.replace("$", "$$")
        unit = unit.replace("@@" + key + "@@", quoted)

    result["service/remote-watch-gateway.service"] = unit.replace("\r\n", "\n").encode("utf-8")
    for name in ("install-systemd.sh", "remove-systemd.sh"):
        result["service/" + name] = _resource(name).replace("\r\n", "\n").encode("utf-8")

    result["SERVICE.md"] = _resource("SERVICE.md").replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8-sig")
    guide = _resource("START_HERE.md").replace("@@VERSION@@", package_version)
    result["START_HERE.md"] = guide.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8-sig")
    manifest = {"schema_version": 1, "package_version": package_version,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.items())}}
    result["deploy-manifest.json"] = _json(manifest)
    return result
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Ограниченное сравнение без показа приватного содержимого
#------------------------------------------------------------------------------------------------------------------
def _compare(
    path: Path,
    expected: bytes,
    ) -> str:

    """
    Compare an existing file without returning its content or hash to the caller.

    :param path: Checked destination path.
    :type path: Path

    :param expected: Proposed public template bytes.
    :type expected: bytes

    :return: create, identical or preserved; unsupported file types raise a safe error.
    :rtype: str
    """

    # path/expected — даже сильно изменённый конфиг не показываем как текстовый diff.

    _path(path)
    if not path.exists():
        return "create"

    if not path.is_file():
        raise DeployError("destination_invalid")

    with path.open("rb") as stream:
        current = stream.read(len(expected) + 1)

    return "identical" if current == expected else "preserved"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Создание файла без возможности перезаписи
#------------------------------------------------------------------------------------------------------------------
def _write_new(
    path: Path,
    data: bytes,
    ) -> None:

    """
    Create exclusively and leave previous files untouched, including during a race.

    :param path: Checked library-owned output path.
    :type path: Path

    :param data: Complete rendered file contents.
    :type data: bytes
    """

    # path/data — O_EXCL не позволяет затереть файл, появившийся после preview.

    _path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o700 if path.suffix == ".sh" else 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Предварительное сравнение и создание отсутствующих файлов
#------------------------------------------------------------------------------------------------------------------
def deploy(
    options: DeployOptions,
    *,
    dry_run: bool = False,
    update_templates: bool = False,
    ) -> list[tuple[str, str]]:

    """
    Prepare files offline without changing existing configurations, journals or services.

    :param options: Explicit installation choices.
    :type options: DeployOptions

    :param dry_run: Compare only, without creating even the target directory.
    :type dry_run: bool

    :param update_templates: Write proposals under templates/version instead of operational paths.
    :type update_templates: bool

    :return: Fixed relative filenames and safe disposition labels, without file contents.
    :rtype: list[tuple[str, str]]
    """

    # options — рабочие конфиги только упоминаются по абсолютной ссылке.
    # dry_run — не создаём папки, не проверяем сеть, не открываем журналы и службы, не читаем токены из окружения.
    # update_templates — оператор вручную переносит нужные отличия в рабочие настройки.

    content = prepare_files(options)
    root = _path(options.directory)
    base = root / "templates" / version("remote-watch") if update_templates else root
    reports = []
    pending = []

    # Проверяем весь план перед первой записью: каталог вместо файла не оставит полкомплекта.
    for name, data in content.items():
        state = _compare(base / name, data)
        if update_templates:
            reports.append((name, "current_" + _compare(root / name, data)))
        reports.append(((base / name).relative_to(root).as_posix(), state))
        if state == "create":
            pending.append((base / name, data))

    if not dry_run:
        for path, data in pending:
            _write_new(path, data)

    return reports
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль deployment не предназначен для прямого запуска. Используйте remote_watch.gateway_server.deploy.")
#------------------------------------------------------------------------------------------------------------------
