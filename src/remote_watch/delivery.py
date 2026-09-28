# Контракты одной попытки доставки и результата провайдера.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-121352
#
# Классы:
#
# -> DeliveryStatus: Классификация результата попытки.
#
# -> ResultSource: Источник ответа.
#
# -> Delivery: Неизменяемое задание одной попытки.
#    -> __post_init__(): Проверка идентификаторов и номера попытки.
#
# -> DeliveryResult: Безопасный структурированный результат.
#    -> __post_init__(): Проверка совместимости полей результата.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ._validation import require_int, require_number, require_text
from .events import Notification

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Классификация результата одной попытки
#------------------------------------------------------------------------------------------------------------------
class DeliveryStatus(str, Enum):
    """Distinguish provider acceptance, retryable failures and uncertain outcomes."""

    PROVIDER_ACCEPTED = "provider_accepted"
    TRANSIENT_FAILURE = "transient_failure"
    RATE_LIMITED = "rate_limited"
    PERMANENT_FAILURE = "permanent_failure"
    UNKNOWN = "unknown"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Источник результата
#------------------------------------------------------------------------------------------------------------------
class ResultSource(str, Enum):
    """Identify whether the provider or relay originated the result."""

    PROVIDER = "provider"
    RELAY = "relay"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Одна попытка доставки
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Delivery:
    """Bind a notification to a destination and stable delivery identifier.

    notification is an immutable snapshot. destination_id is a local logical name.
    delivery_id remains unchanged across retries. attempt is one-based.
    This value describes an attempt; it does not schedule or execute it.
    """

    notification: Notification  # Подготовленное уведомление.
    destination_id: str  # Имя настроенного получателя.
    delivery_id: str  # Общий идентификатор всех повторов отправки.
    attempt: int = 1  # Номер попытки, начиная с единицы.

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка задания
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate the snapshot, identifiers and one-based attempt number."""

        # Задание ссылается на уже проверенное уведомление; повторы сохраняют тот же delivery_id.
        if not isinstance(self.notification, Notification):
            raise TypeError("notification must be Notification")

        require_text(self.destination_id, "destination_id")
        require_text(self.delivery_id, "delivery_id")
        require_int(self.attempt, "attempt")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Результат одной попытки
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class DeliveryResult:
    """Describe one attempt without leaking provider exceptions or response bodies.

    status classifies the attempt; source identifies the reporting boundary.
    reason_code is an optional safe machine code, not arbitrary provider text.
    provider_message_id is available only for accepted messages. retry_after is an
    optional finite delay in seconds for transient/rate-limited responses.
    Adapters must redact sensitive information before constructing this result.
    """

    status: DeliveryStatus  # Результат попытки отправки.
    source: ResultSource = ResultSource.PROVIDER  # Кто ответил: сервис доставки или шлюз.
    reason_code: str | None = None  # Краткий код причины без секретных данных.
    provider_message_id: str | None = None  # Идентификатор принятого сервисом сообщения.
    retry_after: float | None = None  # Задержка до следующей попытки, секунды.

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка согласованности результата
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject contradictory statuses, invalid codes and unbounded retry delays."""

        # Результат описывает ответ сервиса или шлюза, а не показ уведомления на телефоне.
        if not isinstance(self.status, DeliveryStatus):
            raise TypeError("status must be DeliveryStatus")

        if not isinstance(self.source, ResultSource):
            raise TypeError("source must be ResultSource")

        # Допускается короткий машинный код; произвольный текст ответа может раскрыть ключи доступа.
        if self.reason_code is not None:
            require_text(self.reason_code, "reason_code", 64)

            if re.fullmatch(r"[a-z][a-z0-9_.-]*", self.reason_code) is None:
                raise ValueError("reason_code must be a safe machine code")

        # Идентификатор сообщения подтверждает принятие сервисом и несовместим с ошибкой отправки.
        if self.provider_message_id is not None:
            require_text(self.provider_message_id, "provider_message_id")

            if self.status is not DeliveryStatus.PROVIDER_ACCEPTED:
                raise ValueError("provider_message_id requires provider_accepted")

        # Указание задержки допустимо только для известной временной ошибки или ограничения частоты.
        if self.retry_after is not None:
            require_number(self.retry_after, "retry_after", allow_zero=True)

            if self.status not in (DeliveryStatus.RATE_LIMITED, DeliveryStatus.TRANSIENT_FAILURE):
                raise ValueError("retry_after requires a retryable known failure")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.delivery не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
