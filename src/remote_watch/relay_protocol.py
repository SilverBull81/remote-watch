# Формат запроса и ответа одной попытки доставки через gateway.
#
# Version 1.0.4
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-221259
#
# Классы:
# -> RelayRequest: Версионированный запрос одной попытки через gateway.
#    Интерфейс:
#    -> to_dict(): Данные запроса без секретов и локальных настроек.
#    -> from_bytes(): Проверка входящего JSON и создание запроса.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек и ограничений.
#
# Функции:
# -> validate_alias(): Проверка логического имени получателя на gateway.
# -> _object(): Запрет повторяющихся ключей JSON.
# -> _decode(): Разбор ограниченного JSON без неоднозначных полей.
# -> _keys(): Проверка версии и точного состава envelope.
# -> encode_json(): Единое кодирование проверяемых и отправляемых данных.
# -> encode_response(): Кодирование безопасного результата с корреляцией попытки.
# -> decode_response(): Проверка ответа и его принадлежности текущей попытке.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from remote_watch._validation import require_number
from remote_watch.events import Notification
from remote_watch.notifications.delivery import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    ResultSource,
)

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 4096
DIAGNOSTIC_FIELDS = ("http_status", "provider_code", "message_bytes", "request_bytes")


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Единое кодирование проверяемых и отправляемых данных
#------------------------------------------------------------------------------------------------------------------
def encode_json(payload: object) -> str:

    """Encode both validated and transmitted envelopes identically.

    :param payload: JSON-compatible protocol data.
    :type payload: object

    :return: Compact UTF-8-compatible JSON text.
    :rtype: str
    """

    # payload - данные протокола; значения не записываются в диагностику.

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка логического имени получателя на gateway
#------------------------------------------------------------------------------------------------------------------
def validate_alias(alias: str) -> None:

    """Validate a logical gateway alias that cannot carry a URL or provider address.

    :param alias: Authorized gateway destination name.
    :type alias: str
    """

    # alias - логическое имя получателя на gateway.

    if not isinstance(alias, str) or re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", alias) is None:
        raise ValueError("relay alias must be a short ASCII identifier")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрет повторяющихся ключей JSON
#------------------------------------------------------------------------------------------------------------------
def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:

    """Reject duplicate JSON keys rather than choosing an ambiguous value.

    :param pairs: JSON object key/value pairs.
    :type pairs: list[tuple[str, object]]

    :return: JSON object with distinct field names.
    :rtype: dict[str, object]
    """

    # pairs - пары ключей и значений JSON.

    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Разбор ограниченного JSON без неоднозначных полей
