# Исходящие текстовые уведомления через Telegram Bot API.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Классы:
# -> TelegramConfig: Настройки получателя Telegram.
#    Интерфейс:
#    -> destination(): Создание получателя с общей политикой таймаутов.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек до начала сетевой работы.
#
# -> TelegramChannel: Отправка текстового сообщения в Telegram.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Подготовка клиента и данных авторизации.
#    -> send(): Одна попытка отправки и проверка ответа.
#    -> close(): Закрытие клиента и освобождение ресурсов.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import partial

from remote_watch._credentials import resolve_token, validate_credentials
from remote_watch._validation import require_int
from remote_watch.adapters._common import (
    http_failure,
    render,
    retry_after,
    truncate,
    validate_endpoint,
)
from remote_watch.adapters._http import HttpSender
from remote_watch.config import Destination, RetryPolicy
from remote_watch.notifications.delivery import Delivery, DeliveryResult, DeliveryStatus

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки получателя Telegram
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class TelegramConfig:
    """Declare a Telegram destination using a literal token or environment reference."""

    token_env: str | None = field(default=None, repr=False)     # Имя переменной с токеном; альтернатива token.
    token: str | None = field(default=None, repr=False)         # Токен бота непосредственно в настройках.
    chat_id: int | str = field(repr=False)      # Числовой ID чата либо @имя канала.
    endpoint: str = "https://api.telegram.org"  # Корень официального или собственного Bot API.
    message_thread_id: int | None = None        # ID темы в группе-форуме; None — обычная отправка.
    disable_notification: bool = False          # Отправка без звука.
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

        """Bind the same retry policy to the runtime and its lazy channel factory.

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
            destination_id=destination_id, provider="telegram", retry=policy,
            outstanding_capacity=outstanding_capacity,
            channel_factory=partial(TelegramChannel, self, retry=policy),
        )
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек до начала сетевой работы
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate static settings without reading credentials or importing aiohttp."""

        validate_credentials(self.token, self.token_env, "telegram")

        validate_endpoint(self.endpoint, self.allow_http)

        # Bot API принимает числовой адрес чата либо публичное имя канала.
        # Значения bool исключаются отдельно: в Python они являются разновидностью int.
        valid_chat = type(self.chat_id) is int and self.chat_id != 0
        if isinstance(self.chat_id, str):
            valid_chat = re.fullmatch(r"(?:-?[1-9][0-9]*|@[A-Za-z0-9_]+)", self.chat_id) is not None
        if not valid_chat:
            raise ValueError("chat_id must be a nonzero integer or channel username")

        if self.message_thread_id is not None:
            require_int(self.message_thread_id, "message_thread_id")

        if type(self.disable_notification) is not bool:
            raise TypeError("disable_notification must be bool")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Отправка текстового сообщения в Telegram
#------------------------------------------------------------------------------------------------------------------
class TelegramChannel:
    """Send plain-text notifications once through a privately owned HTTP client."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: TelegramConfig,
        *,
        retry: RetryPolicy | None = None,
        ) -> None:

        """Retain validated settings without resolving tokens or starting network work.

        :param config: Validated provider configuration.
        :type config: TelegramConfig

        :param retry: Shared runtime and channel policy.
        :type retry: RetryPolicy | None
        """

        # config - проверенные настройки сервиса.
        # retry - общие ограничения runtime и канала.

        if not isinstance(config, TelegramConfig):
            raise TypeError("config must be TelegramConfig")

        self._config = config
        self._http = HttpSender(retry if retry is not None else RetryPolicy())
        self._url: str | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка клиента и данных авторизации
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Resolve the bot credential and create a client in the worker loop."""

        if self._url is not None:
            await self._http.open()
            return

        token = resolve_token(self._config.token, self._config.token_env, "telegram")
        await self._http.open()
        self._url = f"{self._config.endpoint.rstrip('/')}/bot{token}/sendMessage"
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправки и проверка ответа
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Perform one sendMessage call and classify its sanitized result.

        :param delivery: Immutable delivery attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        if self._url is None:
            raise RuntimeError("channel is not open")
        if not isinstance(delivery, Delivery):
            raise TypeError("delivery must be Delivery")

        # Без parse_mode текст ошибки не становится HTML или Markdown. UTF-16 даёт консервативный
        # предел для emoji: не более 4096 кодовых единиц, без разрезания символа.
        payload: dict[str, object] = {
            "chat_id": self._config.chat_id,
            "text": truncate(render(delivery), 8192, "utf-16-le"),
            "disable_notification": self._config.disable_notification,
            "link_preview_options": {"is_disabled": True},
        }
        if self._config.message_thread_id is not None:
            payload["message_thread_id"] = self._config.message_thread_id
        response = await self._http.post(self._url, payload)
        if isinstance(response, DeliveryResult):
            return response

        status, headers, body = response
        delay = retry_after(headers["retry-after"])
        if isinstance(body, dict) and body.get("ok") is False:
            parameters = body.get("parameters")
            if isinstance(parameters, dict):
                provider_delay = retry_after(parameters.get("retry_after"))
                if provider_delay is not None:
                    delay = max(delay or 0.0, provider_delay)
            code = body.get("error_code")
            if type(code) is int and 400 <= code <= 599:
                return replace(http_failure(code, delay), http_status=status, provider_code=code)

        if not 200 <= status <= 299:
            return http_failure(status, delay)
        if isinstance(body, dict) and body.get("ok") is True:
            result = body.get("result")
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if type(message_id) is int and 0 < message_id < 2**63:
                return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, provider_message_id=str(message_id))
        return DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="invalid_response")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие клиента и освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Release network resources and discard the credential-bearing URL."""

        try:
            await self._http.close()
        finally:
            self._url = None
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.adapters.telegram не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
