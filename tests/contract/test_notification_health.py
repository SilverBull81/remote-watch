# Состояние доставки, восстановление и независимость назначений без сети.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-095557
#
# Функции:
# -> wait_for(): Ограниченное ожидание результата фоновой отправки.
#
# Тесты:
# -> test_health_recovery(): Отказ, последующий успех и сохранение истории.
# -> test_retry_health(): Восстановление внутри повтора одной доставки.
# -> test_health_unobserved(): Чтение до попыток, без сети и с закрытым runtime.
# -> test_loss_is_not_connection_health(): Переполнение очереди остаётся отдельным счётчиком.
# -> test_observation_times(): Монотонные отметки и счётчик последовательных отказов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import FrozenInstanceError, asdict, replace
from unittest.mock import AsyncMock

import pytest
from test_runtime import RecordingChannel, make_runtime

from remote_watch import (
    DeliveryResult,
    DeliveryStatus,
    DestinationHealth,
    Identity,
    NotificationRuntime,
    ResultSource,
    RetryPolicy,
    WatcherConfig,
)
from remote_watch.notifications.health import observe_result


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ограниченное ожидание результата фоновой отправки
#------------------------------------------------------------------------------------------------------------------
def wait_for(predicate: Callable[[], bool]) -> None:

    """Wait for a bounded background observation without changing runtime state.

    :param predicate: Side-effect-free check on snapshots.
    :type predicate: Callable[[], bool]
    """

    # predicate — ожидание ограничено; ошибка не оставляет тест в бесконечном цикле.
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("notification observation did not arrive")
        time.sleep(0.002)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ, последующий успех и сохранение истории
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["transient_failure", "rate_limited", "permanent_failure", "unknown",
                                 "exception", "cancel", "invalid", "timeout", "tls"])
def test_health_recovery(
    identity: Identity,
    mode: str,
) -> None:

    """Recover one destination after a later success without resetting counters or another destination.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param mode: Safe adapter result or contained local adapter failure.
    :type mode: str
    """

    # identity/mode — настоящий worker, подставные адаптеры без HTTP и секретов.
    failed, working = RecordingChannel(), RecordingChannel()
    variants = {"exception": RuntimeError("private-token-payload"), "cancel": asyncio.CancelledError(),
                "invalid": object(), "timeout": asyncio.TimeoutError()}
    initial = (variants[mode] if mode in variants else
               DeliveryResult(status=DeliveryStatus.PERMANENT_FAILURE,
                              error_kind="tls_certificate", verify_code=10)
               if mode == "tls" else DeliveryResult(status=DeliveryStatus(mode), http_status=503))
    failed.send = AsyncMock(side_effect=[initial,
        DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, provider_message_id="private-message-id")])
    runtime = make_runtime(identity, (failed, working))
    logger = logging.Logger("notification.health")
    logger.addHandler(runtime.handler)
    before = runtime.health()
    assert before.runtime_state == "created" and before.delivery_state == "unobserved"
    runtime.start()
    try:
        logger.error("first event")
        wait_for(lambda: runtime.stats("0").exhausted + runtime.stats("0").permanent_failure == 1
                 and runtime.stats("1").accepted == 1)
        snapshot = runtime.health()
        assert snapshot.runtime_state == "running" and snapshot.delivery_state == "degraded"
        bad, good = snapshot.destinations
        assert bad.current_error is not None and bad.last_success_at is None
        assert bad.consecutive_failures == 1 and good.delivery_state == "healthy"
        assert runtime.health("1").delivery_state == "healthy"
        if mode in variants:
            expected = {"exception": "adapter_error", "invalid": "adapter_error",
                        "cancel": "adapter_cancelled", "timeout": "attempt_timeout"}
            assert bad.current_error.reason_code == expected[mode]
        if mode == "tls":
            assert bad.current_error.verify_code == 10 and bad.current_error.tls_reason == "certificate_expired"
        assert "private-token" not in repr(snapshot)

        logger.error("second event")
        wait_for(lambda: runtime.stats().accepted == 3)
        recovered = runtime.health("0").destinations[0]
        assert runtime.health().delivery_state == "healthy"
        assert recovered.current_error is None and recovered.last_error == bad.current_error
        assert recovered.last_success_at >= recovered.last_failure_at
        assert recovered.last_failure_at == bad.last_failure_at
        assert recovered.recoveries == 1 and recovered.consecutive_failures == 0
        assert bad.delivery_state == "degraded" and before.delivery_state == "unobserved"
        assert runtime.stats("0").unknown_attempts + runtime.stats("0").failed_attempts == 1
        assert "private-message-id" not in repr(asdict(runtime.health()))
        with pytest.raises(FrozenInstanceError):
            recovered.delivery_state = "forged"
    finally:
        runtime.stop()
    assert runtime.health().runtime_state == "closed"
    assert runtime.health("0").destinations[0].recoveries == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Восстановление внутри повтора одной доставки
