# Наблюдаемое состояние доставки отдельно от накопительной статистики потерь.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Классы:
# -> NotificationFailure: Безопасная причина одной неудачной попытки
#
# -> DestinationHealth: Последние наблюдения отдельного назначения
#
# -> NotificationHealth: Согласованный снимок runtime и выбранных назначений
#
# Функции:
# -> observe_result(): Обновление наблюдения без потери истории и без сетевых проверок


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

from dataclasses import dataclass, replace

from remote_watch.notifications.delivery import DeliveryResult, DeliveryStatus

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Безопасная причина одной неудачной попытки
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NotificationFailure:
    """
    Retain bounded adapter diagnostics without payloads or provider message identifiers.

    :param status: Fixed delivery attempt outcome.
    :type status: str

    :param source: Authority that produced the delivery outcome.
    :type source: str

    :param reason_code: Safe bounded delivery failure code, or None.
    :type reason_code: str | None

    :param http_status: Observed HTTP status, or None when no valid status was received.
    :type http_status: int | None

    :param provider_code: Allowlisted numeric provider error code, or None.
    :type provider_code: int | None

    :param error_kind: Safe local transport error category, or None.
    :type error_kind: str | None

    :param verify_code: OpenSSL certificate verification code, or None.
    :type verify_code: int | None

    :param tls_reason: Allowlisted TLS failure reason without server addresses or exception text.
    :type tls_reason: str | None
    """

    status: str                     # Категория исхода: transient_failure/rate_limited/permanent_failure/unknown.
    source: str                     # Ответ провайдера либо gateway.
    reason_code: str | None         # Машинный код из проверенного DeliveryResult.
    http_status: int | None         # HTTP-статус; None при отсутствии ответа.
    provider_code: int | None       # Числовой код API, если он известен.
    error_kind: str | None          # Безопасная категория отказа TLS.
    verify_code: int | None         # Код проверки сертификата без данных цепочки.
    tls_reason: str | None          # Фиксированное пояснение к отказу TLS.
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Последние наблюдения отдельного назначения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DestinationHealth:
    """
    Describe last observed delivery health, not a live connectivity or phone receipt guarantee.

    :param destination_id: Unique destination name within the runtime configuration.
    :type destination_id: str

    :param delivery_state: Unobserved before an attempt, then healthy or degraded.
    :type delivery_state: str

    :param last_attempt_at: Monotonic time of the last attempt, or None before any attempt.
    :type last_attempt_at: float | None

    :param last_success_at: Monotonic time of the last provider acceptance, or None.
    :type last_success_at: float | None

    :param last_failure_at: Monotonic time of the last failure, or None.
    :type last_failure_at: float | None

    :param current_error: Current failure, or None after recovery.
    :type current_error: NotificationFailure | None

    :param last_error: Last historical error; successful recovery does not erase it.
    :type last_error: NotificationFailure | None

    :param consecutive_failures: Number of consecutive failed attempts for this destination.
    :type consecutive_failures: int

    :param recoveries: Number of observed transitions from failure to successful operation.
    :type recoveries: int
    """

    destination_id: str                        # Имя из конфигурации; число записей ограничено назначениями.
    delivery_state: str = "unobserved"         # unobserved до первой попытки, затем healthy/degraded.
    last_attempt_at: float | None = None       # Момент завершения последней попытки по time.monotonic().
    last_success_at: float | None = None       # Последнее подтверждение сервиса; не время показа на телефоне.
    last_failure_at: float | None = None       # Последний отказ; сохраняется после восстановления.
    current_error: NotificationFailure | None = None  # Последний отказ до следующего успешного принятия.
    last_error: NotificationFailure | None = None  # История последнего отказа, включая уже устранённый.
    consecutive_failures: int = 0              # Число неудачных попыток подряд для этого назначения.
    recoveries: int = 0                        # Переходы degraded → healthy, включая успешный повтор.
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Согласованный снимок runtime и выбранных назначений
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NotificationHealth:
    """
    Separate local lifecycle from passive observations of destination attempts.

    :param runtime_state: Created, running, stopping, closed or failed independently of delivery health.
    :type runtime_state: str

    :param delivery_state: Unobserved before an attempt, then healthy or degraded.
    :type delivery_state: str

    :param destinations: Notification destinations or their observed health snapshots.
    :type destinations: tuple[DestinationHealth, ...]
    """

    runtime_state: str                         # created/running/stopping/closed/failed независимо от доставки.
    delivery_state: str                        # degraded при любом отказе; иначе unobserved либо healthy.
    destinations: tuple[DestinationHealth, ...]  # Наблюдения всех либо одного выбранного назначения.
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Обновление наблюдения без потери истории и без сетевых проверок
#------------------------------------------------------------------------------------------------------------------
def observe_result(
    previous: DestinationHealth,
    result: DeliveryResult,
    observed_at: float,
    ) -> DestinationHealth:

    """
    Apply a completed attempt while retaining historical failure information.

    :param previous: Immutable previous observation for one configured destination.
    :type previous: DestinationHealth

    :param result: Validated adapter result, with no raw response or exception text.
    :type result: DeliveryResult

    :param observed_at: Local monotonic completion time; never compared across processes.
    :type observed_at: float

    :return: Next immutable observation of this destination only.
    :rtype: DestinationHealth
    """

    # previous/result/observed_at — вызываются runtime под его короткой блокировкой.
    # Успех снимает текущий отказ, но не стирает причину и момент прежнего инцидента.
    if result.status is DeliveryStatus.PROVIDER_ACCEPTED:
        return replace(previous, delivery_state="healthy", last_attempt_at=observed_at,
                       last_success_at=observed_at, current_error=None, consecutive_failures=0,
                       recoveries=previous.recoveries + int(previous.delivery_state == "degraded"))

    # ID сообщения, содержимое уведомления и произвольные исключения не сохраняются.
    # reason_code остаётся частью контракта адаптера: это проверенный машинный код.
    error = NotificationFailure(status=result.status.value, source=result.source.value,
                                reason_code=result.reason_code, http_status=result.http_status,
                                provider_code=result.provider_code, error_kind=result.error_kind,
                                verify_code=result.verify_code, tls_reason=result.tls_reason)
    return replace(previous, delivery_state="degraded", last_attempt_at=observed_at,
                   last_failure_at=observed_at, current_error=error, last_error=error,
                   consecutive_failures=previous.consecutive_failures + 1)
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.notifications.health не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
