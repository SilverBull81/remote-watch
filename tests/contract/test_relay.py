# Контракт relay-клиента: строгий JSON, сроки, корреляция и настоящие HTTP-запросы на loopback.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Тесты:
# -> fresh_delivery(): Попытка со свежим сроком UTC.
# -> test_wire_roundtrip(): Сохранение identity и корреляции при передаче JSON.
# -> test_invalid_wire(): Отклонение неоднозначных и неверных запросов.
# -> test_invalid_response(): Отклонение неверного подтверждения gateway.
# -> test_relay_configuration(): Ленивое назначение и согласованные сроки клиента.
# -> test_relay_http(): Настоящий HTTP-клиент и подставной gateway на loopback.
# -> test_no_request_after_budget_expiry(): Запрет новой отправки при исчерпании остатка времени.
# -> test_runtime_budget_and_mixed_modes(): Общие повторы runtime для direct и relay назначений.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from remote_watch import (
    Delivery,
    DeliveryMode,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Identity,
    Notification,
    NotificationRuntime,
    ResultSource,
    RetryPolicy,
    Route,
    WatcherConfig,
)
from remote_watch.adapters.relay import RelayChannel, RelayConfig
from remote_watch.relay_protocol import (
    MAX_REQUEST_BYTES,
    RelayRequest,
    decode_response,
    encode_response,
)


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Попытка со свежим сроком UTC
#------------------------------------------------------------------------------------------------------------------
def fresh_delivery(notification: Notification) -> Delivery:

    """Create a delivery whose UTC expiry has not passed during the current run.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :return: Delivery whose timestamps are fresh for the current test exchange.
    :rtype: Delivery
    """

    # notification - уведомление с заданными тестовыми данными.

    now = datetime.now(timezone.utc)
    event = replace(notification, created_at=now, expires_at=now + timedelta(seconds=60))
    return Delivery(notification=event, destination_id="local-phone", delivery_id="delivery-1")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение identity и корреляции при передаче JSON
#------------------------------------------------------------------------------------------------------------------
def test_wire_roundtrip(notification: Notification) -> None:

    """Preserve identity and delivery correlation without exposing local destination routing.

    :param notification: Immutable notification fixture.
    :type notification: Notification
    """

    # notification - уведомление с заданными тестовыми данными.

    delivery = fresh_delivery(notification)
    request = RelayRequest(delivery=delivery, alias="phone", remaining_ttl=10, timeout=8)
    data = json.dumps(request.to_dict()).encode()
    decoded = RelayRequest.from_bytes(data)
    assert decoded.delivery.notification == delivery.notification
    assert decoded.delivery.destination_id == "phone"
    assert b"local-phone" not in data
    response = encode_response(delivery, DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED,
                                                        provider_message_id="receipt-1"))
    result = decode_response(json.loads(response), delivery)
    assert result.source is ResultSource.RELAY and result.provider_message_id == "receipt-1"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение неоднозначных и неверных запросов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["duplicate", "version", "extra", "ttl", "timeout", "oversize", "notification"])
