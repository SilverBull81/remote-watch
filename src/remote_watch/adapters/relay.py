# Одна исходящая попытка доставки через HTTPS gateway без provider credentials.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Классы:
# -> RelayConfig: Настройки адреса gateway и разрешённого назначения.
#    Интерфейс:
#    -> destination(): Ленивое назначение с заданным способом доставки.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек и ограничений.
#    Служебные методы:
#    -> _check_policy(): Проверка резерва времени на сетевой обмен.
#
# -> RelayChannel: Доставка через gateway без provider credentials.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска работы.
#    Интерфейс:
#    -> open(): Открытие канала в принадлежащем ему цикле событий.
#    -> send(): Одна попытка отправки без собственного цикла повторов.
#    -> close(): Закрытие канала и освобождение временных данных.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial

from remote_watch._validation import require_number
from remote_watch.adapters._common import (
    http_failure,
    read_token,
    retry_after,
    validate_endpoint,
    validate_env,
)
from remote_watch.adapters._http import HttpSender
from remote_watch.config import DeliveryMode, Destination, RetryPolicy
from remote_watch.notifications.delivery import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    ResultSource,
)
from remote_watch.relay_protocol import (
    MAX_RESPONSE_BYTES,
    RelayRequest,
    _decode,
    decode_response,
    encode_json,
    validate_alias,
)


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки адреса gateway и разрешённого назначения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class RelayConfig:
    """Bind a destination to a fixed gateway endpoint and one authorized alias."""

    endpoint: str                       # Базовый HTTPS-адрес gateway, без токена и query.
    alias: str                          # Имя получателя в конфигурации gateway.
    token_env: str                      # Переменная окружения с отдельным сервисным токеном.
    server_timeout: float = 8.0         # Верхний срок обработки запроса gateway, секунды.
    network_margin: float = 1.0         # Резерв на обмен с gateway, секунды.
    allow_http: bool = False            # Явное разрешение HTTP только для локальной проверки.
    schema_version: int = 1             # Версия wire: 2 добавляет числовую диагностику провайдера.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ленивое назначение с заданным способом доставки
    #--------------------------------------------------------------------------------------------------------------
    def destination(
        self,
        destination_id: str,
        *,
        retry: RetryPolicy | None = None,
        outstanding_capacity: int = 256,
    ) -> Destination:

        """Create a relay-mode destination whose retries remain owned by the application runtime.

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
        self._check_policy(policy)
        return Destination(destination_id=destination_id, mode=DeliveryMode.RELAY, provider="relay",
                           channel_factory=partial(RelayChannel, self, retry=policy),
                           retry=policy, outstanding_capacity=outstanding_capacity)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек и ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate fixed routing and finite budgets without reading credentials."""

        validate_endpoint(self.endpoint, self.allow_http)
        validate_alias(self.alias)
        validate_env(self.token_env)
        if self.token_env is None:
            raise ValueError("relay requires token_env")
        require_number(self.server_timeout, "server_timeout")
        require_number(self.network_margin, "network_margin")
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError("unsupported relay schema version")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка резерва времени на сетевой обмен
    #--------------------------------------------------------------------------------------------------------------
    def _check_policy(
        self,
        policy: RetryPolicy,
    ) -> None:

        """Require room for a gateway result within the client's attempt deadline.

        :param policy: Timeout and retry policy.
        :type policy: RetryPolicy
        """

        # policy - политика времени ожидания и повторов.

        if not isinstance(policy, RetryPolicy):
            raise TypeError("retry must be RetryPolicy")
        if self.server_timeout + self.network_margin > policy.attempt_timeout:
            raise ValueError("relay server timeout and network margin exceed attempt_timeout")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Доставка через gateway без provider credentials
#------------------------------------------------------------------------------------------------------------------
class RelayChannel:
    """Send one correlated request; it does not import a concrete provider or retry internally."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: RelayConfig,
        *,
        retry: RetryPolicy | None = None,
    ) -> None:

        """Prepare lazy HTTP ownership without accessing the network or credentials.

        :param config: Validated settings.
        :type config: RelayConfig

        :param retry: Shared runtime and channel policy.
        :type retry: RetryPolicy | None
        """

        # config - проверенные настройки выбранного сценария.
        # retry - общие ограничения runtime и канала.

        if not isinstance(config, RelayConfig):
            raise TypeError("config must be RelayConfig")
        self._policy = retry if retry is not None else RetryPolicy()
        config._check_policy(self._policy)
        self._config = config
        self._http = HttpSender(self._policy, response_limit=MAX_RESPONSE_BYTES,
                                json_encoder=encode_json,
                                json_decoder=partial(_decode, limit=MAX_RESPONSE_BYTES))
        self._token: str | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие канала в принадлежащем ему цикле событий
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Resolve a service credential and open the client in the worker loop."""

        if self._token is not None:
            await self._http.open()
            return
        token = read_token(self._config.token_env)
        if token is None or len(token) < 32:
            raise ValueError("relay token must contain at least 32 URL-safe characters")
        await self._http.open()
        self._token = token
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправки без собственного цикла повторов
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
    ) -> DeliveryResult:

        """Perform one request with conservative expiry and correlated result validation.

        :param delivery: Immutable delivery attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        if self._token is None:
            raise RuntimeError("channel is not open")
        if not isinstance(delivery, Delivery):
            raise TypeError("delivery must be Delivery")

        # Runtime передаёт остаток монотонного срока попытки с учётом TTL и остановки.
        # UTC может только дополнительно сократить этот срок, но не продлить его.
        utc_remaining = (delivery.notification.expires_at - datetime.now(timezone.utc)).total_seconds()
        remaining = min(utc_remaining, self._policy.ttl, self._policy.attempt_timeout,
                        delivery.remaining_timeout if delivery.remaining_timeout is not None else float("inf"))
        timeout = min(self._config.server_timeout, remaining - self._config.network_margin)
        if timeout <= 0:
            return DeliveryResult(source=ResultSource.RELAY, status=DeliveryStatus.PERMANENT_FAILURE,
                                  reason_code="relay_budget_exhausted")
        try:
            request = RelayRequest(delivery=delivery, alias=self._config.alias,
                                   remaining_ttl=remaining, timeout=timeout,
                                   schema_version=self._config.schema_version)
        except (ValueError, TypeError, OverflowError):
            # Неверный для wire размер не является неизвестным исходом: сеть ещё не начата.
            return DeliveryResult(source=ResultSource.RELAY, status=DeliveryStatus.PERMANENT_FAILURE,
                                  reason_code="relay_payload_limits")
        response = await self._http.post(self._config.endpoint.rstrip("/") + "/v1/notifications",
                                         request.to_dict(), self._token)
        if isinstance(response, DeliveryResult):
            return replace(response, source=ResultSource.RELAY)
        status, headers, body = response
        if not 200 <= status <= 299:
            result = http_failure(status, retry_after(headers["retry-after"]))
            # Ошибку доступа к gateway не выдаём за отказ сервиса Telegram/ntfy.
            if status in (401, 403):
                result = replace(result, reason_code="relay_auth_denied")
            return replace(result, source=ResultSource.RELAY)
        try:
            return decode_response(body, delivery, schema_version=self._config.schema_version)
        except (ValueError, TypeError, OverflowError):
            return DeliveryResult(source=ResultSource.RELAY, status=DeliveryStatus.UNKNOWN,
                                  reason_code="invalid_relay_response")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие канала и освобождение временных данных
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close the client and discard the cached service token."""

        try:
            await self._http.close()
        finally:
            self._token = None
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.adapters.relay не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
