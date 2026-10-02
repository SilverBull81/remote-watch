# Явные JSON-настройки командного сервера и клиента приложения.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-143102
#
# Классы:
# -> CommandConfigError: Безопасная ошибка с названием раздела настроек.
#    Конструктор:
#    -> __init__(): Создание объекта.
#
# -> ProviderBinding: Связь политики источника с настройками провайдера.
#    Специальные методы:
#    -> __post_init__(): Проверка и фиксация настроек.
#
# -> CommandGatewayConfig: Настройки отдельного командного сервера.
#    Специальные методы:
#    -> __post_init__(): Проверка и фиксация настроек.
#
# Функции:
# -> load_command_gateway(): Чтение и проверка серверных настроек.
# -> check_command_gateway(): Проверка структуры без чтения окружения и сетевых запросов.
# -> load_command_client(): Подготовка клиента с новой сессией и постоянным журналом.
# -> read_command_json(): Чтение ограниченного строгого JSON.
# -> _gateway(): Проверка разделов серверного JSON.
# -> _credential(): Проверка и разрешение выбранного секрета.
# -> _path(): Путь относительно файла настроек.
# -> _schema(): Проверка версии командных настроек.
# -> _keys(): Проверка обязательных и лишних полей.
# -> _items(): Проверка конечного списка настроек.
# -> _pairs(): Отказ от повторяющихся ключей JSON.
# -> _constant(): Отказ от нечисловых JSON-констант.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_hex
from typing import Any

