# Общие настройки deployment и их проверка без запуска серверов.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-184007
#
# Классы:
# -> DeploymentConfigError: Безопасная ошибка общего конфига.
#    Конструктор:
#    -> __init__(): Сохранение только известных кодов и полей.
#
# -> ComponentConfig: Путь к настройкам и внутренний listener компонента.
#    Специальные методы:
#    -> __post_init__(): Проверка пути и loopback-адреса.
#
# -> CaddyConfig: Явное владение процессом Caddy.
#    Специальные методы:
#    -> __post_init__(): Проверка режима и обязательных путей.
#
# -> LifecycleConfig: Сроки запуска, наблюдения и остановки.
#    Специальные методы:
#    -> __post_init__(): Проверка сроков и частоты наблюдения.
#
# -> DeploymentConfig: Общие настройки двух независимых компонентов.
#    Специальные методы:
#    -> __post_init__(): Проверка согласованности deployment.
#
# Функции:
# -> load_deployment_config(): Чтение общего конфига и проверка связанных файлов.
# -> _read_json(): Ограниченное чтение строгого JSON.
# -> _unique_object(): Отказ при повторном ключе объекта.
# -> _reject_constant(): Отказ при NaN и Infinity.
# -> _object(): Проверка известных и обязательных полей.
# -> _path(): Разрешение пути относительно deployment-файла.
# -> _absolute_path(): Проверка уже разрешённого пути.
# -> _origin(): Проверка внешнего HTTPS origin.
# -> _check_components(): Проверка существующих схем без открытия ресурсов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .._validation import require_int, require_text
from .policy import RestartPolicy, _bounded_seconds


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Безопасная ошибка общего конфига
#------------------------------------------------------------------------------------------------------------------
class DeploymentConfigError(ValueError):

    """Expose fixed schema labels without paths, credentials or nested exceptions."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        code: str,
        field_name: str,
    ) -> None:

        """Retain only allowlisted error labels.

        :param code: Fixed diagnostic category.
        :type code: str

        :param field_name: Fixed deployment section name.
        :type field_name: str
        """

        # code — категория ошибки, без исходного сообщения исключения.
        # field_name — раздел общей схемы; неизвестные ключи не выводятся.

        codes = ("config_read", "config_size", "config_json", "config_schema", "config_value",
                 "component_config", "path_unavailable", "operation_failed")
        fields = ("config", "schema_version", "endpoint", "control_dir", "log_dir", "notifications",
                  "commands", "caddy", "lifecycle", "restart")
        self.code = code if code in codes else "config_value"
        self.field = field_name if field_name in fields else "config"
        super().__init__(f"Invalid deployment: code={self.code} field={self.field}")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Путь к настройкам и внутренний listener компонента
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ComponentConfig:

    """An enabled component using an existing configuration file and a loopback listener."""

    config: Path = field(repr=False)     # Абсолютный путь к прежнему конфигу компонента.
    port: int                           # TCP-порт внутреннего HTTP listener.
    host: str = "127.0.0.1"              # Числовой loopback-адрес, без DNS и wildcard.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка пути и loopback-адреса
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Require an absolute file path and a concrete loopback TCP endpoint."""

        _absolute_path(self.config)
        require_int(self.port, "port")
        if self.port > 65535:
            raise ValueError("port exceeds limit")

        if type(self.host) is not str or self.host not in ("127.0.0.1", "::1"):
            raise ValueError("explicit IPv4 or IPv6 loopback required")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Явное владение процессом Caddy
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CaddyConfig:

    """External Caddy is observed only; managed Caddy requires explicit persistent storage."""

    mode: str = "external"                              # external не даёт права управлять чужим процессом.
    executable: Path | None = field(default=None, repr=False)  # Уже установленный binary; без скачивания.
    config: Path | None = field(default=None, repr=False)      # Caddyfile для собственного дочернего процесса.
    data_dir: Path | None = field(default=None, repr=False)    # Постоянные данные Caddy, включая прежний CA.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка режима и обязательных путей
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject ambiguous ownership and incomplete managed configuration."""

        paths = (self.executable, self.config, self.data_dir)
        if self.mode == "external":
            if any(path is not None for path in paths):
                raise ValueError("external Caddy cannot contain managed paths")
        elif self.mode == "managed":
            for path in paths:
                _absolute_path(path)
        else:
            raise ValueError("unknown Caddy ownership mode")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Сроки запуска, наблюдения и остановки
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LifecycleConfig:

    """Monotonic deadlines for the future manager, independent of provider request timeouts."""

    startup_timeout: float = 60.0        # Общий срок начального запуска всех собственных компонентов.
    shutdown_timeout: float = 30.0       # Общий срок мягкой остановки, секунды.
    kill_timeout: float = 5.0            # Дополнительный срок подтверждения принудительной остановки.
    heartbeat_interval: float = 1.0      # Частота наблюдения дочернего loop, секунды.
    heartbeat_timeout: float = 15.0      # Предельный возраст признака работающего loop.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка сроков и частоты наблюдения
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate finite deadlines and leave room for multiple heartbeat observations."""

        for name in ("startup_timeout", "shutdown_timeout", "kill_timeout",
                     "heartbeat_interval", "heartbeat_timeout"):
            _bounded_seconds(getattr(self, name), name)

        if self.heartbeat_interval < 0.1 or self.heartbeat_timeout < 3 * self.heartbeat_interval:
            raise ValueError("heartbeat intervals are inconsistent")
        if self.startup_timeout < self.heartbeat_timeout:
            raise ValueError("startup deadline is shorter than heartbeat deadline")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Общие настройки двух независимых компонентов
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DeploymentConfig:

    """Validated deployment paths and policies; construction does not acquire resources."""

    endpoint: str = field(repr=False)        # Внешний HTTPS origin; не внутренний порт компонента.
    control_dir: Path = field(repr=False)    # Каталог будущего локального управления manager.
    log_dir: Path = field(repr=False)        # Отдельный каталог ограниченных служебных логов.
    notifications: ComponentConfig | None = None     # None отключает исходящий gateway.
    commands: ComponentConfig | None = None          # None отключает командный gateway.
    caddy: CaddyConfig = field(default_factory=CaddyConfig)           # Режим владения Caddy.
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)  # Сроки управления процессами.
    restart: RestartPolicy = field(default_factory=RestartPolicy)    # Лимит повторных запусков компонента.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка согласованности deployment
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject empty deployments, ambiguous paths and shared listener ports."""

        _origin(self.endpoint)
        for path in (self.control_dir, self.log_dir):
            _absolute_path(path)
        if not isinstance(self.caddy, CaddyConfig) or not isinstance(self.lifecycle, LifecycleConfig):
            raise TypeError("deployment models required")
        if not isinstance(self.restart, RestartPolicy):
            raise TypeError("restart model required")

        components = [item for item in (self.notifications, self.commands) if item is not None]
        if not components or any(not isinstance(item, ComponentConfig) for item in components):
            raise ValueError("at least one valid component required")

        # Разные порты упрощают одинаковую конфигурацию IPv4/IPv6 и исключают
        # неоднозначное проксирование. Внешний порт также не принадлежит backend.
        ports = [item.port for item in components]
        external_port = urlsplit(self.endpoint).port or 443
        if len(set(ports)) != len(ports) or external_port in ports:
            raise ValueError("listener ports must be distinct")

        # Control, логи и данные CA не должны вкладываться друг в друга:
        # будущая ротация/очистка логов не получает доступ к управлению и ключам.
        directories = [self.control_dir, self.log_dir]
        if self.caddy.data_dir is not None:
            directories.append(self.caddy.data_dir)
        for index, left in enumerate(directories):
            for right in directories[index + 1:]:
                if left == right or left in right.parents or right in left.parents:
                    raise ValueError("deployment directories overlap")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение общего конфига и проверка связанных файлов
#------------------------------------------------------------------------------------------------------------------
def load_deployment_config(path: str | Path) -> DeploymentConfig:

    """Validate local deployment files without acquiring owners, stores or network clients.

    :param path: Deployment JSON file; relative references are based on its directory.
    :type path: str | Path

    :return: Immutable deployment configuration with absolute component paths.
    :rtype: DeploymentConfig
    """

    # path — общий конфиг; вложенные конфиги сохраняют собственную базу относительных путей.

    section = "config"
    try:
        location = Path(path).resolve()
        root = _read_json(location)
        _object(root, {"schema_version", "endpoint", "control_dir", "log_dir"},
                {"notifications", "commands", "caddy", "lifecycle", "restart"})
        if type(root["schema_version"]) is not int or root["schema_version"] != 1:
            raise DeploymentConfigError("config_schema", "schema_version")

        section = "endpoint"
        _origin(root["endpoint"])
        directories = {}
        for section in ("control_dir", "log_dir"):
            directories[section] = _path(location, root[section])
            if directories[section].exists() and not directories[section].is_dir():
                raise DeploymentConfigError("path_unavailable", section)

        components = {}
        for section in ("notifications", "commands"):
            if section not in root:
                continue
            value = _object(root[section], {"config", "port"}, {"host"})
            components[section] = ComponentConfig(config=_path(location, value["config"]),
                                                  port=value["port"], host=value.get("host", "127.0.0.1"))

        section = "caddy"
        value = _object(root.get("caddy", {"mode": "external"}), {"mode"},
                        {"executable", "config", "data_dir"})
        caddy = CaddyConfig(mode=value["mode"], **{
            name: _path(location, item) for name, item in value.items() if name != "mode"})
        if caddy.mode == "managed":
            if not caddy.executable.is_file() or not caddy.config.is_file():
                raise DeploymentConfigError("path_unavailable", section)
            if caddy.data_dir.exists() and not caddy.data_dir.is_dir():
                raise DeploymentConfigError("path_unavailable", section)

        section = "lifecycle"
        value = _object(root.get(section, {}), set(), {"startup_timeout", "shutdown_timeout", "kill_timeout",
                                                     "heartbeat_interval", "heartbeat_timeout"})
        lifecycle = LifecycleConfig(**value)
        section = "restart"
        value = _object(root.get(section, {}), set(), {"max_restarts", "window", "initial_delay", "max_delay"})
        restart = RestartPolicy(**value)

        section = "config"
        deployment = DeploymentConfig(endpoint=root["endpoint"], caddy=caddy, lifecycle=lifecycle,
                                      restart=restart, **directories, **components)
        _check_components(deployment)
        return deployment
    except DeploymentConfigError:
        raise
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        raise DeploymentConfigError("config_value", section) from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Ограниченное чтение строгого JSON
#------------------------------------------------------------------------------------------------------------------
def _read_json(path: Path) -> dict[str, Any]:

    """Read at most one MiB and reject duplicate keys and non-finite JSON constants.

    :param path: Explicit deployment file path.
    :type path: Path

    :return: Parsed JSON object; schema validation follows separately.
    :rtype: dict[str, Any]
    """

    # path — файл с потенциально приватными адресами; его содержимое не входит в ошибки.

    code = "config_read"
    try:
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        code = "config_size"
        if len(data) > 1024 * 1024:
            raise ValueError("configuration too large")
        code = "config_json"
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (OSError, ValueError, RecursionError):
        raise DeploymentConfigError(code, "config") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Отказ при повторном ключе объекта
#------------------------------------------------------------------------------------------------------------------
def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:

    """Build a JSON object without silently replacing earlier keys.

    :param pairs: Object entries supplied by the JSON decoder.
    :type pairs: list[tuple[str, Any]]

    :return: Object with unique keys.
    :rtype: dict[str, Any]
    """

    # pairs — пары JSON до преобразования в словарь, пока дубликаты ещё различимы.

    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Отказ при NaN и Infinity
#------------------------------------------------------------------------------------------------------------------
def _reject_constant(value: str) -> None:

    """Reject nonstandard numeric constants without echoing their source.

    :param value: Decoder token that must not be accepted.
    :type value: str
    """

    # value — нечисловая константа, недопустимая в настройках сроков и пределов.

    raise ValueError("nonstandard JSON constant")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка известных и обязательных полей
#------------------------------------------------------------------------------------------------------------------
def _object(
    value: object,
    required: set[str],
    optional: set[str],
) -> dict[str, Any]:

    """Require an object containing only declared schema keys.

    :param value: Candidate JSON object.
    :type value: object

    :param required: Keys that must be present.
    :type required: set[str]

    :param optional: Additional accepted keys.
    :type optional: set[str]

    :return: Validated object without copying private values into errors.
    :rtype: dict[str, Any]
    """

    # value — проверяемый узел JSON.
    # required — обязательные поля.
    # optional — допустимые дополнительные поля.

    if type(value) is not dict or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("invalid object fields")
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Разрешение пути относительно deployment-файла
#------------------------------------------------------------------------------------------------------------------
def _path(
    location: Path,
    value: object,
) -> Path:

    """Resolve a literal path without environment or home expansion.

    :param location: Absolute deployment file path.
    :type location: Path

    :param value: Literal JSON path string.
    :type value: object

    :return: Absolute normalized path.
    :rtype: Path
    """

    # location — база путей только общего конфига.
    # value — буквальный путь; переменные окружения и ~ не раскрываются.

    require_text(value, "path", max_bytes=4096)
    if any(ord(char) < 32 for char in value):
        raise ValueError("control character in path")
    return (location.parent / value).resolve()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка уже разрешённого пути
#------------------------------------------------------------------------------------------------------------------
def _absolute_path(value: object) -> None:

    """Require an absolute pathlib path for the typed configuration interface.

    :param value: Candidate resolved path.
    :type value: object
    """

    # value — в Python API пути задаются явно, без неявной привязки к cwd.

    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError("absolute Path required")
    if ".." in value.parts:
        raise ValueError("normalized path required")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка внешнего HTTPS origin
#------------------------------------------------------------------------------------------------------------------
def _origin(value: object) -> None:

    """Require an HTTPS origin without credentials, paths, query or fragments.

    :param value: External URL used by gateway clients.
    :type value: object
    """

    # value — адрес для клиентов; он не разрешается через DNS при проверке конфига.

    require_text(value, "endpoint", max_bytes=2048)
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
        raise ValueError("invalid origin characters")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/")
            or parsed.username is not None or parsed.password is not None or "?" in value or "#" in value):
        raise ValueError("HTTPS origin required")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("invalid origin port")
    if parsed.netloc.endswith(":"):
        raise ValueError("empty origin port")

    # Не пропускаем произвольный текст в hostname. IPv6 проверяется отдельно;
    # DNS-имя может быть Unicode, но каждый его IDNA-label должен быть корректным.
    host = parsed.hostname
    if ":" in host:
        ipaddress.IPv6Address(host)
    else:
        encoded = host.encode("idna").decode("ascii")
        labels = encoded.removesuffix(".").split(".")
        if len(encoded) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
                                                     label) for label in labels):
            raise ValueError("invalid origin host")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка существующих схем без открытия ресурсов
#------------------------------------------------------------------------------------------------------------------
def _check_components(config: DeploymentConfig) -> None:

    """Delegate to existing read-only validators, hiding nested paths and secret values.

    :param config: Structurally valid deployment configuration.
    :type config: DeploymentConfig
    """

    # config — проверенные ссылки; загрузчики компонентов не получают новую базу путей.

    from ..gateway.command_config import CommandConfigError, check_command_gateway
    from ..gateway.json_config import GatewayConfigError, load_gateway_config

    for name in ("notifications", "commands"):
        component = getattr(config, name)
        if component is None:
            continue
        try:
            if name == "notifications":
                load_gateway_config(component.config)
            else:
                check_command_gateway(component.config)
        except (GatewayConfigError, CommandConfigError):
            raise DeploymentConfigError("component_config", name) from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.gateway_server.config не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
