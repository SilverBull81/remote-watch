# Проверки безопасной диагностики ntfy, совместимости relay и пределов уведомления.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Тесты:
# -> test_diagnostic_values(): Отклонение недопустимых значений диагностики.
# -> test_response_versions(): Совместимость прежнего ответа и новой диагностики.
# -> test_relay_limits(): Единые ограничения уведомления на обеих сторонах relay.
# -> test_relay_rejects_before_post(): Отказ по размеру до сетевой попытки.
# -> test_ntfy_diagnostics(): Сохранение безопасных чисел через обе сетевые границы.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import io
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from remote_watch import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    Notification,
    RetryPolicy,
    SnapshotLimits,
)
from remote_watch.adapters.ntfy import NtfyChannel, NtfyConfig
from remote_watch.adapters.relay import RelayChannel, RelayConfig
from remote_watch.diagnostics.field_smoke import _Journal, _ObservedChannel
from remote_watch.gateway.config import GatewayConfig, GatewayPrincipal
from remote_watch.gateway.server import Gateway
from remote_watch.relay_protocol import (
    DIAGNOSTIC_FIELDS,
    RelayRequest,
    decode_response,
    encode_json,
    encode_response,
)

pytest.importorskip("aiohttp")


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение недопустимых значений диагностики
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field", DIAGNOSTIC_FIELDS)
@pytest.mark.parametrize("value", [True, "private_value", -1, 2**40])
def test_diagnostic_values(
    field: str,
    value: Any,
) -> None:

    """Keep diagnostic fields bounded and reject text or bool values.

    :param field: Diagnostic field name.
    :type field: str

    :param value: Untrusted delay value.
    :type value: Any
    """

    # field - проверяемое числовое поле.
    # value - значение задержки из ответа.

    with pytest.raises((TypeError, ValueError)):
        DeliveryResult(status=DeliveryStatus.UNKNOWN, **{field: value})
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Совместимость прежнего ответа и новой диагностики
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("schema_version", [1, 2])
def test_response_versions(
    notification: Notification,
    schema_version: int,
) -> None:

    """Preserve v1 shape and strictly correlate v2 diagnostic replies.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param schema_version: Explicit wire schema version.
    :type schema_version: int
    """

    # notification - уведомление с заданными тестовыми данными.
    # schema_version - явная версия данных relay.

    delivery = Delivery(notification=notification, destination_id="phone", delivery_id="test")
    result = DeliveryResult(status=DeliveryStatus.TRANSIENT_FAILURE, http_status=503, provider_code=50301,
                            message_bytes=4096, request_bytes=4210)
    payload = json.loads(encode_response(delivery, result, schema_version=schema_version))
    decoded = decode_response(payload, delivery, schema_version=schema_version)
    if schema_version == 1:
        assert set(payload["result"]) == {"status", "reason_code", "provider_message_id", "retry_after"}
        assert decoded.http_status is None
    else:
        assert decoded.http_status == 503 and decoded.provider_code == 50301
        assert decoded.request_bytes == 4210 and decoded.message_bytes == 4096
        payload["result"]["http_status"] = "private_value"
        with pytest.raises(ValueError):
            decode_response(payload, delivery, schema_version=2)
    with pytest.raises(ValueError):
        decode_response(payload, delivery, schema_version=3 - schema_version)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Единые ограничения уведомления на обеих сторонах relay
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("size", [8192, 8193, 10000])
def test_relay_limits(
    notification: Notification,
    size: int,
) -> None:

    """Apply the receiver's fixed limits even when the sender allows larger local snapshots.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param size: Maximum chunk size in bytes.
    :type size: int
    """

    # notification - уведомление с заданными тестовыми данными.
    # size - размер читаемой части, байт.

    event = replace(notification, message="x" * size,
                    limits=SnapshotLimits(message_max_bytes=12000, event_max_bytes=20000))
    delivery = Delivery(notification=event, destination_id="phone", delivery_id="test")
    if size > 8192:
        with pytest.raises(ValueError):
            RelayRequest(delivery=delivery, alias="phone", remaining_ttl=10, timeout=8)
    else:
        request = RelayRequest(delivery=delivery, alias="phone", remaining_ttl=10, timeout=8)
        decoded = RelayRequest.from_bytes(encode_json(request.to_dict()).encode())
        assert decoded.delivery.notification.to_dict() == event.to_dict()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ по размеру до сетевой попытки
