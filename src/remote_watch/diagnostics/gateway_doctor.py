# Проверка gateway на сервере и клиенте без доставки сообщений и регистрации сессий.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261013-220456
#
# Классы:
# -> DoctorTarget: Закрытые параметры проверки одного канала.
#
# Функции:
# -> load_targets(): Проверка отдельного клиентского JSON без чтения окружения.
# -> client_checks(): Проверка CA, credentials и явно разрешённой сети.
# -> server_checks(): Проверка deployment и наблюдение работающего manager.
# -> _server_online(): Чтение локального status и проверка loopback listeners.
# -> _tls_context(): Подготовка проверяющего TLS context из выбранного CA.
# -> _row(): Формирование безопасной строки отчёта.
# -> _error_row(): Преобразование известных конфигурационных ошибок без их исходного текста.
# -> _write_report(): Создание нового JSON-отчёта без перезаписи файлов.
# -> main(): CLI с offline-проверкой по умолчанию и явным --online.
#
# Константы:
# -> HINTS: Фиксированные пояснения и следующие действия оператора.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
import platform
import re
import ssl
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any

from remote_watch._credentials import CredentialError, resolve_token, validate_credentials
from remote_watch.events import Identity
from remote_watch.gateway.command_config import CommandConfigError, read_command_json
from remote_watch.gateway.doctor_protocol import decode_probe
from remote_watch.gateway_server.__main__ import DeploymentParser
from remote_watch.gateway_server.config import DeploymentConfigError, _origin, load_deployment_config

from ._doctor_network import network_checks

#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************
HINTS = {
    "config_ok": "Структура настроек корректна; сеть и наличие секретов в окружении этим не проверяются.",
    "config_invalid": "Сверьте структуру файла с шаблоном doctor; значения в отчёт не включаются.",
    "dependency_missing": "Установите нужные extras в тот же venv; doctor ничего не устанавливает сам.",
    "dependencies_ok": "Зависимости выбранного режима доступны в этом venv.",
    "ca_ok": "Проверка сертификата и имени включена; указан доверенный CA либо системное хранилище.",
    "ca_read": "Проверьте путь и доступ к CA; относительный путь считается от клиентского JSON.",
    "ca_invalid": "CA должен быть PEM-сертификатом доверенного корня, а не закрытым ключом.",
    "credential_unavailable": "Проверьте token/token_env и окружение процесса doctor. Значения не печатаются.",
    "credential_shared": "Для notifications и commands нужны разные сервисные токены.",
    "online_not_requested": "Для конечных сетевых проверок добавьте --online.",
    "dns_ok": "Имя разрешено; адреса намеренно не включаются в отчёт.",
    "dns_unavailable": "Проверьте имя endpoint и доступность DNS на этой машине.",
    "dns_timeout": "DNS не ответил за отведённое время; дальнейшие сетевые этапы не запускаются.",
    "tcp_ok": "TCP-соединение установлено; это ещё не проверка TLS или приложения.",
    "tcp_refused": "На выбранном адресе/порту нет доступного listener либо соединение явно отклоняется.",
    "tcp_unavailable": "Проверьте адрес, порт, маршрут VPN и Firewall с этой машины.",
    "tcp_timeout": "TCP не установился вовремя; проверьте маршрут и правила доступа.",
    "tls_ok": "Цепочка сертификатов, срок и имя/IP endpoint прошли проверку TLS.",
    "tls_certificate": ("Проверьте verify_code/tls_reason, доверенный root.crt, имя и часы. "
                        "Проверку TLS не отключайте."),
    "tls_handshake": "На порту может быть обычный HTTP или несовместимый TLS; проверьте Caddy endpoint.",
    "tls_timeout": "TCP доступен, но TLS handshake не завершился в срок.",
    "http_timeout": "TLS доступен, но HTTP-обмен не завершился в срок.",
    "http_unavailable": "HTTP-обмен оборвался после TLS; проверьте Caddy и gateway.",
    "response_invalid": "Ответ не соответствует ограниченному протоколу doctor; проверьте версии и маршрут.",
    "route_ok": "Получен ответ read-only endpoint нужного канала.",
    "route_missing": "Обновите gateway и маршрут Caddy для /v1/notifications/probe или /v1/commands/probe.",
    "redirect_rejected": ("Endpoint возвращает redirect; укажите конечный HTTPS origin. "
                          "Токен дальше не отправлялся."),
    "route_unverified": "Ответ не опознан как doctor нужного канала; проверьте upstream и версии.",
    "upstream_unavailable": "Проверьте, запущен ли нужный gateway и правильно ли задан upstream Caddy.",
    "credential_denied": "Сервисный токен не принят; используйте токен principal нужного канала, а не bot token.",
    "permission_denied": "Токен принят, но точная Identity или запрошенные aliases/scopes не разрешены.",
    "permissions_ok": "Токен, Identity и заявленные права приняты; регистрация и callback не выполнялись.",
    "probe_busy": "Заняты два диагностических обработчика; повторите проверку позднее.",
    "probe_failed": "Диагностический endpoint отказал в проверке; смотрите статус и локальный журнал gateway.",
    "ready": "Наблюдаемый компонент готов; это не подтверждение доставки на телефон.",
    "time_ready": "Hub располагает пригодным доверенным временем; отдельный запрос к TimeAPI не выполнялся.",
    "time_unavailable": "Hub не располагает пригодным временем. Проверьте TimeAPI на сервере через time_probe.",
    "session_present": "Hub видит действующую сессию этой Identity; работоспособность callback не проверялась.",
    "application_not_registered": ("У этой Identity нет действующей сессии. "
                                   "Проверьте локальную диагностику клиента."),
    "control_unavailable": "Manager недоступен через локальное управление; проверьте его запуск и control_dir.",
    "server_not_ready": "Manager или компонент ещё не готов; проверьте status и служебный журнал.",
    "not_observed": "Эти данные не читаются doctor: журналы не открываются и recovery не запускается.",
}


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Закрытые параметры проверки одного канала
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DoctorTarget:

    """Retain explicitly configured probe inputs without exposing secrets through repr."""

    component: str                              # notifications либо commands.
    endpoint: str = field(repr=False)            # Явный HTTPS origin, без пути операции.
    identity: Identity = field(repr=False)       # Точное приложение, чьи права проверяются.
    permissions: tuple[str, ...] = field(repr=False)  # Aliases уведомлений либо scopes команд.
    ca_file: Path | None = field(repr=False)     # Публичный CA; None — системное доверие.
    token: str | None = field(repr=False)        # Закрытый сервисный токен одного канала.
    token_env: str | None = field(repr=False)    # Альтернативное имя переменной окружения.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка клиентского JSON без чтения окружения