def test_invalid_wire(
    notification: Notification,
    mode: str,
) -> None:

    """Reject ambiguous envelopes and unsafe or inconsistent budgets.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param mode: Selected failure or success scenario.
    :type mode: str
    """

    # notification - уведомление с заданными тестовыми данными.
    # mode - выбранный сценарий ответа сервера.

    payload = RelayRequest(delivery=fresh_delivery(notification), alias="phone",
                           remaining_ttl=10, timeout=8).to_dict()
    if mode == "version":
        payload["schema_version"] = True
    elif mode == "extra":
        payload["provider_token"] = "synthetic"
    elif mode == "ttl":
        payload["remaining_ttl"] = float("nan")
    elif mode == "timeout":
        payload["timeout"] = 11
    elif mode == "notification":
        payload["notification"]["identity"]["unexpected"] = "value"
    data = json.dumps(payload).encode()
    if mode == "duplicate":
        data = b'{"schema_version":1,' + data[1:]
    if mode == "oversize":
        data = b" " * (MAX_REQUEST_BYTES + 1)
    with pytest.raises(ValueError):
        RelayRequest.from_bytes(data)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение неверного подтверждения gateway
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["id", "attempt", "bool", "version", "extra", "status", "retry", "receipt"])
def test_invalid_response(
    notification: Notification,
    mode: str,
) -> None:

    """Do not accept a mismatched or contradictory gateway receipt.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param mode: Selected failure or success scenario.
    :type mode: str
    """

    # notification - уведомление с заданными тестовыми данными.
    # mode - выбранный сценарий ответа сервера.

    delivery = fresh_delivery(notification)
    payload = json.loads(encode_response(delivery, DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)))
    if mode in ("id", "attempt", "bool", "version", "extra"):
        key, value = {"id": ("delivery_id", "wrong"), "attempt": ("attempt", 2), "bool": ("attempt", True),
                      "version": ("schema_version", 2), "extra": ("queue_id", "not-supported")}[mode]
        payload[key] = value
    elif mode == "status":
        payload["result"]["status"] = "queued"
    elif mode == "retry":
        payload["result"]["retry_after"] = 3
    elif mode == "receipt":
        payload["result"]["provider_message_id"] = "receipt"
        payload["result"]["status"] = "unknown"
    with pytest.raises(ValueError):
        decode_response(payload, delivery)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ленивое назначение и согласованные сроки клиента
