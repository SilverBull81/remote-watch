# Чтение локальной JSON-конфигурации gateway без исполнения Python-кода.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260929-180009
#
# Функции:
# -> load_gateway_config(): Чтение и проверка локального JSON-файла.
# -> _object(): Проверка допустимых и обязательных полей.
# -> _items(): Проверка размера списка настроек.
# -> _unique_object(): Запрет повторяющихся ключей JSON.
# -> _invalid_constant(): Отклонение нестандартных чисел JSON.
# -> _principal(): Создание точных прав одного приложения.
# -> _destination(): Создание настроек выбранного адаптера без сети.
#
# Константы:
# -> MAX_CONFIG_BYTES: Наибольший размер файла настроек, байт.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Destination, RetryPolicy
from .events import Identity
from .gateway_config import GatewayConfig, GatewayPrincipal

#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************
MAX_CONFIG_BYTES = 1024 * 1024


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
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise ValueError("configuration is too large")
        root = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
        root = _object(root, {"schema_version", "destinations", "principals", "gateway"},
                       {"schema_version", "destinations", "principals"})
        if type(root["schema_version"]) is not int or root["schema_version"] != 1:
            raise ValueError("unsupported configuration schema")

        # Списки проверяются до создания адаптеров; собственные модели повторно
        # проверяют значения, точную Identity, права и единственность имён.
        destinations = _items(root["destinations"], 64)
        principals = _items(root["principals"], 256)
        options = _object(root.get("gateway", {}), {
            "capacity", "body_timeout", "attempt_timeout", "startup_timeout",
            "shutdown_timeout", "future_tolerance", "destination_interval",
        })
        return GatewayConfig(
            destinations=tuple(_destination(item) for item in destinations),
            principals=tuple(_principal(item) for item in principals),
            **options,
        )
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValueError("Invalid gateway JSON configuration; check schema, fields and file access.") from None
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

    :return: The value described by this operation.
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

    :return: The value described by this operation.
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

    :return: The value described by this operation.
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

    :return: The value described by this operation.
    :rtype: GatewayPrincipal
    """

    # value - значение, прочитанное из JSON.

    settings = _object(value, {"name", "token_env", "identity", "aliases", "capacity", "min_interval"},
                       {"name", "token_env", "identity", "aliases"}).copy()
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

    :return: The value described by this operation.
    :rtype: Destination
    """

    # value - значение, прочитанное из JSON.

    settings = _object(value, {"alias", "provider", "settings", "timeouts"}, {"alias", "provider", "settings"})
    timeouts = _object(settings.get("timeouts", {}), {"connect_timeout", "attempt_timeout", "ttl"})
    retry = RetryPolicy(max_attempts=1, **timeouts)

    # JSON не выбирает импортируемый модуль или функцию. Произвольные адаптеры
    # остаются доступны через явно доверенную Python-фабрику. Здесь создаются
    # только настройки: токены будут прочитаны позже, при запуске gateway.
    if settings["provider"] == "telegram":
        from .adapters.telegram import TelegramConfig

        provider = TelegramConfig(**_object(settings["settings"], {
            "token_env", "chat_id", "endpoint", "message_thread_id", "disable_notification", "allow_http",
        }, {"token_env", "chat_id"}))
    elif settings["provider"] == "ntfy":
        from .adapters.ntfy import NtfyConfig

        provider = NtfyConfig(**_object(settings["settings"], {
            "topic", "token_env", "endpoint", "title", "priority", "tags", "allow_http",
        }, {"topic", "token_env"}))
    else:
        raise ValueError("unsupported gateway provider")
    return provider.destination(settings["alias"], retry=retry)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.gateway_json не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