#------------------------------------------------------------------------------------------------------------------
def test_relay_rejects_before_post(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Classify an oversized outgoing event as permanent without invoking HTTP.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # notification - уведомление с заданными тестовыми данными.
    # monkeypatch - фикстура подмены зависимостей и окружения.

    monkeypatch.setenv("RW_DIAGNOSTIC_RELAY", "synthetic_gateway_token_123456789012345")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Prepare a valid local event that exceeds the wire contract."""

        now = datetime.now(timezone.utc)
        event = replace(notification, message="x" * 10000, created_at=now, expires_at=now + timedelta(seconds=60),
                        limits=SnapshotLimits(message_max_bytes=12000, event_max_bytes=20000))
        channel = RelayChannel(RelayConfig(endpoint="https://example.invalid", alias="phone",
                                            token_env="RW_DIAGNOSTIC_RELAY"))

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Один HTTP POST без скрытых повторов
        #----------------------------------------------------------------------------------------------------------
        async def post(
            *args: Any,
            **kwargs: Any,
        ) -> None:

            """Fail the test if any HTTP request is attempted.

            :param args: Context manager exception details.
            :type args: Any

            :param kwargs: Captured request or client keyword arguments.
            :type kwargs: Any
            """

            # args - сведения об исключении при выходе из контекста.
            # kwargs - именованные параметры запроса или клиента.

            raise AssertionError("HTTP must not be attempted")
        #----------------------------------------------------------------------------------------------------------

        monkeypatch.setattr(channel._http, "post", post)
        try:
            await channel.open()
            result = await channel.send(Delivery(notification=event, destination_id="phone", delivery_id="test"))
            assert result.status is DeliveryStatus.PERMANENT_FAILURE
            assert result.reason_code == "relay_payload_limits"
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение безопасных чисел через обе сетевые границы
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,code", [(200, None), (408, 40801), (500, 50001), (502, "private_value"),
                                         (503, True), (429, 42901), (400, 2**50)])
def test_ntfy_diagnostics(
    notification: Notification,
    status: int,
    code: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:

    """Preserve safe numbers through ntfy, gateway v1/v2 and the field journal.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param status: HTTP response status.
    :type status: int

    :param code: Controlled provider error code.
    :type code: Any

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param caplog: Pytest log capture fixture.
    :type caplog: pytest.LogCaptureFixture
    """

    # notification - уведомление с заданными тестовыми данными.
    # status - код HTTP-ответа.
    # code - числовой либо недопустимый код ошибки провайдера.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # caplog - перехват журнала для проверки отсутствия токенов.

    from aiohttp import web

    monkeypatch.setenv("RW_DIAGNOSTIC_RELAY", "synthetic_gateway_token_123456789012345")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Use real HTTP on both hops and compare diagnostics with transmitted bytes."""

        requests: list[bytes] = []

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Подставной ответ ntfy с приватным текстом ошибки
        #----------------------------------------------------------------------------------------------------------
        async def publish(request: web.Request) -> web.Response:

            """Return a controlled provider response containing deliberately private text.

            :param request: Incoming loopback HTTP request.
            :type request: web.Request

            :return: Synthetic HTTP response to the recorded publication attempt.
            :rtype: web.Response
            """

            # request - входящий запрос локального тестового сервера.

            raw = await request.read()
            requests.append(raw)
            return web.json_response({"code": code, "error": "private_value", "event": "message",
                                      "topic": "synthetic", "id": "synthetic"}, status=status)
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_post("/", publish)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        endpoint = f"http://127.0.0.1:{runner.addresses[0][1]}"
        config = NtfyConfig(topic="synthetic", token_env=None, endpoint=endpoint, allow_http=True)
        now = datetime.now(timezone.utc)
        event = replace(notification, message="Текст 📡 " * 450,
                        created_at=now, expires_at=now + timedelta(seconds=60))
        delivery = Delivery(notification=event, destination_id="phone", delivery_id="test")
        gateway = Gateway(GatewayConfig(
            destinations=(config.destination("phone", retry=RetryPolicy(max_attempts=1)),),
            principals=(GatewayPrincipal(name="test", token_env="RW_DIAGNOSTIC_RELAY", identity=event.identity,
                                         aliases=("phone",), min_interval=0),), destination_interval=0))
        channel = NtfyChannel(config)
        try:
            await gateway.start(port=0)
            journal = _Journal()
            observed = _ObservedChannel(lambda: channel, journal, "ntfy")
            await observed.open()
            direct = await observed.send(delivery)
            stream = io.StringIO()
            journal.flush(stream)
            record = json.loads(stream.getvalue())
            assert record["http_status"] == status
            assert record["message_bytes"] == len(json.loads(requests[0])["message"].encode())
            assert record["request_bytes"] == len(requests[0])
            assert "private_value" not in stream.getvalue()

            for schema in (1, 2):
                relay = RelayChannel(RelayConfig(endpoint=f"http://127.0.0.1:{gateway.port}", alias="phone",
                    token_env="RW_DIAGNOSTIC_RELAY", allow_http=True, schema_version=schema))
                try:
                    await relay.open()
                    result = await relay.send(delivery)
                    assert result.status is direct.status
                    for field in DIAGNOSTIC_FIELDS:
                        assert getattr(result, field) == (getattr(direct, field) if schema == 2 else None)
                finally:
                    await relay.close()
            assert len(requests) == 3
            expected_code = code if status != 200 and type(code) is int and code <= 999999 else None
            assert direct.provider_code == expected_code
            assert "private_value" not in repr(direct)
        finally:
            await channel.close()
            await gateway.close()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
    assert "private_value" not in caplog.text
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_diagnostics не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