#------------------------------------------------------------------------------------------------------------------
def load_targets(path: Path) -> tuple[DoctorTarget, ...]:

    """Validate explicit doctor settings without opening client journals or creating registrations.

    :param path: Local client diagnostic JSON, distinct from an application runtime configuration.
    :type path: Path

    :return: One or two validated channel targets retaining private inputs only in memory.
    :rtype: tuple[DoctorTarget, ...]
    """

    # path — doctor не импортирует приложение и не пытается угадать его callbacks/конфиг.
    value = read_command_json(path)
    if (set(value) - {"schema_version", "endpoint", "identity", "ca_file", "notifications", "commands"}
            or not {"schema_version", "endpoint", "identity"} <= set(value)
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise DeploymentConfigError("config_schema", "config")
    _origin(value["endpoint"])
    endpoint = value["endpoint"]
    ca_file = value.get("ca_file")
    if ca_file is not None:
        if type(ca_file) is not str or not ca_file.strip() or "\x00" in ca_file:
            raise CommandConfigError("client.ca_file", "ca_read")
        ca_file = (path.resolve().parent / ca_file).resolve()
    targets = []
    for name, permission_key in (("notifications", "aliases"), ("commands", "scopes")):
        if name not in value:
            continue
        item = value[name]
        if (type(item) is not dict or set(item) - {"token", "token_env", permission_key}
                or permission_key not in item):
            raise DeploymentConfigError("config_value", name)
        validate_credentials(item.get("token"), item.get("token_env"), "gateway")
        try:
            identity, permissions = decode_probe(json.dumps({"schema_version": 1, "identity": value["identity"],
                "permissions": item[permission_key]}).encode("utf-8"))
        except (ValueError, TypeError, RecursionError):
            raise DeploymentConfigError("config_value", name) from None
        targets.append(DoctorTarget(name, endpoint, identity, permissions, ca_file,
                                    item.get("token"), item.get("token_env")))
    if not targets:
        raise DeploymentConfigError("config_value", "config")
    if len(targets) == 2 and any(
        getattr(targets[0], key) is not None and getattr(targets[0], key) == getattr(targets[1], key)
        for key in ("token", "token_env")
    ):
        raise CredentialError("token", "source")
    return tuple(targets)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Формирование безопасной строки отчёта
#------------------------------------------------------------------------------------------------------------------
def _row(
    check: str,
    status: str,
    code: str,
) -> dict[str, Any]:

    """Create a report row using only labels selected by diagnostic code.

    :param check: Fixed diagnostic stage.
    :type check: str

    :param status: ok, warning, skipped or failed.
    :type status: str

    :param code: Fixed explanation code, never an exception message.
    :type code: str

    :return: Nonsecret structured result.
    :rtype: dict[str, Any]
    """

    # check/status/code — только служебные литералы; никаких имён из JSON.
    return {"check": check, "status": status, "code": code}
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Преобразование известных ошибок конфигурации
#------------------------------------------------------------------------------------------------------------------
def _error_row(error: Exception) -> dict[str, Any]:

    """Preserve known safe configuration labels while dropping arbitrary exception details.

    :param error: Exception from a built-in validator or diagnostic operation.
    :type error: Exception

    :return: Failed configuration result without values or traceback.
    :rtype: dict[str, Any]
    """

    # error — исходный текст, notes, цепочка и пути никогда не сериализуются.
    row = _row("configuration", "failed", "config_invalid")
    if type(error) in (DeploymentConfigError, CommandConfigError):
        row.update(config_code=error.code, field=error.field)
        if type(error) is DeploymentConfigError and error.component_code:
            row.update(component_code=error.component_code, component_field=error.component_field)
        if getattr(error, "hint", None):
            row["hint"] = error.hint
    elif type(error) is CredentialError:
        row["code"] = "credential_unavailable"
    return row
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка проверяющего TLS context
#------------------------------------------------------------------------------------------------------------------
def _tls_context(ca_file: Path | None) -> ssl.SSLContext:

    """Load an explicitly trusted CA without changing operating-system certificate stores.

    :param ca_file: Public CA PEM file, or None for default system trust.
    :type ca_file: Path | None

    :return: Context requiring certificate and hostname verification.
    :rtype: ssl.SSLContext
    """

    # ca_file — только чтение; doctor не копирует сертификаты и не отключает проверку имени.
    context = ssl.create_default_context()
    if ca_file is not None:
        try:
            context.load_verify_locations(cafile=str(ca_file))
        except ssl.SSLError:
            raise CommandConfigError("client.ca_file", "ca_invalid") from None
        except OSError:
            raise CommandConfigError("client.ca_file", "ca_read") from None
    return context
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка CA, credentials и явно разрешённой сети
#------------------------------------------------------------------------------------------------------------------
def client_checks(
    path: Path,
    online: bool,
    timeout: float,
) -> list[dict[str, Any]]:

    """Check explicit client access without creating a command client or sending a notification.

    :param path: Doctor-specific client configuration.
    :type path: Path

    :param online: Explicit permission for DNS, TCP, TLS and read-only HTTP probes.
    :type online: bool

    :param timeout: Maximum observed duration of each network phase.
    :type timeout: float

    :return: Independent nonsecret results for configured channels.
    :rtype: list[dict[str, Any]]
    """

    # path/online/timeout — offline не читает environment credentials и не создаёт сокеты.
    targets = load_targets(path)
    rows = [_row("configuration", "ok", "config_ok")]
    context = _tls_context(targets[0].ca_file)
    rows.append(_row("ca", "ok", "ca_ok"))
    if not online:
        return rows + [_row("network", "skipped", "online_not_requested")]
    tokens = {}
    for target in targets:
        try:
            tokens[target.component] = resolve_token(target.token, target.token_env, "gateway")
        except CredentialError:
            rows.append(_row(target.component + ".credential", "failed", "credential_unavailable"))
    if len(tokens) == 2 and len(set(tokens.values())) != 2:
        return rows + [_row("credentials", "failed", "credential_shared")]
    for target in targets:
        if target.component not in tokens:
            continue
        body = json.dumps({"schema_version": 1, "identity": asdict(target.identity),
                           "permissions": list(target.permissions)}).encode("utf-8")
        rows.extend(network_checks(target.endpoint, context, target.component,
                                   tokens[target.component], body, timeout))
    return rows
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение локального status и проверка loopback listeners
#------------------------------------------------------------------------------------------------------------------
async def _server_online(
    config: Any,
    timeout: float,
) -> list[dict[str, Any]]:

    """Observe the existing manager through its status operation without acquiring deployment ownership.

    :param config: Validated deployment with numeric loopback component listeners.
    :type config: Any

    :param timeout: Maximum local TCP connection duration.
    :type timeout: float

    :return: Sanitized manager, component and listener observations.
    :rtype: list[dict[str, Any]]
    """

    # config/timeout — runtime.json читается только для существующего status; stop не вызывается.
    from remote_watch.gateway_server.manager import control_request

    rows = []
    try:
        snapshot = await asyncio.wait_for(control_request(config, "status"), timeout)
        rows.append(_row("manager", "ok" if snapshot.get("state") == "ready" else "failed",
                         "ready" if snapshot.get("state") == "ready" else "server_not_ready"))
        for name in ("notifications", "commands", "caddy"):
            value = snapshot.get("components", {}).get(name)
            if type(value) is not dict:
                continue
            ready = value.get("state") == "ready"
            code = "ready" if ready else "time_unavailable" if value.get("reason") == "time_unavailable" else (
                "server_not_ready")
            rows.append(_row(name + ".component", "ok" if ready else "failed", code))
    except Exception:
        rows.append(_row("manager", "failed", "control_unavailable"))
    for name in ("notifications", "commands"):
        component = getattr(config, name)
        if component is None:
            continue
        writer = None
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(component.host, component.port), timeout)
            rows.append(_row(name + ".listener", "ok", "tcp_ok"))
        except Exception:
            rows.append(_row(name + ".listener", "failed", "tcp_unavailable"))
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout)
                except Exception:
                    pass
    rows.append(_row("journal_unknown_outcomes", "skipped", "not_observed"))
    return rows
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка deployment и наблюдение работающего manager
#------------------------------------------------------------------------------------------------------------------
def server_checks(
    path: Path,
    online: bool,
    timeout: float,
) -> list[dict[str, Any]]:

    """Validate deployment offline, then optionally observe only already running components.

    :param path: Existing gateway server configuration.
    :type path: Path

    :param online: Explicit permission for local control and loopback TCP observations.
    :type online: bool

    :param timeout: Maximum per-operation observation duration.
    :type timeout: float

    :return: Safe configuration, dependency and readiness results.
    :rtype: list[dict[str, Any]]
    """

    # path/online/timeout — не запускаем manager/Caddy и не открываем SQLite/locks.
    config = load_deployment_config(path)
    rows = [_row("configuration", "ok", "config_ok")]
    modules = ["aiohttp"] + (["pystray", "PIL"] if config.application.ui == "tray" else [])
    for module in modules:
        available = importlib.util.find_spec(module) is not None
        rows.append(_row("dependency." + module, "ok" if available else "failed",
                         "dependencies_ok" if available else "dependency_missing"))
    if not online:
        return rows + [_row("runtime", "skipped", "online_not_requested")]
    if importlib.util.find_spec("aiohttp") is None:
        return rows
    rows.extend(asyncio.run(_server_online(config, timeout)))
    return rows
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание JSON-отчёта без перезаписи файлов
#------------------------------------------------------------------------------------------------------------------
def _write_report(
    path: Path,
    report: dict[str, Any],
) -> None:

    """Create a new private report file without replacing an existing file or symlink.

    :param path: Explicit new output path; its parent must already exist.
    :type path: Path

    :param report: Sanitized diagnostic results, without private configuration values.
    :type report: dict[str, Any]
    """

    # path/report — нельзя затереть конфиг, CA или прежний отчёт ошибочным --report.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : CLI с offline-проверкой по умолчанию
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Run explicitly selected diagnostic scope with a shareable bounded report.

    :param argv: CLI arguments, or None for the process command line.
    :type argv: Sequence[str] | None

    :return: Zero without failed checks, one on a diagnostic failure, two on usage or report failure.
    :rtype: int
    """

    # argv — секреты передаются только в приватном JSON/окружении; CLI не принимает токены.
    parser = DeploymentParser(description="Gateway doctor: безопасные проверки сервера и клиента.")
    parser.add_argument("--version", action="version", version=version("remote-watch"))
    parser.add_argument("--mode", choices=("server", "client"), default="server")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--online", action="store_true", help="Разрешить конечные read-only сетевые проверки.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Срок одного сетевого этапа, 0.1–10 секунд.")
    parser.add_argument("--json", action="store_true", help="Печатать безопасный JSON вместо текстовой сводки.")
    parser.add_argument("--report", type=Path, help="Создать новый JSON-файл; существующий не перезаписывается.")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0.1 <= args.timeout <= 10:
        parser.error("timeout")
    try:
        check = server_checks if args.mode == "server" else client_checks
        rows = check(args.config, args.online, args.timeout)
    except Exception as error:
        rows = [_error_row(error)]
    for row in rows:
        row.setdefault("hint", HINTS.get(row["code"], "Проверьте локальный статус и журнал компонента."))
    package_version = version("remote-watch")
    package_version = package_version if re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,63}", package_version) else "unknown"
    report = {"schema_version": 1, "package_version": package_version, "python": platform.python_version(),
              "mode": args.mode, "online": args.online, "checks": rows,
              "passed": all(row["status"] != "failed" for row in rows)}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"Gateway doctor {package_version}: {args.mode}, {'online' if args.online else 'offline'}")
        for row in rows:
            labels = " ".join(f"{key}={row[key]}" for key in (
                "http_status", "verify_code", "tls_reason", "config_code", "field",
                "component_code", "component_field") if key in row)
            print(f"[{row['status']}] {row['check']}: {row['code']} {labels}\n  {row['hint']}", flush=True)
    if args.report is not None:
        try:
            _write_report(args.report, report)
        except OSError:
            print("Doctor: code=report_write. Укажите новый файл в существующей доступной папке.", file=sys.stderr)
            return 2
    return 0 if report["passed"] else 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явная проверка сервера либо клиентского доступа
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