#------------------------------------------------------------------------------------------------------------------
def _decode(
    data: bytes,
    limit: int,
) -> dict[str, object]:

    """Parse bounded UTF-8 JSON with one unambiguous object at its root.

    :param data: Bounded UTF-8 JSON bytes.
    :type data: bytes

    :param limit: Maximum accepted body size in bytes.
    :type limit: int

    :return: Validated JSON object within the requested byte limit.
    :rtype: dict[str, object]
    """

    # data - байты JSON с ограниченным размером.
    # limit - предел размера тела, байты.

    if not isinstance(data, bytes) or len(data) > limit:
        raise ValueError("relay body exceeds its byte limit")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid relay JSON") from None
    if not isinstance(value, dict):
        raise ValueError("relay body must be an object")
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка версии и точного состава envelope
#------------------------------------------------------------------------------------------------------------------
def _keys(
    payload: dict[str, object],
    expected: set[str],
) -> None:

    """Require an exact envelope shape and a supported integer schema version.

    :param payload: Decoded JSON envelope.
    :type payload: dict[str, object]

    :param expected: Required envelope keys.
    :type expected: set[str]
    """

    # payload - данные запроса или ответа после разбора JSON.
    # expected - точный набор допустимых полей.

    if set(payload) != expected:
        raise ValueError("relay envelope has missing or unknown fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] not in (1, 2, 3):
        raise ValueError("unsupported relay schema version")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Версионированный запрос одной попытки через gateway
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class RelayRequest:
    """Describe one attempt, never asynchronous acceptance or a durable receipt."""

    delivery: Delivery                  # Подготовленная попытка с неизменяемым уведомлением.
    alias: str                          # Разрешённое на gateway имя получателя.
    remaining_ttl: float                # Верхний предел оставшегося срока, секунды.
    timeout: float                      # Максимальное ожидание gateway, секунды.
    schema_version: int = 1             # 1 — исходная схема; 2 — диагностика; 3 — отображение клиента.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Данные запроса без секретов и локальных настроек
    #--------------------------------------------------------------------------------------------------------------
    def to_dict(self) -> dict[str, object]:

        """Create a JSON-ready request with no provider or service credentials.

        :return: JSON-compatible request fields without transport credentials.
        :rtype: dict[str, object]
        """

        payload = {"schema_version": self.schema_version, "notification": self.delivery.notification.to_dict(),
                   "alias": self.alias, "delivery_id": self.delivery.delivery_id,
                   "attempt": self.delivery.attempt, "remaining_ttl": self.remaining_ttl, "timeout": self.timeout}
        if self.schema_version == 3:
            # Политика только отображения, без адресов, токенов или шаблонов кода.
            # null сохраняет настройку сервера; fields=null выбирает состав режима.
            payload["display"] = (None if self.delivery.display_mode is None else {
                "mode": self.delivery.display_mode,
                "fields": None if self.delivery.display_fields is None else list(self.delivery.display_fields)})
        return payload
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка входящего JSON и создание запроса
    #--------------------------------------------------------------------------------------------------------------
    @classmethod
    def from_bytes(
        cls,
        data: bytes,
    ) -> RelayRequest:

        """Decode a bounded request; authentication, ACL and expiry checks belong to the gateway.

        :param data: Bounded UTF-8 JSON bytes.
        :type data: bytes

        :return: Validated relay request reconstructed from its wire fields.
        :rtype: RelayRequest
        """

        # data - байты JSON с ограниченным размером.

        payload = _decode(data, MAX_REQUEST_BYTES)
        expected = {"schema_version", "notification", "alias", "delivery_id", "attempt",
                    "remaining_ttl", "timeout"}
        if payload.get("schema_version") == 3:
            expected.add("display")
        _keys(payload, expected)
        try:
            display = payload.get("display")
            if display is not None:
                if (type(display) is not dict or set(display) != {"mode", "fields"}
                        or type(display["mode"]) is not str):
                    raise ValueError("invalid relay display")
                if display["fields"] is not None and type(display["fields"]) is not list:
                    raise ValueError("invalid relay display fields")
            return cls(alias=payload["alias"], remaining_ttl=payload["remaining_ttl"], timeout=payload["timeout"],
                       schema_version=payload["schema_version"],
                       delivery=Delivery(notification=Notification.from_dict(payload["notification"]),
                                         destination_id=payload["alias"], delivery_id=payload["delivery_id"],
                                         attempt=payload["attempt"],
                                         display_mode=None if display is None else display["mode"],
                                         display_fields=None if display is None else display["fields"]))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid relay request") from None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек и ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate budgets and bound the complete serialized request."""

        if not isinstance(self.delivery, Delivery):
            raise TypeError("delivery must be Delivery")
        if type(self.schema_version) is not int or self.schema_version not in (1, 2, 3):
            raise ValueError("unsupported relay schema version")
        if self.schema_version != 3 and self.delivery.display_mode is not None:
            raise ValueError("client display requires relay schema 3")

        # Все версии wire используют стандартные пределы Notification. Увеличенные
        # локальные SnapshotLimits не расширяют доверие принимающей стороны.
        # Повторная проверка на клиенте исключает заведомо бесполезный сетевой запрос.
        Notification.from_dict(self.delivery.notification.to_dict())
        validate_alias(self.alias)
        require_number(self.remaining_ttl, "remaining_ttl")
        require_number(self.timeout, "timeout")
        if self.timeout > self.remaining_ttl:
            raise ValueError("relay timeout exceeds remaining TTL")
        encoded = encode_json(self.to_dict()).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError("relay request exceeds its byte limit")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Кодирование безопасного результата с корреляцией попытки
#------------------------------------------------------------------------------------------------------------------
def encode_response(
    delivery: Delivery,
    result: DeliveryResult,
    *,
    schema_version: int = 1,
) -> bytes:

    """Encode a correlated relay response without arbitrary provider error text.

    :param delivery: Immutable delivery attempt.
    :type delivery: Delivery

    :param result: Validated attempt result.
    :type result: DeliveryResult

    :param schema_version: Version explicitly selected by the request.
    :type schema_version: int

    :return: UTF-8 JSON response describing the delivery outcome.
    :rtype: bytes
    """

    # delivery - подготовленные данные одной попытки.
    # result - проверенный результат одной попытки.
    # schema_version - версия запроса; сервер отвечает в том же формате.

    if not isinstance(delivery, Delivery) or not isinstance(result, DeliveryResult):
        raise TypeError("delivery and result must use validated models")
    if type(schema_version) is not int or schema_version not in (1, 2, 3):
        raise ValueError("unsupported relay schema version")
    payload = {"schema_version": schema_version, "delivery_id": delivery.delivery_id, "attempt": delivery.attempt,
               "result": {"status": result.status.value, "reason_code": result.reason_code,
                          "provider_message_id": result.provider_message_id, "retry_after": result.retry_after}}
    if schema_version in (2, 3):
        payload["result"].update({name: getattr(result, name) for name in DIAGNOSTIC_FIELDS})
    data = encode_json(payload).encode("utf-8")
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("relay response exceeds its byte limit")
    return data
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка ответа и его принадлежности текущей попытке
#------------------------------------------------------------------------------------------------------------------
def decode_response(
    payload: object,
    delivery: Delivery,
    *,
    schema_version: int = 1,
) -> DeliveryResult:

    """Require an exact versioned response correlated to the attempted delivery.

    :param payload: Decoded JSON envelope.
    :type payload: object

    :param delivery: Immutable delivery attempt.
    :type delivery: Delivery

    :param schema_version: Version selected for this attempt, without automatic fallback.
    :type schema_version: int

    :return: Validated delivery outcome from the relay response.
    :rtype: DeliveryResult
    """

    # payload - данные запроса или ответа после разбора JSON.
    # delivery - подготовленные данные одной попытки.
    # schema_version - версия текущей попытки; тихое понижение не допускается.

    if not isinstance(payload, dict):
        raise ValueError("relay response must be an object")
    _keys(payload, {"schema_version", "delivery_id", "attempt", "result"})
    if type(schema_version) is not int or payload["schema_version"] != schema_version:
        raise ValueError("relay response version mismatch")
    if payload["delivery_id"] != delivery.delivery_id or type(payload["attempt"]) is not int:
        raise ValueError("relay response correlation mismatch")
    if payload["attempt"] != delivery.attempt:
        raise ValueError("relay response attempt mismatch")
    result = payload["result"]
    expected = {"status", "reason_code", "provider_message_id", "retry_after"}
    if schema_version in (2, 3):
        expected.update(DIAGNOSTIC_FIELDS)
    if not isinstance(result, dict) or set(result) != expected:
        raise ValueError("invalid relay result fields")
    try:
        return DeliveryResult(source=ResultSource.RELAY, status=DeliveryStatus(result["status"]),
                              reason_code=result["reason_code"], provider_message_id=result["provider_message_id"],
                              retry_after=result["retry_after"],
                              **({name: result[name] for name in DIAGNOSTIC_FIELDS}
                                 if schema_version in (2, 3) else {}))
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid relay result") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.relay_protocol не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