from remote_watch._credentials import CredentialError, resolve_token, validate_credentials
from remote_watch.adapters.command_http import HttpsCommandTransport
from remote_watch.adapters.ntfy_commands import NtfyCommandConfig
from remote_watch.adapters.telegram_commands import TelegramCommandConfig
from remote_watch.commands.client import CommandClient
from remote_watch.commands.hub_config import CommandAccess, CommandHubConfig, CommandPrincipal, CommandSource
from remote_watch.commands.protocol import CommandRegistration, describe_commands
from remote_watch.commands.registry import CommandRegistry
from remote_watch.commands.source_protocol import SourceTargets
from remote_watch.commands.sqlite_store import SQLiteCommandStore
from remote_watch.commands.storage import StoreRole
from remote_watch.events import Identity


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Безопасная ошибка с названием раздела настроек
#------------------------------------------------------------------------------------------------------------------
class CommandConfigError(ValueError):
    """Expose only a known configuration stage without values or nested exception details."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        field: str,
        code: str = "config_value",
    ) -> None:

        """Restrict the public diagnostic to known configuration sections.

        :param field: Known configuration section used for a fixed diagnostic.
        :type field: str

        :param code: Fixed failure category without configuration values.
        :type code: str
        """

        # field — известный раздел настроек для безопасной диагностики.
        # code — фиксированная причина, не содержащая секретов или пользовательских имён.

        pattern = (r"(?:config|schema|targets|hub|state_dir|"
                   r"client(?:\.(?:token|token_env|identity|commands|endpoint|ca_file|state_file|owner_id))?|"
                   r"principals(?:\[[0-9]{1,2}\])?(?:\.(?:token|token_env|name|targets|scopes))?|"
                   r"sources(?:\[[0-9]\])?(?:\.source_id|\.settings(?:\.(?:token|token_env))?|"
                   r"\.access(?:\[[0-9]{1,3}\])?(?:\.targets)?)?)")
        self.field = field if type(field) is str and re.fullmatch(pattern, field) else "config"
        known = {"config_value", "config_read", "config_json", "config_size", "config_schema",
                 "duplicate_principal_token", "duplicate_principal_name", "duplicate_identity",
                 "unknown_target", "unassigned_target", "duplicate_source", "duplicate_provider",
                 "credential_source", "credential_environment", "credential_value", "source_acl",
                 "ca_read", "ca_invalid", "endpoint_invalid"}
        self.code = code if type(code) is str and code in known else "config_value"
        self.hint = {
            "duplicate_principal_token": "У каждого приложения должен быть свой командный токен.",
            "duplicate_principal_name": "Имена principals должны различаться.",
            "duplicate_identity": "Каждая Identity должна принадлежать ровно одному principal.",
            "unknown_target": "Указанная цель отсутствует в разделе targets.",
            "unassigned_target": "Для каждой цели назначьте principal с правами приложения.",
            "duplicate_source": "Имена source_id должны различаться.",
            "duplicate_provider": "Для одного bot или топика допускается только один источник.",
            "source_acl": "Проверьте actor_id и conversation_id; для Telegram нужны числовые ID строками.",
            "credential_source": "Укажите ровно один источник: token или token_env.",
            "credential_environment": "В token_env нужно имя переменной; сам токен укажите в token.",
            "credential_value": "Проверьте наличие и формат токена в файле или выбранной переменной.",
            "ca_read": "Файл CA недоступен; относительный путь считается от файла настроек.",
            "ca_invalid": "Файл CA не содержит подходящего сертификата PEM.",
            "endpoint_invalid": "Нужен HTTPS-адрес gateway без credentials и пути операции.",
            "config_read": "Проверьте путь к JSON и права чтения файла.",
            "config_json": "Проверьте UTF-8, синтаксис JSON и отсутствие повторяющихся ключей.",
            "config_size": "Размер JSON не должен превышать 1 MiB.",
            "config_schema": "В schema_version требуется целое число 1.",
        }.get(self.code, "Проверьте указанное поле настроек.")
        super().__init__(f"invalid command configuration: code={self.code} field={self.field}")
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Связь политики источника с настройками провайдера
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderBinding:
    """Pair one source policy with the provider settings that establish its actor identity."""

    source_id: str                              # Постоянный ID источника в журнале hub.
    settings: TelegramCommandConfig | NtfyCommandConfig = field(repr=False)     # Проверенные настройки провайдера.


    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка и фиксация настроек
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject unsupported providers and unsafe source filenames in typed configuration."""

        if type(self.source_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.source_id) is None:
            raise ValueError("invalid source binding")

        if type(self.settings) not in (TelegramCommandConfig, NtfyCommandConfig):
            raise TypeError("invalid provider settings")
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки отдельного командного сервера
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandGatewayConfig:
    """Compose typed command-only gateway settings without starting network activity."""

    state_dir: Path                             # Постоянный каталог журналов; общий для перезапусков.
    hub: CommandHubConfig = field(repr=False)   # Отдельные секреты приложений и права команд.
    targets: SourceTargets                      # Явные адреса всех приложений.
    providers: tuple[ProviderBinding, ...]      # Не более восьми отдельных источников.


    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка и фиксация настроек
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject ambiguous provider bindings before starting the server."""

        if type(self.hub) is not CommandHubConfig or type(self.targets) is not SourceTargets:
            raise TypeError("invalid command gateway config")

        if not 1 <= len(self.providers) <= 8 or any(type(item) is not ProviderBinding for item in self.providers):
            raise ValueError("invalid provider bindings")

        if len({item.source_id for item in self.providers}) != len(self.providers):
            raise ValueError("duplicate command provider")

        if {item.source_id for item in self.providers} != {source.source_id for source in self.hub.sources}:
            raise ValueError("missing command provider")
        identities = {identity for principal in self.hub.principals for identity in principal.identities}

        if identities != set(self.targets.aliases.values()):
            raise ValueError("command target mismatch")
        object.__setattr__(self, "state_dir", Path(self.state_dir))
        object.__setattr__(self, "providers", tuple(self.providers))
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение и проверка серверных настроек
#------------------------------------------------------------------------------------------------------------------
def load_command_gateway(path: str | Path) -> CommandGatewayConfig:

    """Read explicit server settings, resolving principal environment references only on this call.

    :param path: Explicit local data path or fixed HTTP operation path.
    :type path: str | Path

    :return: Validated command-only gateway configuration.
    :rtype: CommandGatewayConfig
    """

    # path — явный путь локальных данных либо фиксированный путь HTTP-операции.

    return _gateway(Path(path), True)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка структуры без чтения окружения и сетевых запросов
#------------------------------------------------------------------------------------------------------------------
def check_command_gateway(path: str | Path) -> None:

    """Validate server structure without resolving environment values or contacting providers.

    :param path: Explicit local data path or fixed HTTP operation path.
    :type path: str | Path
    """

    # path — явный путь локальных данных либо фиксированный путь HTTP-операции.

    _gateway(Path(path), False)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка клиента с новой сессией и постоянным журналом
#------------------------------------------------------------------------------------------------------------------
def load_command_client(
    path: str | Path,
    commands: CommandRegistry,
) -> CommandClient:

    """Build one fresh-session client from local settings while retaining its durable journal path.

    :param path: Explicit local data path or fixed HTTP operation path.
    :type path: str | Path

    :param commands: Application-owned immutable callback registry.
    :type commands: CommandRegistry

    :return: Fresh-session client retaining the configured durable journal.
    :rtype: CommandClient
    """

    # path — явный путь локальных данных либо фиксированный путь HTTP-операции.
    # commands — реестр обработчиков, заданный приложением.

    field_name = "client"
    code = "config_value"

    try:
        location = Path(path).resolve()
        value = read_command_json(location)
        _keys(value, {"schema_version", "identity", "endpoint", "state_file", "owner_id"},
              {"token", "token_env", "ca_file", "allow_loopback_http"})
        _schema(value)
        token = _credential(value, True)
        field_name = "client.identity"
        identity = Identity(**value["identity"])
        session = token_hex(16)
        field_name = "client.commands"
        registration = CommandRegistration(
            identity=identity, session_id=session, capabilities=describe_commands(commands))
        context = ssl.create_default_context()
        if value.get("ca_file") is not None:
            field_name = "client.ca_file"
            try:
                context.load_verify_locations(str(_path(location, value["ca_file"])))
            except OSError as error:
                code = "ca_invalid" if isinstance(error, ssl.SSLError) else "ca_read"
                raise CommandConfigError(field_name, code) from None
        field_name, code = "client.endpoint", "endpoint_invalid"
        transport = HttpsCommandTransport(value["endpoint"], token, ssl_context=context,
                                         allow_loopback_http=value.get("allow_loopback_http", False))
        field_name, code = "client.state_file", "config_value"
        state_file = _path(location, value["state_file"])
        field_name = "client.owner_id"
        store = SQLiteCommandStore(state_file, owner_id=value["owner_id"],
                                   generation=session, role=StoreRole.CLIENT)
        return CommandClient(registration, transport, store)
    except CommandConfigError:
        raise
    except CredentialError as error:
        raise CommandConfigError("client." + error.field, "credential_" + error.reason) from None
    except Exception:
        raise CommandConfigError(field_name, code) from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение ограниченного строгого JSON
#------------------------------------------------------------------------------------------------------------------
def read_command_json(path: Path) -> dict[str, Any]:

    """Read at most one MiB of strict JSON without importing Python factories or revealing values.

    :param path: Explicit local data path or fixed HTTP operation path.
    :type path: Path

    :return: Strict JSON object read from the bounded configuration file.
    :rtype: dict[str, Any]
    """

    # path — явный путь локальных данных либо фиксированный путь HTTP-операции.

    try:
        with path.open("rb") as stream:
            body = stream.read(1048577)
    except OSError:
        raise CommandConfigError("config", "config_read") from None
    try:
        if len(body) > 1048576:
            raise CommandConfigError("config", "config_size")
        value = json.loads(body.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict:
            raise ValueError("configuration object")
        return value
    except CommandConfigError:
        raise
    except Exception:
        raise CommandConfigError("config", "config_json") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка разделов серверного JSON
#------------------------------------------------------------------------------------------------------------------
def _gateway(
    path: Path,
    resolve_environment: bool,
) -> CommandGatewayConfig:

    """Validate nested server sections using fixed error paths and exact allowed keys.

    :param path: Explicit local data path or fixed HTTP operation path.
    :type path: Path

    :param resolve_environment: Whether environment credentials may be resolved now.
    :type resolve_environment: bool

    :return: Validated command-only gateway configuration.
    :rtype: CommandGatewayConfig
    """

    # path — явный путь локальных данных либо фиксированный путь HTTP-операции.
    # resolve_environment — разрешать ли сейчас ссылки на переменные окружения.

    field_name = "config"

    try:
        path = path.resolve()
        value = read_command_json(path)
        _keys(value, {"schema_version", "state_dir", "targets", "principals", "sources"}, {"hub"})
        field_name = "schema"
        _schema(value)
        field_name = "targets"
        # Адрес из сообщения не заменяет Identity. Проверяем полную таблицу целей
        # прежде, чем выдавать кому-либо права на одно из этих приложений.
        targets = SourceTargets(aliases={
            name: Identity(**identity) for name, identity in value["targets"].items()})
        principals = []
        seen_tokens: set[str] = set()
        seen_environment: set[str] = set()
        seen_names: set[str] = set()
        seen_identities: set[Identity] = set()
        field_name = "principals"
        # У каждой клиентской Identity один владелец и отдельный секрет команд.
        # Ссылки на окружение разрешаются только при настоящем запуске, не в check.
        for index, raw in enumerate(_items(value["principals"], 64)):
            field_name = f"principals[{index}]"
            _keys(raw, {"name", "targets", "scopes"}, {"token", "token_env"})
            token = _credential(raw, resolve_environment)
            environment = raw.get("token_env")
            if token in seen_tokens or (environment is not None and environment in seen_environment):
                suffix = ".token_env" if environment is not None else ".token"
                raise CommandConfigError(field_name + suffix, "duplicate_principal_token")
            seen_tokens.add(token)
            if environment is not None:
                seen_environment.add(environment)

            field_name = f"principals[{index}].targets"
            names = _items(raw["targets"], 64)
            if any(type(name) is not str or name not in targets.aliases for name in names):
                raise CommandConfigError(field_name, "unknown_target")
            identities = tuple(targets.aliases[name] for name in names)
            if len(set(identities)) != len(identities) or seen_identities.intersection(identities):
                raise CommandConfigError(field_name, "duplicate_identity")
            seen_identities.update(identities)
            field_name = f"principals[{index}]"
            principal = CommandPrincipal(name=raw["name"], token=token, identities=identities,
                                         scopes=frozenset(_items(raw["scopes"], 128)))
            if principal.name in seen_names:
                raise CommandConfigError(field_name + ".name", "duplicate_principal_name")
            seen_names.add(principal.name)
            principals.append(principal)

        if seen_identities != set(targets.aliases.values()):
            raise CommandConfigError("targets", "unassigned_target")
        sources = []
        providers = []
        seen_providers = set()
        seen_sources: set[str] = set()
        field_name = "sources"
        # Provider задаёт способ удостоверения автора. Ни текст сообщения, ни
        # параметры клиентского HTTP-запроса не могут подменить эту политику.
        for index, raw in enumerate(_items(value["sources"], 8)):
            field_name = f"sources[{index}]"
            _keys(raw, {"source_id", "provider", "settings", "access"}, set())
            if raw["source_id"] in seen_sources:
                raise CommandConfigError(field_name + ".source_id", "duplicate_source")
            seen_sources.add(raw["source_id"])
            field_name += ".settings"
            settings = raw["settings"]
            if raw["provider"] == "telegram":
                _keys(settings, set(), {"token", "token_env", "endpoint", "allow_loopback_http"})
                config = TelegramCommandConfig(**settings)
                # Даже разные source_id не должны порождать второго getUpdates для одного bot.
                marker = ("telegram", config.endpoint, config.token or config.token_env)
            elif raw["provider"] == "ntfy":
                _keys(settings, {"topic", "reply_topic", "actor_id", "private_topic_confirmed"},
                      {"token", "token_env", "endpoint", "allow_loopback_http"})
                config = NtfyCommandConfig(**settings)
                marker = ("ntfy", config.endpoint, config.topic)
            else:
                raise ValueError("unsupported command provider")
            if marker in seen_providers:
                raise CommandConfigError(field_name, "duplicate_provider")
            seen_providers.add(marker)
            access = []
            # Короткий список адресов в JSON разворачивается в точные ACL-записи.
            # Ограничиваем размер по мере разворачивания, до создания всего списка.
            field_name = f"sources[{index}].access"
            for rule_index, rule in enumerate(_items(raw["access"], 256)):
                field_name = f"sources[{index}].access[{rule_index}]"
                _keys(rule, {"actor_id", "conversation_id", "targets", "scopes"}, set())
                for name in _items(rule["targets"], 64):
                    if type(name) is not str or name not in targets.aliases:
                        raise CommandConfigError(field_name + ".targets", "unknown_target")
                    if len(access) >= 256:
                        raise ValueError("source access capacity")
                    access.append(CommandAccess(actor_id=rule["actor_id"], conversation_id=rule["conversation_id"],
                        identity=targets.aliases[name], scopes=frozenset(_items(rule["scopes"], 128))))
                if isinstance(config, NtfyCommandConfig):
                    if rule["actor_id"] != config.actor_id or rule["conversation_id"] != config.topic:
                        raise CommandConfigError(field_name, "source_acl")
                elif (type(rule["actor_id"]) is not str or not re.fullmatch(r"[1-9][0-9]{0,19}", rule["actor_id"])
                      or type(rule["conversation_id"]) is not str
                      or not re.fullmatch(r"-?[0-9]{1,20}", rule["conversation_id"])):
                    raise CommandConfigError(field_name, "source_acl")
            field_name = f"sources[{index}].source_id"
            sources.append(CommandSource(source_id=raw["source_id"], token=token_hex(32), access=tuple(access)))
            providers.append(ProviderBinding(source_id=raw["source_id"], settings=config))
        field_name = "hub"
        # Только известные конечные пределы: неизвестное поле не должно молча
        # оставить более широкое значение по умолчанию вместо ожидаемого запрета.
        limits = value.get("hub", {})
        _keys(limits, set(), {"session_ttl", "poll_timeout", "storage_timeout", "shutdown_timeout",
                             "refresh_interval", "max_sessions", "max_pending"})
        hub = CommandHubConfig(principals=tuple(principals), sources=tuple(sources), **limits)
        field_name = "state_dir"
        return CommandGatewayConfig(state_dir=_path(path, value["state_dir"]), hub=hub,
                                    targets=targets, providers=tuple(providers))
    except CommandConfigError:
        raise
    except CredentialError as error:
        raise CommandConfigError(field_name + "." + error.field, "credential_" + error.reason) from None
    except Exception:
        raise CommandConfigError(field_name) from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка и разрешение выбранного секрета
#------------------------------------------------------------------------------------------------------------------
def _credential(
    value: dict[str, Any],
    resolve_environment: bool,
) -> str:

    """Validate one literal or environment credential without exposing its origin or value.

    :param value: Untrusted configuration or provider value under validation.
    :type value: dict[str, Any]

    :param resolve_environment: Whether environment credentials may be resolved now.
    :type resolve_environment: bool

    :return: Resolved application command credential, or a validation-only surrogate.
    :rtype: str
    """

    # value — проверяемое значение настроек либо ответа провайдера.
    # resolve_environment — разрешать ли сейчас ссылки на переменные окружения.

    token, token_env = value.get("token"), value.get("token_env")
    validate_credentials(token, token_env, "gateway")

    if resolve_environment or token is not None:
        return resolve_token(token, token_env, "gateway")
    # Суррогат используется только внутри check_command_gateway и никогда не возвращается
    # вызывающему коду. Настоящий loader всегда разрешает переменные окружения.
    return token_hex(32)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Путь относительно файла настроек
#------------------------------------------------------------------------------------------------------------------
def _path(
    location: Path,
    value: str,
) -> Path:

    """Resolve local data paths relative to the configuration file rather than the shell directory.

    :param location: Absolute path of the configuration file.
    :type location: Path

    :param value: Untrusted configuration or provider value under validation.
    :type value: str

    :return: Resolved local path relative to its configuration file.
    :rtype: Path
    """

    # location — абсолютный путь файла настроек.
    # value — проверяемое значение настроек либо ответа провайдера.

    if type(value) is not str or not value.strip() or "\0" in value:
        raise ValueError("invalid configuration path")
    return (location.parent / value).resolve()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка версии командных настроек
#------------------------------------------------------------------------------------------------------------------
def _schema(value: dict[str, Any]) -> None:

    """Require the command-specific JSON schema without accepting a Boolean as its version.

    :param value: Untrusted configuration or provider value under validation.
    :type value: dict[str, Any]
    """

    # value — проверяемое значение настроек либо ответа провайдера.

    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise CommandConfigError("schema", "config_schema")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка обязательных и лишних полей
#------------------------------------------------------------------------------------------------------------------
def _keys(
    value: Any,
    required: set[str],
    optional: set[str],
) -> None:

    """Reject missing and unknown keys before constructing typed objects.

    :param value: Untrusted configuration or provider value under validation.
    :type value: Any

    :param required: Required JSON object keys.
    :type required: set[str]

    :param optional: Allowed optional object keys.
    :type optional: set[str]
    """

    # value — проверяемое значение настроек либо ответа провайдера.
    # required — обязательные поля JSON-объекта.
    # optional — допустимые необязательные поля объекта.

    if type(value) is not dict or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("invalid configuration keys")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка конечного списка настроек
#------------------------------------------------------------------------------------------------------------------
def _items(
    value: Any,
    maximum: int,
) -> list[Any]:

    """Require a nonempty bounded JSON list instead of accepting arbitrary iterables.

    :param value: Untrusted configuration or provider value under validation.
    :type value: Any

    :param maximum: Maximum permitted number of JSON list items.
    :type maximum: int

    :return: Validated bounded JSON list.
    :rtype: list[Any]
    """

    # value — проверяемое значение настроек либо ответа провайдера.
    # maximum — наибольшее допустимое число элементов списка JSON.

    if type(value) is not list or not 1 <= len(value) <= maximum:
        raise ValueError("invalid configuration list")
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Отказ от повторяющихся ключей JSON
#------------------------------------------------------------------------------------------------------------------
def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:

    """Reject repeated JSON object keys at every nesting level.

    :param pairs: Field pairs from one JSON object.
    :type pairs: list[tuple[str, Any]]

    :return: Object assembled from unique JSON keys.
    :rtype: dict[str, Any]
    """

    # pairs — пары полей одного JSON-объекта, включая возможные повторы.

    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Отказ от нечисловых JSON-констант
#------------------------------------------------------------------------------------------------------------------
def _constant(value: str) -> None:

    """Reject nonfinite JSON constants without including them in diagnostics.

    :param value: Untrusted configuration or provider value under validation.
    :type value: str
    """

    # value — проверяемое значение настроек либо ответа провайдера.

    raise ValueError("invalid JSON constant")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль command_config не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
