# Чтение локальной JSON-конфигурации gateway без исполнения Python-кода.
#
# Version 1.0.6
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-210047
#
# Классы:
# -> GatewayConfigError: Ошибка с фиксированной категорией и разделом схемы.
#    Конструктор:
#    -> __init__(): Создание объекта.
#
# Функции:
# -> load_gateway_config(): Чтение и проверка локального JSON-файла.
# -> _object(): Проверка допустимых и обязательных полей.
# -> _items(): Проверка размера списка настроек.
# -> _unique_object(): Запрет повторяющихся ключей JSON.
# -> _invalid_constant(): Отклонение нестандартных чисел JSON.
# -> _principal(): Создание точных прав одного приложения.
# -> _destination(): Создание настроек выбранного адаптера без сети.
# -> _credential_source(): Проверка явного выбора token либо token_env в JSON.
#
# Константы:
# -> MAX_CONFIG_BYTES: Наибольший размер файла настроек, байт.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from remote_watch._credentials import CREDENTIAL_HINTS, CredentialError
from remote_watch.config import Destination, RetryPolicy
from remote_watch.events import Identity
from remote_watch.gateway.config import GatewayConfig, GatewayPrincipal

#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************
MAX_CONFIG_BYTES = 1024 * 1024


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ошибка с фиксированной категорией и разделом схемы
#------------------------------------------------------------------------------------------------------------------
class GatewayConfigError(ValueError):
    """Expose only fixed validation categories and schema field names."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        code: str,
        field: str,
        reason: str | None = None,
    ) -> None:

        """Keep safe diagnostic labels without the source exception.

        :param code: Fixed error category selected by the loader.
        :type code: str

        :param field: Fixed schema group selected by the loader.
        :type field: str

        :param reason: Optional fixed credential explanation.
        :type reason: str | None
        """

        # code - категория, заданная загрузчиком, а не текстом JSON.
        # field - раздел известной схемы, без неизвестных ключей и значений.
        # reason - фиксированное объяснение ошибки токена.

        self.code = code if code in ("config_read", "config_size", "config_json", "config_fields",
                                      "config_schema", "config_value") else "config_value"
        fields = ("config", "schema_version", "destinations", "principals", "gateway")
        # В подробном пути допустимы только известные поля и ограниченный индекс списка.
        indexed = re.fullmatch(r"(destinations|principals)\[([0-9]{1,3})\](\.settings)?\.(token|token_env)", field)
        safe_path = indexed is not None and (
            indexed[1] == "destinations" and int(indexed[2]) < 64 and indexed[3] == ".settings"
            or indexed[1] == "principals" and int(indexed[2]) < 256 and indexed[3] is None)
        self.field = field if field in fields or safe_path else "config"
        self.reason = reason if isinstance(reason, str) and reason in CREDENTIAL_HINTS else None
        super().__init__(f"Invalid gateway JSON configuration: code={self.code} field={self.field}")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение и проверка локального JSON-файла
#------------------------------------------------------------------------------------------------------------------
def load_gateway_config(path: str | Path) -> GatewayConfig:

    """Read schema version 1 without resolving secrets or opening network clients.

    :param path: Local configuration file.
    :type path: str | Path

    :return: Validated server configuration with lazy provider factories.
    :rtype: GatewayConfig
    """

    # path - путь к локальному файлу настроек.

    # Ограничиваем чтение до разбора JSON. Файл может содержать локальные адреса,
    # поэтому ошибки наружу возвращаются без его содержимого и значений полей.
    code, field = "config_read", "config"
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_CONFIG_BYTES + 1)
        code = "config_size"
        if len(data) > MAX_CONFIG_BYTES:
            raise ValueError("configuration is too large")
        code = "config_json"
        root = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
        code = "config_fields"
        root = _object(root, {"schema_version", "destinations", "principals", "gateway"},
                       {"schema_version", "destinations", "principals"})
        code, field = "config_schema", "schema_version"
        if type(root["schema_version"]) is not int or root["schema_version"] != 1:
            raise ValueError("unsupported configuration schema")

        # Списки проверяются до создания адаптеров; собственные модели повторно
        # проверяют значения, точную Identity, права и единственность имён.
        # Имя этапа задаётся самим загрузчиком; неизвестный ключ из файла не печатается.
        code, field = "config_value", "destinations"
        destinations = []
        for index, item in enumerate(_items(root["destinations"], 64)):
            try:
                destinations.append(_destination(item))
            except CredentialError as error:
                raise GatewayConfigError(
                    code, f"destinations[{index}].settings.{error.field}", error.reason,
                ) from None
        field = "principals"
        principals = []
        for index, item in enumerate(_items(root["principals"], 256)):
            try:
                principals.append(_principal(item))
            except CredentialError as error:
                raise GatewayConfigError(code, f"principals[{index}].{error.field}", error.reason) from None
        field = "gateway"
        options = _object(root.get("gateway", {}), {
            "capacity", "body_timeout", "attempt_timeout", "startup_timeout",
            "shutdown_timeout", "future_tolerance", "clock_skew_tolerance", "destination_interval",
        })
        return GatewayConfig(
            destinations=tuple(destinations),
            principals=tuple(principals),
            **options,
        )
    except GatewayConfigError:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        raise GatewayConfigError(code, field) from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка допустимых и обязательных полей
#------------------------------------------------------------------------------------------------------------------
def _object(
    value: Any,
    allowed: set[str],
    required: set[str] | None = None,
) -> dict[str, Any]:

    """Reject unknown fields and missing required fields in a JSON object.

    :param value: Decoded JSON value.
    :type value: Any

    :param allowed: Allowed field names.
    :type allowed: set[str]

    :param required: Required field names or None.
    :type required: set[str] | None

    :return: Dictionary containing only allowed fields and all required fields.
    :rtype: dict[str, Any]
    """

    # value - значение, прочитанное из JSON.
    # allowed - набор допустимых имён полей.
    # required - обязательные поля либо отсутствие требований.

    if not isinstance(value, dict) or not set(value) <= allowed:
        raise ValueError("invalid configuration object")
    if required is not None and not required <= set(value):
        raise ValueError("missing required fields")
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка размера списка настроек
#------------------------------------------------------------------------------------------------------------------
def _items(
    value: Any,
    maximum: int,
) -> list[Any]:

    """Accept only a nonempty bounded JSON array.

    :param value: Decoded JSON value.
    :type value: Any

    :param maximum: Maximum number of entries.
    :type maximum: int

    :return: Validated nonempty list within the configured item limit.
    :rtype: list[Any]
    """

    # value - значение, прочитанное из JSON.
    # maximum - предельное число элементов.

    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError("invalid configuration array")
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрет повторяющихся ключей JSON
#------------------------------------------------------------------------------------------------------------------
def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:

    """Prevent later duplicate keys from silently replacing access rules.

    :param pairs: Ordered JSON key-value pairs.
    :type pairs: list[tuple[str, Any]]

    :return: Decoded object with distinct JSON keys.
    :rtype: dict[str, Any]
    """

    # pairs - пары ключей и значений до объединения в словарь.

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Отклонение нестандартных чисел JSON
#------------------------------------------------------------------------------------------------------------------
def _invalid_constant(value: str) -> None:

    """Reject nonstandard JSON numeric constants.

    :param value: Decoded JSON value.
    :type value: str
    """

    # value - значение, прочитанное из JSON.

    raise ValueError("nonstandard JSON constant")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание точных прав одного приложения
#------------------------------------------------------------------------------------------------------------------
def _principal(value: Any) -> GatewayPrincipal:

    """Construct one explicitly authorized application identity.

    :param value: Decoded JSON value.
    :type value: Any

    :return: Validated application credential and exact relay permissions.
    :rtype: GatewayPrincipal
    """

    # value - значение, прочитанное из JSON.

    settings = _object(value, {"name", "token", "token_env", "identity", "aliases", "capacity", "min_interval"},
                       {"name", "identity", "aliases"}).copy()
    _credential_source(settings)
    identity_fields = {"service", "environment", "region", "host", "instance_id"}
    settings["identity"] = Identity(**_object(settings["identity"], identity_fields, identity_fields))
    return GatewayPrincipal(**settings)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание настроек выбранного адаптера без сети
#------------------------------------------------------------------------------------------------------------------
def _destination(value: Any) -> Destination:

    """Build a lazy direct destination from the fixed list of supported providers.

    :param value: Decoded JSON value.
    :type value: Any

    :return: Destination configured with a lazy factory for an allowed provider.
    :rtype: Destination
    """

    # value - значение, прочитанное из JSON.

    settings = _object(value, {"alias", "provider", "settings", "timeouts"}, {"alias", "provider", "settings"})
    timeouts = _object(settings.get("timeouts", {}), {"connect_timeout", "attempt_timeout", "ttl"})
    retry = RetryPolicy(max_attempts=1, **timeouts)

    # JSON не выбирает импортируемый модуль или функцию. Произвольные адаптеры
    # остаются доступны через явно доверенную Python-фабрику. Здесь создаются
    # только настройки: literal-токены проверяются сразу, окружение читается при запуске.
    if settings["provider"] == "telegram":
        from remote_watch.adapters.telegram import TelegramConfig

        options = _object(settings["settings"], {
            "token", "token_env", "chat_id", "endpoint", "message_thread_id", "disable_notification", "allow_http",
            "display_mode", "display_fields",
        }, {"chat_id"})
        _credential_source(options)
        provider = TelegramConfig(**options)
    elif settings["provider"] == "ntfy":
        from remote_watch.adapters.ntfy import NtfyConfig

        options = _object(settings["settings"], {
            "topic", "token", "token_env", "endpoint", "title", "priority", "tags", "allow_http",
            "display_mode", "display_fields",
        }, {"topic"})
        _credential_source(options)
        provider = NtfyConfig(**options)
    else:
        raise ValueError("unsupported gateway provider")
    return provider.destination(settings["alias"], retry=retry)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка явного выбора token либо token_env в JSON
#------------------------------------------------------------------------------------------------------------------
def _credential_source(settings: dict[str, Any]) -> None:

    """Require one explicit credential key, preserving anonymous token_env=null.

    :param settings: Validated settings object with known keys.
    :type settings: dict[str, Any]
    """

    # settings - объект со стандартными ключами схемы.

    # Проверяем наличие ключей, а не истинность значений: token вместе с token_env=null
    # тоже неоднозначен. Пустая строка и token=null не включают анонимную отправку.
    if ("token" in settings) == ("token_env" in settings):
        raise CredentialError("token", "source")
    if "token" in settings and settings["token"] is None:
        raise CredentialError("token", "value")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.gateway.json_config не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
