# Общие проверки настроек, подготовка текста и классификация HTTP-ответов.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260929-185913
#
# Функции:
# -> validate_endpoint(): Проверка адреса сервиса без раскрытия его содержимого.
# -> validate_env(): Проверка имени переменной с токеном.
# -> read_token(): Чтение токена при открытии канала.
# -> truncate(): Сокращение текста с сохранением целых символов.
# -> render(): Подготовка текста со сведениями об отправителе.
# -> retry_after(): Чтение минимальной задержки из ответа сервиса.
# -> http_failure(): Классификация HTTP-ошибки без текста ответа.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .._validation import require_text
from ..delivery import Delivery, DeliveryResult, DeliveryStatus

#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка адреса сервиса без раскрытия его содержимого
#------------------------------------------------------------------------------------------------------------------
def validate_endpoint(
    endpoint: str,
    allow_http: bool,
    ) -> None:

    """Reject credentials, redirects encoded in paths and ambiguous base URLs.

    :param endpoint: Configured provider base URL.
    :type endpoint: str

    :param allow_http: Explicit permission for unencrypted HTTP.
    :type allow_http: bool
    """

    # endpoint - адрес сервера из настроек.
    # allow_http - явное разрешение соединения без TLS.

    require_text(endpoint, "endpoint", 2048)
    if type(allow_http) is not bool:
        raise TypeError("allow_http must be bool")

    # URL задаётся только настройкой; событие не может поменять адрес сервиса.
    try:
        url = urlsplit(endpoint)
        valid = (
            url.scheme in (("https", "http") if allow_http else ("https",))
            and url.hostname and url.username is None and url.password is None
            and not url.query and not url.fragment and url.port != 0
            and re.fullmatch(r"[A-Za-z0-9/_-]*", url.path) is not None
            and not any(char.isspace() or ord(char) < 32 for char in endpoint)
            and "\\" not in endpoint
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("endpoint must be an absolute safe HTTPS base URL") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка имени переменной с токеном
#------------------------------------------------------------------------------------------------------------------
def validate_env(name: str | None) -> None:

    """Validate an environment reference without accessing the secret.

    :param name: Environment variable name.
    :type name: str | None
    """

    # name - имя переменной окружения.

    if name is not None and (not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None):
        raise ValueError("token_env must name an environment variable")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение токена при открытии канала
#------------------------------------------------------------------------------------------------------------------
def read_token(
    name: str | None,
    telegram: bool = False,
    ) -> str | None:

    """Resolve a credential only while opening a channel.

    :param name: Environment variable name.
    :type name: str | None

    :param telegram: Whether to validate Telegram bot token syntax.
    :type telegram: bool

    :return: Resolved token or None.
    :rtype: str | None
    """

    # name - имя переменной окружения.
    # telegram - выбор формата токена Telegram.

    if name is None:
        return None

    value = os.environ.get(name, "")
    pattern = r"[0-9]+:[A-Za-z0-9_-]+" if telegram else r"[A-Za-z0-9_-]+"
    if len(value) > 512 or re.fullmatch(pattern, value) is None:
        # Не включаем имя переменной, её значение или исключение окружения в диагностику.
        raise ValueError("provider token is missing or invalid")
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Сокращение текста с сохранением целых символов
#------------------------------------------------------------------------------------------------------------------
def truncate(
    text: str,
    limit: int,
    encoding: str = 'utf-8',
    ) -> str:

    """Shorten text at a complete Unicode boundary and append a visible marker.

    :param text: Text to shorten.
    :type text: str

    :param limit: Maximum encoded byte length.
    :type limit: int

    :param encoding: Encoding used to count bytes.
    :type encoding: str

    :return: Bounded text.
    :rtype: str
    """

    # text - исходный текст.
    # limit - предел длины после кодирования, байт.
    # encoding - кодировка для подсчёта размера.

    data = text.encode(encoding)
    if len(data) <= limit:
        return text
    marker = "\n[сокращено]"
    return data[:limit - len(marker.encode(encoding))].decode(encoding, errors="ignore") + marker
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка текста со сведениями об отправителе
#------------------------------------------------------------------------------------------------------------------
def render(delivery: Delivery) -> str:

    """Render explicit source identity and event identifiers as plain text.

    :param delivery: Immutable delivery attempt.
    :type delivery: Delivery

    :return: Plain text with source context.
    :rtype: str
    """

    # delivery - подготовленные данные одной попытки.

    event = delivery.notification
    identity = event.identity
    # Поля принадлежности остаются отдельными и не выводятся из текста сообщения или имени чата.
    source = (
        f"service={identity.service} environment={identity.environment} region={identity.region}\n"
        f"host={identity.host} instance_id={identity.instance_id}\n"
        f"[{event.level_name}] {event.logger_name} {event.created_at.isoformat()}\n"
        f"event_id={event.event_id} session_id={event.session_id}\n"
        f"delivery_id={delivery.delivery_id}\n\n{event.message}"
    )
    if event.exception:
        source += "\n\n" + event.exception
    return source
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение минимальной задержки из ответа сервиса
#------------------------------------------------------------------------------------------------------------------
def retry_after(
    value: object,
    now: datetime | None = None,
    ) -> float | None:

    """Parse finite Retry-After seconds or an HTTP date without exposing response text.

    :param value: Untrusted delay value.
    :type value: object

    :param now: UTC reference time for HTTP dates.
    :type now: datetime | None

    :return: Valid delay or None.
    :rtype: float | None
    """

    # value - значение задержки из ответа.
    # now - время UTC для вычисления относительной задержки.

    # Некорректный заголовок не превращается в исключение с содержимым ответа.
    if type(value) in (int, float):
        try:
            result = float(value)
        except OverflowError:
            return None
    elif isinstance(value, str) and len(value) <= 128:
        try:
            result = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    return None
                result = max(0.0, (date - (now or datetime.now(timezone.utc))).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return None
    else:
        return None
    return result if math.isfinite(result) and result >= 0 else None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Классификация HTTP-ошибки без текста ответа
#------------------------------------------------------------------------------------------------------------------
def http_failure(
    status: int,
    delay: float | None,
    ) -> DeliveryResult:

    """Classify an HTTP rejection using fixed reason codes only.

    :param status: HTTP response status.
    :type status: int

    :param delay: Validated minimum retry delay.
    :type delay: float | None

    :return: Sanitized delivery result.
    :rtype: DeliveryResult
    """

    # status - код HTTP-ответа.
    # delay - проверенная минимальная задержка повтора.

    if status == 429:
        return DeliveryResult(status=DeliveryStatus.RATE_LIMITED, reason_code="rate_limited",
                              retry_after=delay, http_status=status)
    if status == 408 or 500 <= status <= 599:
        return DeliveryResult(
            status=DeliveryStatus.TRANSIENT_FAILURE, reason_code="http_temporary", retry_after=delay,
            http_status=status,
        )
    if 300 <= status <= 499:
        return DeliveryResult(status=DeliveryStatus.PERMANENT_FAILURE, reason_code="http_rejected",
                              http_status=status)
    return DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="invalid_response")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.adapters._common не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
