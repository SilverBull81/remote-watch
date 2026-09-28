# Проверки конфигурации, ленивых фабрик и результатов доставки.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-121352
#
# Функции:
#
# -> unused_factory(): Фабрика, которую конфигурация не должна вызывать.
#
# Тесты:
#
# -> test_configuration_is_lazy_and_detached(): Защитные копии без создания клиента.
#
# -> test_configuration_rejects_invalid_references(): Дубликаты и отсутствующие адресаты.
#
# -> test_finite_policy_bounds(): Неправильные временные бюджеты.
#
# -> test_runtime_and_destination_validation(): Неподдерживаемые режимы и типы.
#
# -> test_delivery_results(): Классификация одной попытки.
#
# -> test_invalid_delivery_result(): Противоречивые поля результата.
#
# -> test_delivery_retry_preserves_ids(): Явное изменение номера попытки.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from dataclasses import replace

import pytest

from remote_watch import (
    Delivery,
    DeliveryMode,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Identity,
    Notification,
    NotificationChannel,
    RetryPolicy,
    Route,
    RuntimeConfig,
    WatcherConfig,
)

#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Контроль отсутствия побочных действий при конфигурации
#------------------------------------------------------------------------------------------------------------------
def unused_factory() -> NotificationChannel:

    """Fail if validation tries to create a channel.

    :return: Never returns; invocation is a test failure.
    :rtype: NotificationChannel
    """

    raise AssertionError("configuration must not create provider clients")
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Конфигурация не создаёт каналы и не сохраняет изменяемые списки
#------------------------------------------------------------------------------------------------------------------
def test_configuration_is_lazy_and_detached(
    identity: Identity,
    ) -> None:

    """Keep provider factories lazy and configuration collections immutable.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - сведения о приложении.

    destinations = [Destination(destination_id="phone", channel_factory=unused_factory)]
    target_ids = ["phone"]
    routes = [Route(destination_ids=target_ids, required_tags=["ops"])]
    config = WatcherConfig(identity=identity, destinations=destinations, routes=routes)
    destinations.clear()
    routes.clear()
    target_ids.clear()
    assert config.destinations[0].destination_id == "phone"
    assert config.routes[0].destination_ids == ("phone",)
    assert config.routes[0].required_tags == ("ops",)
    assert config.routes[0].min_level == 40
    assert len(config.commands) == 0
    assert "unused_factory" not in repr(config)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Неоднозначная конфигурация не принимается
#------------------------------------------------------------------------------------------------------------------
def test_configuration_rejects_invalid_references(
    identity: Identity,
    ) -> None:

    """Reject duplicate IDs, missing targets and exceeded destination budgets.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - сведения о приложении.

    destination = Destination(destination_id="phone", channel_factory=unused_factory)

    with pytest.raises(ValueError, match="duplicate"):
        WatcherConfig(identity=identity, destinations=(destination, destination))

    with pytest.raises(ValueError, match="unknown"):
        WatcherConfig(identity=identity, routes=(Route(destination_ids=("absent",)),))

    with pytest.raises(ValueError, match="max_destinations"):
        WatcherConfig(identity=identity, runtime=RuntimeConfig(max_destinations=1),
                      destinations=(destination, replace(destination, destination_id="second")))

    with pytest.raises(ValueError):
        Route(destination_ids=())

    with pytest.raises(TypeError):
        WatcherConfig(identity=identity, commands={"invalid": object()})
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Временные бюджеты должны быть конечными и согласованными
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("changes", [
    {"max_attempts": True}, {"max_attempts": 0}, {"ttl": float("inf")},
    {"attempt_timeout": float("nan")}, {"connect_timeout": -1}, {"ttl": 10 ** 500},
    {"connect_timeout": 20}, {"backoff_base": 31}, {"backoff_cap": "30"},
])
def test_finite_policy_bounds(
    changes: dict[str, object],
    ) -> None:

    """Reject nonfinite, incorrectly typed and contradictory retry policies.

    :param changes: Invalid policy fields.
    :type changes: dict[str, object]
    """

    # changes - недопустимые настройки политики.

    with pytest.raises((TypeError, ValueError)):
        RetryPolicy(**changes)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Нельзя случайно включить relay или безразмерную очередь
#------------------------------------------------------------------------------------------------------------------
def test_runtime_and_destination_validation() -> None:

    """Reject unsupported modes and invalid queue/factory values eagerly."""

    with pytest.raises(ValueError, match="not implemented"):
        Destination(destination_id="phone", channel_factory=unused_factory, mode=DeliveryMode.RELAY)

    with pytest.raises(TypeError, match="mode"):
        Destination(destination_id="phone", channel_factory=unused_factory, mode="direct")

    with pytest.raises(TypeError, match="callback"):
        Destination(destination_id="phone", channel_factory=None)

    with pytest.raises(ValueError):
        RuntimeConfig(ingress_capacity=0)

    with pytest.raises(TypeError):
        RuntimeConfig(ingress_capacity=True)

    with pytest.raises(ValueError):
        RuntimeConfig(shutdown_timeout=float("inf"))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Результаты различают известный успех, ограничение и неизвестный исход
#------------------------------------------------------------------------------------------------------------------
def test_delivery_results() -> None:

    """Represent accepted, rate-limited and ambiguous attempts separately."""

    accepted = DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, provider_message_id="message-1")
    limited = DeliveryResult(status=DeliveryStatus.RATE_LIMITED, retry_after=0, reason_code="http_429")
    unknown = DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="response_timeout")
    assert accepted.status.value == "provider_accepted"
    assert limited.retry_after == 0
    assert unknown.provider_message_id is None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Несовместимые поля результата отклоняются
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("changes", [
    {"status": "provider_accepted"}, {"retry_after": 1}, {"reason_code": "body with token"},
    {"source": "unknown"}, {"status": DeliveryStatus.RATE_LIMITED, "retry_after": float("nan")},
    {"status": DeliveryStatus.RATE_LIMITED, "retry_after": True},
    {"status": DeliveryStatus.UNKNOWN, "provider_message_id": "message-1"},
])
def test_invalid_delivery_result(
    changes: dict[str, object],
    ) -> None:

    """Prevent impossible combinations and unsafe provider result data.

    :param changes: Invalid result fields.
    :type changes: dict[str, object]
    """

    # changes - недопустимые поля результата.

    with pytest.raises((TypeError, ValueError)):
        DeliveryResult(**({"status": DeliveryStatus.PROVIDER_ACCEPTED} | changes))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Повтор использует прежний delivery ID и прежний снимок
#------------------------------------------------------------------------------------------------------------------
def test_delivery_retry_preserves_ids(
    notification: Notification,
    ) -> None:

    """Model another attempt without changing event or delivery identity.

    :param notification: Valid immutable snapshot.
    :type notification: Notification
    """

    # notification - исходный снимок.

    first = Delivery(notification=notification, destination_id="phone", delivery_id="delivery-1")
    second = replace(first, attempt=2)
    assert second.delivery_id == first.delivery_id
    assert second.notification is first.notification

    with pytest.raises(ValueError):
        replace(first, attempt=0)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль tests/unit/test_config.py не предназначен для прямого запуска. Используйте pytest.',
    )
#------------------------------------------------------------------------------------------------------------------