#------------------------------------------------------------------------------------------------------------------
def test_relay_configuration() -> None:

    """Keep a lazy relay factory and require room for the network within attempt timeout."""

    config = RelayConfig(endpoint="https://gateway.invalid", alias="phone", token_env="RW_RELAY_TEST")
    destination = config.destination("local-phone")
    assert destination.mode is DeliveryMode.RELAY
    assert isinstance(destination.channel_factory(), RelayChannel)
    with pytest.raises(ValueError, match="exceed"):
        config.destination("phone", retry=RetryPolicy(attempt_timeout=5))
    for endpoint in ("http://gateway.invalid", "https://user:password@gateway.invalid",
                     "https://gateway.invalid/?key=x"):
        with pytest.raises(ValueError):
            replace(config, endpoint=endpoint)
    with pytest.raises(ValueError):
        replace(config, alias="https://provider.invalid/chat")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящий HTTP-клиент и подставной gateway на loopback
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["accepted", "mismatch", "duplicate", "oversize", "auth", "rate", "disconnect"])
def test_relay_http(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:

    """Exercise the real HTTP client against a temporary loopback gateway stub.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param mode: Selected failure or success scenario.
    :type mode: str
    """

    # notification - уведомление с заданными тестовыми данными.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # mode - выбранный сценарий ответа сервера.

    web = pytest.importorskip("aiohttp.web")
    monkeypatch.setenv("RW_RELAY_TEST", "synthetic_service_token_01234567890123456789")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Serve one request and verify its fixed endpoint, bearer credential and schema."""

        deliveries = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Ответ локального сервера в выбранном режиме
        #----------------------------------------------------------------------------------------------------------
        async def respond(request: web.Request) -> web.Response:

            """Model receipt loss and malformed responses without a real provider.

            :param request: Incoming loopback HTTP request.
            :type request: web.Request

            :return: Synthetic relay response for the selected validation scenario.
            :rtype: web.Response
            """

            # request - HTTP-запрос к подставному gateway.

            assert request.headers["Authorization"].startswith("Bearer synthetic_service_token_")
            decoded = RelayRequest.from_bytes(await request.read())
            deliveries.append(decoded)
            result = DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)
            body = encode_response(decoded.delivery, result)
            if mode == "auth":
                return web.Response(status=403)
            if mode == "rate":
                return web.Response(status=429, headers={"Retry-After": "5"})
            if mode == "disconnect":
                request.transport.close()
            if mode == "mismatch":
                body = body.replace(b"delivery-1", b"delivery-2")
            if mode == "duplicate":
                body = b'{"schema_version":1,' + body[1:]
            if mode == "oversize":
                body += b" " * 4096
            return web.Response(body=body, content_type="application/json")
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_post("/v1/notifications", respond)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            site = web.SockSite(runner, sock)
            await site.start()
            channel = RelayChannel(RelayConfig(endpoint=f"http://127.0.0.1:{port}", alias="phone",
                                                token_env="RW_RELAY_TEST", allow_http=True))
            try:
                await channel.open()
                result = await channel.send(replace(fresh_delivery(notification), remaining_timeout=3))
                assert len(deliveries) == 1
                assert 0 < deliveries[0].timeout <= 2
                assert deliveries[0].remaining_ttl <= 3
                expected = {"accepted": DeliveryStatus.PROVIDER_ACCEPTED, "auth": DeliveryStatus.PERMANENT_FAILURE,
                            "rate": DeliveryStatus.RATE_LIMITED}.get(mode, DeliveryStatus.UNKNOWN)
                assert result.status is expected and result.source is ResultSource.RELAY
                if mode == "auth":
                    assert result.reason_code == "relay_auth_denied"
                if mode == "rate":
                    assert result.retry_after == 5
            finally:
                await channel.close()
                await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет новой отправки при исчерпании остатка времени
#------------------------------------------------------------------------------------------------------------------
def test_no_request_after_budget_expiry(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Do not start a request when the remaining runtime budget cannot cover the network margin.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # notification - уведомление с заданными тестовыми данными.
    # monkeypatch - фикстура подмены зависимостей и окружения.

    monkeypatch.setenv("RW_RELAY_TEST", "synthetic_service_token_01234567890123456789")
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Open a client without connecting and exercise a locally exhausted budget."""

        channel = RelayChannel(RelayConfig(endpoint="https://gateway.invalid", alias="phone",
                                            token_env="RW_RELAY_TEST"))
        await channel.open()
        try:
            result = await channel.send(replace(fresh_delivery(notification), remaining_timeout=0.5))
            assert result.reason_code == "relay_budget_exhausted"
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Общие повторы runtime для direct и relay назначений
#------------------------------------------------------------------------------------------------------------------
def test_runtime_budget_and_mixed_modes(
    identity: Identity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Keep retries in runtime while direct and relay destinations receive one event independently.

    :param identity: Explicit application identity.
    :type identity: Identity

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # identity - явные сведения об отправителе.
    # monkeypatch - фикстура подмены зависимостей и окружения.

    sent = []

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Подставной канал для проверки срока попытки
    #--------------------------------------------------------------------------------------------------------------
    class Channel:
        """Capture the actual budget assigned to each runtime attempt."""

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Открытие канала в принадлежащем ему цикле событий
        #----------------------------------------------------------------------------------------------------------
        async def open(self) -> None:

            """Avoid external resources."""

            pass
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Одна попытка отправки без собственного цикла повторов
        #----------------------------------------------------------------------------------------------------------
        async def send(
            self,
            delivery: Delivery,
        ) -> DeliveryResult:

            """Fail the first relay attempt and accept its runtime-owned retry.

            :param delivery: Immutable delivery attempt.
            :type delivery: Delivery

            :return: Sanitized provider result.
            :rtype: DeliveryResult
            """

            # delivery - подготовленные данные одной попытки.

            sent.append(delivery)
            if delivery.destination_id == "relay" and delivery.attempt == 1:
                return DeliveryResult(status=DeliveryStatus.TRANSIENT_FAILURE, source=ResultSource.RELAY)
            return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Закрытие канала и освобождение временных данных
        #----------------------------------------------------------------------------------------------------------
        async def close(self) -> None:

            """Avoid external cleanup."""

            pass
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    policy = RetryPolicy(max_attempts=2, backoff_base=0.001, backoff_cap=0.001, ttl=0.5)
    destinations = tuple(Destination(destination_id=mode.value, mode=mode, channel_factory=Channel, retry=policy)
                         for mode in (DeliveryMode.DIRECT, DeliveryMode.RELAY))
    runtime = NotificationRuntime(WatcherConfig(identity=identity, destinations=destinations,
                                               routes=(Route(destination_ids=("direct", "relay")),)))
    logger = logging.Logger("mixed", logging.INFO)
    logger.addHandler(runtime.handler)
    with runtime:
        logger.error("one event")
    relay = [d for d in sent if d.destination_id == "relay"]
    assert len(relay) == 2 and relay[0].delivery_id == relay[1].delivery_id
    assert all(0 < d.remaining_timeout <= 0.5 for d in sent)
    assert runtime.stats("direct").accepted == runtime.stats("relay").accepted == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.contract.test_relay не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
