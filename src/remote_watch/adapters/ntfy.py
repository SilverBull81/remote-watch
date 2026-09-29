# Исходящие уведомления через JSON publish API сервера ntfy.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260929-185913
#
# Классы:
# -> NtfyConfig: Настройки получателя ntfy.
#    Интерфейс:
#    -> destination(): Создание получателя с общей политикой таймаутов.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек до начала сетевой работы.
#
# -> NtfyChannel: Публикация уведомления в ntfy.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Подготовка клиента и данных авторизации.
#    -> send(): Одна попытка отправки и проверка ответа.
#    -> close(): Закрытие клиента и освобождение ресурсов.
#
# Функции:
# -> _encode_json(): Компактный JSON с кириллицей в UTF-8.
# -> _publish_payload(): Ограничение текста и полного запроса перед отправкой.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from functools import partial

from .._validation import require_int, require_text, text_tuple
from ..config import Destination, RetryPolicy
from ..delivery import Delivery, DeliveryResult, DeliveryStatus
from ._common import http_failure, read_token, render, retry_after, truncate, validate_endpoint, validate_env
from ._http import HttpSender

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки получателя ntfy
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class NtfyConfig:
    """Configure a fixed ntfy topic with explicit authenticated or anonymous access."""

    topic: str = field(repr=False)              # Тема на сервере ntfy; не тема маршрутизации.
    token_env: str | None                       # Переменная с Bearer-токеном; None — без авторизации.
    endpoint: str = "https://ntfy.sh"           # Корень выбранного сервера ntfy.
    title: str = "Remote Watch"                 # Заголовок уведомления на телефоне.
    priority: int = 3                           # Приоритет ntfy от 1 до 5.
    tags: tuple[str, ...] = ()                  # Метки отображения ntfy, отдельно от routing tags.
    allow_http: bool = False                    # Явное разрешение HTTP для своего сервера.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Создание получателя с общей политикой таймаутов
    #--------------------------------------------------------------------------------------------------------------
    def destination(
        self,
        destination_id: str,
        *,
        retry: RetryPolicy | None = None,
        outstanding_capacity: int = 256,
        ) -> Destination:

        """Create a destination whose channel and runtime share one timeout policy.

        :param destination_id: Logical routing identifier.
        :type destination_id: str

        :param retry: Shared runtime and channel policy.
        :type retry: RetryPolicy | None

        :param outstanding_capacity: Maximum queued, active and retrying deliveries.
        :type outstanding_capacity: int

        :return: Lazy destination configuration.
        :rtype: Destination
        """

        # destination_id - логическое имя получателя для маршрутизации.
        # retry - общие ограничения runtime и канала.
        # outstanding_capacity - предел незавершённых доставок.

        policy = retry if retry is not None else RetryPolicy()
        return Destination(
            destination_id=destination_id, provider="ntfy", retry=policy,
            outstanding_capacity=outstanding_capacity,
            channel_factory=partial(NtfyChannel, self, retry=policy),
        )
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек до начала сетевой работы
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate a fixed topic and bounded display settings without side effects."""

        validate_endpoint(self.endpoint, self.allow_http)
        validate_env(self.token_env)

        # Тема фиксирована для получателя. Значение из LogRecord не может перенаправить публикацию.
        if not isinstance(self.topic, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.topic) is None:
            raise ValueError("topic must contain 1..64 ASCII letters, digits, underscores or hyphens")

        require_text(self.title, "title", 256)
        require_int(self.priority, "priority")
        if self.priority > 5:
            raise ValueError("priority must be between one and five")

        # Копия набора меток защищает конфигурацию от последующего изменения исходного списка.
        object.__setattr__(self, "tags", text_tuple(self.tags, "tags"))
        if len(self.tags) > 16:
            raise ValueError("at most sixteen tags are supported")

        # Даже метаданные с экранированием должны оставлять место для отметки об усечении.
        # Проверяем это при настройке, чтобы неверный набор меток не ломал каждую отправку.
        _publish_payload(self, "\n[сокращено]")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Публикация уведомления в ntfy
#------------------------------------------------------------------------------------------------------------------
class NtfyChannel:
    """Publish one bounded JSON message per call without hidden retries."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: NtfyConfig,
        *,
        retry: RetryPolicy | None = None,
        ) -> None:

        """Keep settings locally until the channel is opened by the runtime.

        :param config: Validated provider configuration.
        :type config: NtfyConfig

        :param retry: Shared runtime and channel policy.
        :type retry: RetryPolicy | None
        """

        # config - проверенные настройки сервиса.
        # retry - общие ограничения runtime и канала.

        if not isinstance(config, NtfyConfig):
            raise TypeError("config must be NtfyConfig")

        self._config = config
        self._http = HttpSender(retry if retry is not None else RetryPolicy(), json_encoder=_encode_json)
        self._token: str | None = None
        self._opened = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка клиента и данных авторизации
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Resolve optional authentication and open an owned client in the worker loop."""

        if self._opened:
            await self._http.open()
            return

        token = read_token(self._config.token_env)
        await self._http.open()
        self._token = token
        self._opened = True
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправки и проверка ответа
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Publish a fixed-topic JSON message and validate the acknowledgement.

        :param delivery: Immutable delivery attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        if not self._opened:
            raise RuntimeError("channel is not open")
        if not isinstance(delivery, Delivery):
            raise TypeError("delivery must be Delivery")

        # Проверяем не только текст, но и байты JSON, которые действительно отправит HTTP-клиент.
        payload = _publish_payload(self._config, render(delivery))
        sizes = {"message_bytes": len(str(payload["message"]).encode("utf-8")),
                 "request_bytes": len(_encode_json(payload).encode("utf-8"))}
        response = await self._http.post(self._config.endpoint.rstrip('/') + '/', payload, self._token)
        if isinstance(response, DeliveryResult):
            return replace(response, **sizes)
        status, headers, body = response
        if not 200 <= status <= 299:
            # Копируем лишь ограниченный числовой code. error/link и прочие поля
            # ответа могут содержать приватные данные и не выходят из адаптера.
            code = body.get("code") if isinstance(body, dict) else None
            code = code if type(code) is int and 0 <= code <= 999999 else None
            return replace(http_failure(status, retry_after(headers["retry-after"])),
                           provider_code=code, http_status=status, **sizes)

        if isinstance(body, dict) and body.get("event") == "message" and body.get("topic") == self._config.topic:
            message_id = body.get("id")
            if isinstance(message_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", message_id):
                return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, provider_message_id=message_id,
                                      http_status=status, **sizes)
        return DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="invalid_response",
                              http_status=status, **sizes)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие клиента и освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close the owned client and clear authentication even if cleanup is cancelled."""

        try:
            await self._http.close()
        finally:
            self._token = None
            self._opened = False
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Компактный JSON с кириллицей в UTF-8
#------------------------------------------------------------------------------------------------------------------
def _encode_json(payload: object) -> str:

    """Serialize JSON without expanding Unicode characters into ASCII escapes.

    :param payload: JSON-compatible request data.
    :type payload: object

    :return: The exact JSON text used by the HTTP client.
    :rtype: str
    """

    # payload - данные запроса; содержимое не выводится в диагностику.

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ограничение текста и полного запроса перед отправкой
#------------------------------------------------------------------------------------------------------------------
def _publish_payload(
    config: NtfyConfig,
    message: str,
    ) -> dict[str, object]:

    """Bound message bytes and serialized JSON bytes independently.

    :param config: Validated destination metadata.
    :type config: NtfyConfig

    :param message: Rendered notification text.
    :type message: str

    :return: One publish request within both default ntfy limits.
    :rtype: dict[str, object]
    """

    # config - постоянные настройки получателя.
    # message - текст со сведениями об источнике и идентификаторами доставки.

    # ntfy ограничивает текст 4096 байтами, а JSON publish — удвоенным размером текста.
    # ensure_ascii=False устраняет разрастание кириллицы, но кавычки и управляющие символы
    # всё равно экранируются; большой набор меток тоже занимает часть доступного объёма.
    text = truncate(message, 4096)
    payload: dict[str, object] = {
        "topic": config.topic, "message": text, "title": config.title,
        "priority": config.priority, "tags": list(config.tags),
    }
    if len(_encode_json(payload).encode("utf-8")) <= 8192:
        return payload

    marker = "\n[сокращено]"
    payload["message"] = marker
    if len(_encode_json(payload).encode("utf-8")) > 8192:
        raise ValueError("ntfy display settings exceed the JSON request limit")

    # Подбираем самый длинный допустимый префикс за ограниченное число шагов.
    # Режем по символам Python: emoji и кириллица сохраняются целиком. Метаданные не меняем.
    lower, upper = 0, len(text)
    while lower < upper:
        middle = (lower + upper + 1) // 2
        candidate = text[:middle] + marker
        payload["message"] = candidate
        if len(candidate.encode("utf-8")) <= 4096 and len(_encode_json(payload).encode("utf-8")) <= 8192:
            lower = middle
        else:
            upper = middle - 1

    payload["message"] = text[:lower] + marker
    return payload
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.adapters.ntfy не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