#------------------------------------------------------------------------------------------------------------------
def test_retry_health(identity: Identity) -> None:

    """Make a transient first attempt recover while retaining failed_attempts for the same event.

    :param identity: Synthetic application identity.
    :type identity: Identity
    """

    # identity — один event, два вызова send; успешный повтор не стирает историю.
    channel = RecordingChannel()
    channel.send = AsyncMock(side_effect=[DeliveryResult(status=DeliveryStatus.TRANSIENT_FAILURE),
                                         DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)])
    config = make_runtime(identity, (channel,)).config
    destination = replace(config.destinations[0], retry=RetryPolicy(max_attempts=2, backoff_base=0.01))
    runtime = NotificationRuntime(replace(config, destinations=(destination,)), random_source=lambda: 0)
    logger = logging.Logger("notification.retry.health")
    logger.addHandler(runtime.handler)
    runtime.start()
    try:
        logger.error("single event")
        wait_for(lambda: runtime.stats().accepted == 1)
        assert runtime.health().delivery_state == "healthy"
        assert runtime.health().destinations[0].recoveries == 1
        assert runtime.stats().failed_attempts == 1 and runtime.stats().retries == 1
        assert channel.send.call_count == 2
    finally:
        runtime.stop()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Чтение до попыток, без сети и с закрытым runtime
#------------------------------------------------------------------------------------------------------------------
def test_health_unobserved(identity: Identity) -> None:

    """Keep idle/unused destinations unobserved and distinguish lifecycle without probing.

    :param identity: Synthetic application identity.
    :type identity: Identity
    """

    # identity — чтение снимка не должно даже открыть транспорт.
    channel = RecordingChannel()
    runtime = make_runtime(identity, (channel,))
    for _ in range(5):
        assert runtime.health().delivery_state == "unobserved"
    assert channel.calls == []
    with pytest.raises(KeyError):
        runtime.health("missing")
    runtime.start()
    assert runtime.health().runtime_state == "running" and runtime.health().delivery_state == "unobserved"
    runtime.stop()
    assert runtime.health().runtime_state == "closed" and not channel.deliveries
    empty = NotificationRuntime(WatcherConfig(identity=identity))
    assert empty.health().destinations == () and empty.health().delivery_state == "unobserved"

    broken = RecordingChannel()
    broken.open_error = True
    failed = make_runtime(identity, (broken,))
    with pytest.raises(RuntimeError):
        failed.start()
    assert failed.health().runtime_state == "failed"
    assert failed.health().delivery_state == "unobserved"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Переполнение очереди остаётся отдельным счётчиком
#------------------------------------------------------------------------------------------------------------------
def test_loss_is_not_connection_health(identity: Identity) -> None:

    """Do not diagnose connectivity from a local capacity rejection without an attempt.

    :param identity: Synthetic application identity.
    :type identity: Identity
    """

    # identity — тестовый адаптер удерживает слот; вторая доставка отклоняется локально.
    channel = RecordingChannel()
    channel.block = True
    runtime = make_runtime(identity, (channel,), capacity=1)
    logger = logging.Logger("notification.overflow.health")
    logger.addHandler(runtime.handler)
    runtime.start()
    try:
        logger.error("blocked")
        assert channel.entered.wait(2)
        logger.error("overflow")
        wait_for(lambda: runtime.stats().destination_overflow == 1)
        assert runtime.health().delivery_state == "unobserved"
        channel.calls[0][2].call_soon_threadsafe(channel.gate.set)
        wait_for(lambda: runtime.stats().accepted == 1)
        assert runtime.health().delivery_state == "healthy"
        assert runtime.stats().destination_overflow == 1
    finally:
        runtime.stop()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Монотонные отметки и счётчик последовательных отказов
#------------------------------------------------------------------------------------------------------------------
def test_observation_times() -> None:

    """Retain history with deterministic observations and count only actual recoveries."""

    initial = DestinationHealth("one")
    result = DeliveryResult(status=DeliveryStatus.RATE_LIMITED, source=ResultSource.GATEWAY, http_status=429)
    first = observe_result(initial, result, 10)
    second = observe_result(first, result, 20)
    assert second.consecutive_failures == 2 and second.last_failure_at == 20
    assert second.current_error.http_status == 429 and second.current_error.source == "gateway"
    success = DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)
    recovered = observe_result(second, success, 30)
    healthy = observe_result(recovered, success, 40)
    assert healthy.last_success_at == 40 and healthy.last_attempt_at == 40
    assert healthy.last_failure_at == 20 and healthy.recoveries == 1
    assert healthy.current_error is None and healthy.last_error == second.current_error
    next_failure = observe_result(healthy, result, 50)
    assert next_failure.delivery_state == "degraded" and next_failure.recoveries == 1
    assert next_failure.last_success_at == 40 and next_failure.last_failure_at == 50
    assert next_failure.consecutive_failures == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение тестового файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Тесты наблюдения уведомлений запускаются через pytest.")
#------------------------------------------------------------------------------------------------------------------
