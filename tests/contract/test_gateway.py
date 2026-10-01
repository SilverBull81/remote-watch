# Проверки настоящего gateway на loopback без внешней сети и реальных credentials.
#
# Version 1.0.5
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-165638
#
# Классы:
# -> Channel: Управляемый канал проверки попыток и отмены.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска ресурсов.
#    Интерфейс:
#    -> open(): Подготовка тестового канала.
#    -> send(): Одна управляемая попытка отправки.
#    -> close(): Ограниченное завершение и очистка.
#
# Тесты:
# -> settings(): Настройки точных прав тестового отправителя.
# -> envelope(): Запрос с заданным сроком и идентификатором доставки.
# -> credential(): Временный вымышленный токен.
# -> test_gateway_requests(): Проверка HTTP-доступа, формата и срока до отправки.
# -> test_gateway_attempt_outcomes(): Классификация одной попытки без повторов сервера.
# -> test_gateway_overload(): Немедленный отказ при превышении ёмкости.
# -> test_gateway_body_deadline(): Ограничение времени чтения незавершённого тела.
# -> test_gateway_lifecycle(): Очистка при ошибках и отмене запуска или остановки.
# -> test_gateway_mixed_runtime(): Настоящие HTTP-адаптеры в смешанном direct и relay режиме.
# -> test_gateway_start_guards(): Защита запуска и независимость очистки от отмены владельца.
# -> test_gateway_rate_and_chunked(): Ограничение частоты и размера chunked-запроса.
# -> test_gateway_lost_reply(): Возможный дубликат при потере подтверждения провайдера.
# -> test_gateway_multiple_apps(): Раздельные права и независимая отправка четырёх приложений.
# -> test_gateway_clock_tolerance(): Расхождение UTC и границы допуска через настоящий HTTP.
# -> test_gateway_elapsed_budget(): Допуск часов не восстанавливает исчерпанное время запроса.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from remote_watch import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Notification,
    RemoteWatcher,
    ResultSource,
    RetryPolicy,
    Route,
    WatcherConfig,
)
from remote_watch.adapters.relay import RelayChannel, RelayConfig
from remote_watch.gateway.config import GatewayConfig, GatewayPrincipal
from remote_watch.gateway.json_config import load_gateway_config
from remote_watch.gateway.server import Gateway
from remote_watch.notifications._context import delivery_context
from remote_watch.relay_protocol import RelayRequest, decode_response

aiohttp = pytest.importorskip("aiohttp")
TOKEN = "synthetic_gateway_credential_1234567890"


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Управляемый канал проверки попыток и отмены
#------------------------------------------------------------------------------------------------------------------
class Channel:
    """Observe attempts, cancellation and ownership without provider network traffic."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        mode: str = 'success',
    ) -> None:

        """Prepare a selectable provider outcome.

        :param mode: Selected fake-channel behavior.
        :type mode: str
        """

        # mode - заданный режим тестового канала.

        self.mode = mode
        self.calls: list[Delivery] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.opened = False
        self.cancelled = False
        self.cancelled_event = asyncio.Event()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка тестового канала
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Record startup and optionally reproduce partial failure."""

        self.opened = True
        if self.mode == "open_error":
            raise RuntimeError("synthetic private provider details")
        if self.mode == "open_wait":
            self.entered.set()
            await self.release.wait()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна управляемая попытка отправки
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
    ) -> DeliveryResult:

        """Return one outcome or remain cancellably active until released.

        :param delivery: Validated provider attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        assert delivery_context.get()
        self.calls.append(delivery)
        self.entered.set()
        try:
            if self.mode == "wait":
                await self.release.wait()
            if self.mode == "error":
                raise OSError("synthetic private provider details")
            if self.mode == "relay":
                return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, source=ResultSource.RELAY)
            if self.mode == "rate":
                return DeliveryResult(status=DeliveryStatus.RATE_LIMITED, retry_after=2)
            return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, provider_message_id="synthetic")
        except asyncio.CancelledError:
            self.cancelled = True
            self.cancelled_event.set()
            raise
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ограниченное завершение и очистка
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Record cleanup, with an optional cancellation-cooperative stall."""

        self.closed = True
        if self.mode == "close_wait":
            await self.release.wait()
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Настройки точных прав тестового отправителя
#------------------------------------------------------------------------------------------------------------------
def settings(
    notification: Notification,
    channel: Channel,
    **changes: Any,
) -> GatewayConfig:

    """Create exact grants and a one-attempt destination for tests.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param channel: Owned provider channel.
    :type channel: Channel

    :param changes: Explicit configuration overrides.
    :type changes: Any

    :return: Gateway settings with isolated test destinations and credentials.
    :rtype: GatewayConfig
    """

    # notification - синтетическое уведомление с заданным временем.
    # channel - канал, принадлежащий серверу.
    # changes - переопределяемые настройки сценария.

    return GatewayConfig(destinations=(Destination(destination_id="phone", channel_factory=lambda: channel,
        retry=RetryPolicy(max_attempts=1)),), principals=(GatewayPrincipal(name="test", token_env="RW_GW_TEST",
        identity=notification.identity, aliases=("phone",), min_interval=0),),
        destination_interval=0, **changes)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрос с заданным сроком и идентификатором доставки
#------------------------------------------------------------------------------------------------------------------
def envelope(notification: Notification) -> RelayRequest:

    """Create a finite request while preserving the fixture's controlled UTC time.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :return: Synthetic relay request with bounded lifetime and exact test identity.
    :rtype: RelayRequest
    """

    # notification - синтетическое уведомление с заданным временем.

    return RelayRequest(delivery=Delivery(notification=notification, destination_id="phone", delivery_id="one"),
        alias="phone", remaining_ttl=10, timeout=8)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Временный вымышленный токен
#------------------------------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def credential(monkeypatch: pytest.MonkeyPatch) -> None:

    """Install only synthetic service credentials in the test process.

    :param monkeypatch: Pytest environment patch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # monkeypatch - фикстура временной подмены окружения.

    monkeypatch.setenv("RW_GW_TEST", TOKEN)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка HTTP-доступа, формата и срока до отправки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["success", "missing_token", "wrong_token", "duplicate_auth", "identity",
    "alias", "expired", "future", "schema", "duplicate_json", "too_large", "compressed", "content_type",
    "query", "commands", "expect"])
def test_gateway_requests(
    notification: Notification,
    case: str,
    caplog: pytest.LogCaptureFixture,
) -> None:

    """Check authentication, exact grants, bounds and expiry before provider dispatch.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param case: Selected test case.
    :type case: str

    :param caplog: Pytest log capture.
    :type caplog: pytest.LogCaptureFixture
    """

    # notification - синтетическое уведомление с заданным временем.
    # case - выбранный сценарий проверки.
    # caplog - перехват logging для проверки отсутствия приватных данных.

    caplog.set_level(logging.DEBUG)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Send one controlled HTTP request to the actual gateway."""

        channel = Channel()
        gateway = Gateway(settings(notification, channel, future_tolerance=30 if case == "expired" else 0),
            utc_now=lambda: notification.created_at)
        await gateway.start(port=0)
        request = envelope(notification)
        payload = request.to_dict()
        path = "/v1/notifications"
        headers = [("Authorization", "Bearer " + TOKEN), ("Content-Type", "application/json")]
        expected = 200
        if case == "missing_token":
            headers = headers[1:]
            expected = 401
        elif case == "wrong_token":
            headers[0] = ("Authorization", "Bearer " + "x" * 40)
            expected = 401
        elif case == "duplicate_auth":
            headers.append(headers[0])
            expected = 401
        elif case == "identity":
            payload["notification"]["identity"]["host"] = "forged"
            expected = 403
        elif case == "alias":
            payload["alias"] = "forbidden"
            expected = 403
        elif case == "expired":
            gateway._utc_now = lambda: notification.expires_at
        elif case == "future":
            gateway._utc_now = lambda: notification.created_at - timedelta(seconds=1)
        elif case == "schema":
            payload["schema_version"] = True
            expected = 400
        elif case == "compressed":
            headers.append(("Content-Encoding", "gzip"))
            expected = 400
        elif case == "content_type":
            headers[1] = ("Content-Type", "text/plain")
            expected = 415
        elif case == "query":
            path += "?token=synthetic"
            expected = 400
        elif case == "commands":
            path = "/v1/commands"
            expected = 404
        elif case == "expect":
            headers.append(("Expect", "100-continue"))
            expected = 417
        raw = json.dumps(payload).encode()
        if case == "duplicate_json":
            raw = b'{"schema_version":1,' + raw[1:]
            expected = 400
        elif case == "too_large":
            raw = b"x" * 65537
            expected = 413
        try:
            async with aiohttp.ClientSession() as client:
                async with client.post(f"http://127.0.0.1:{gateway.port}" + path,
                        data=raw, headers=headers) as response:
                    assert response.status == expected
                    body = await response.read()
            if case in ("expired", "future"):
                assert decode_response(json.loads(body), request.delivery).reason_code == (
                    "relay_expired" if case == "expired" else "relay_clock_skew")
            assert len(channel.calls) == int(case == "success")
            assert gateway.stats()["active"] == 0
        finally:
            await gateway.close()
        assert channel.closed and not gateway._tokens
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
    assert TOKEN not in caplog.text
    assert "synthetic private" not in caplog.text
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Классификация одной попытки без повторов сервера
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["error", "wait", "rate", "relay"])
def test_gateway_attempt_outcomes(
    notification: Notification,
    mode: str,
) -> None:

    """Return safe classified outcomes without adding provider retries.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param mode: Selected fake-channel behavior.
    :type mode: str
    """

    # notification - синтетическое уведомление с заданным временем.
    # mode - заданный режим тестового канала.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Use the real relay client against one configured provider attempt."""

        now = datetime.now(timezone.utc)
        event = replace(notification, created_at=now, expires_at=now + timedelta(seconds=60))
        channel = Channel(mode)
        gateway = Gateway(settings(event, channel, attempt_timeout=0.05))
        await gateway.start(port=0)
        client = RelayChannel(RelayConfig(endpoint=f"http://127.0.0.1:{gateway.port}", alias="phone",
            token_env="RW_GW_TEST", allow_http=True))
        try:
            await client.open()
            result = await client.send(envelope(event).delivery)
            expected = DeliveryStatus.RATE_LIMITED if mode == "rate" else DeliveryStatus.UNKNOWN
            assert result.status is expected and result.source is ResultSource.RELAY
            assert len(channel.calls) == 1
            assert 0 < channel.calls[0].remaining_timeout <= 0.05
            if mode == "wait":
                assert channel.cancelled
        finally:
            await client.close()
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Немедленный отказ при превышении ёмкости
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("limit", ["global", "principal", "destination"])
def test_gateway_overload(
    notification: Notification,
    limit: str,
) -> None:

    """Reject excess requests promptly instead of accumulating waiting tasks.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param limit: Admission limit being exercised.
    :type limit: str
    """

    # notification - синтетическое уведомление с заданным временем.
    # limit - проверяемое ограничение приёма.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Hold one attempt and test independent admission limits."""

        channel = Channel("wait")
        config = settings(notification, channel, capacity=1 if limit == "global" else 32)
        if limit == "principal":
            config = replace(config, principals=(replace(config.principals[0], capacity=1),))
        gateway = Gateway(config, utc_now=lambda: notification.created_at)
        await gateway.start(port=0)
        try:
            async with aiohttp.ClientSession(headers={"Authorization": "Bearer " + TOKEN}) as client:
                url = f"http://127.0.0.1:{gateway.port}/v1/notifications"
                first = asyncio.create_task(client.post(url, json=envelope(notification).to_dict()))
                await asyncio.wait_for(channel.entered.wait(), 2)
                async with client.post(url, json=envelope(notification).to_dict()) as response:
                    assert response.status == 429
                    assert response.headers["Retry-After"] == "1"
                assert gateway.stats()["active"] == 1
                assert len(channel.calls) == 1
                channel.release.set()
                response = await first
                await response.read()
                response.release()
        finally:
            channel.release.set()
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничение времени чтения незавершённого тела
#------------------------------------------------------------------------------------------------------------------
def test_gateway_body_deadline(notification: Notification) -> None:

    """Reserve admission before body reading and release it after a stalled upload.

    :param notification: Synthetic notification fixture.
    :type notification: Notification
    """

    # notification - синтетическое уведомление с заданным временем.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Send headers without the advertised body using a raw loopback connection."""

        channel = Channel()
        gateway = Gateway(settings(notification, channel, body_timeout=0.05),
            utc_now=lambda: notification.created_at)
        await gateway.start(port=0)
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
        try:
            writer.write(("POST /v1/notifications HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " + TOKEN +
                "\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n").encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 2)
            assert b"408" in response.split(b"\r\n", 1)[0]
            assert gateway.stats()["body_timeout"] == 1
            assert gateway.stats()["active"] == 0 and not channel.calls
        finally:
            writer.close()
            await writer.wait_closed()
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Очистка при ошибках и отмене запуска или остановки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["open_error", "open_wait", "cancel_start", "close_wait", "wait", "disconnect"])
def test_gateway_lifecycle(
    notification: Notification,
    mode: str,
) -> None:

    """Clean partial startup and bound shutdown without skipping other providers.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param mode: Selected fake-channel behavior.
    :type mode: str
    """

    # notification - синтетическое уведомление с заданным временем.
    # mode - заданный режим тестового канала.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise cancellation and timeout with cooperative fake channels."""

        channel = Channel("open_wait" if mode == "cancel_start" else "wait" if mode == "disconnect" else mode)
        other = Channel()
        config = settings(notification, channel, startup_timeout=0.05, shutdown_timeout=0.15)
        config = replace(config, destinations=(*config.destinations, Destination(destination_id="other",
            channel_factory=lambda: other, retry=RetryPolicy(max_attempts=1))))
        gateway = Gateway(config, utc_now=lambda: notification.created_at)
        if mode in ("open_error", "open_wait", "cancel_start"):
            if mode == "cancel_start":
                start = asyncio.create_task(gateway.start(port=0))
                await asyncio.wait_for(channel.entered.wait(), 2)
                start.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await start
            else:
                with pytest.raises((RuntimeError, asyncio.TimeoutError)):
                    await gateway.start(port=0)
        else:
            await gateway.start(port=0)
            async with aiohttp.ClientSession(headers={"Authorization": "Bearer " + TOKEN}) as client:
                pending = None
                if mode in ("wait", "disconnect"):
                    pending = asyncio.create_task(client.post(f"http://127.0.0.1:{gateway.port}/v1/notifications",
                        json=envelope(notification).to_dict()))
                    await asyncio.wait_for(channel.entered.wait(), 2)
                    if mode == "disconnect":
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                        await asyncio.wait_for(channel.cancelled_event.wait(), 2)
                await asyncio.wait_for(gateway.close(), 1)
                if pending:
                    await asyncio.gather(pending, return_exceptions=True)
            assert other.closed
        await gateway.close()
        await asyncio.sleep(0)
        assert channel.closed and not gateway._tokens and gateway._state == "closed"
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящие HTTP-адаптеры в смешанном direct и relay режиме
#------------------------------------------------------------------------------------------------------------------
def test_gateway_mixed_runtime(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Deliver Telegram through the real gateway and ntfy directly from one watcher.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param monkeypatch: Pytest environment patch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # notification - синтетическое уведомление с заданным временем.
    # monkeypatch - фикстура временной подмены окружения.

    monkeypatch.setenv("RW_GW_TELEGRAM", "123:synthetic")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Run actual provider HTTP adapters against a deterministic loopback service."""

        from aiohttp import web

        from remote_watch.adapters.ntfy import NtfyConfig
        from remote_watch.adapters.telegram import TelegramConfig

        received: list[str] = []

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Подставной HTTP-сервис Telegram и ntfy
        #----------------------------------------------------------------------------------------------------------
        async def provider(request: web.Request) -> web.Response:

            """Accept one Telegram or ntfy payload without external network access.

            :param request: Incoming HTTP request.
            :type request: web.Request

            :return: Synthetic provider response for the selected acceptance scenario.
            :rtype: web.Response
            """

            # request - входящий HTTP-запрос.

            payload = await request.json()
            if request.path == "/":
                received.append("ntfy")
                return web.json_response({"event": "message", "topic": payload["topic"], "id": "synthetic"})
            received.append("telegram")
            assert payload["chat_id"] == 123
            return web.json_response({"ok": True, "result": {"message_id": 123}})
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_post("/", provider)
        app.router.add_post("/bot123:synthetic/sendMessage", provider)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        endpoint = f"http://127.0.0.1:{runner.addresses[0][1]}"
        direct = RetryPolicy(max_attempts=1)
        grant = GatewayPrincipal(name="test", token_env="RW_GW_TEST",
            identity=notification.identity, aliases=("phone",))
        config = GatewayConfig(principals=(grant,), destinations=(TelegramConfig(token_env="RW_GW_TELEGRAM",
            chat_id=123, endpoint=endpoint, allow_http=True).destination("phone", retry=direct),))
        gateway = Gateway(config)
        try:
            await gateway.start(port=0)
            relay = RelayConfig(endpoint=f"http://127.0.0.1:{gateway.port}", alias="phone", token_env="RW_GW_TEST",
                allow_http=True).destination("telegram", retry=direct)
            ntfy = NtfyConfig(topic="synthetic", token_env=None, endpoint=endpoint,
                allow_http=True).destination("ntfy", retry=direct)
            watcher = RemoteWatcher(WatcherConfig(identity=notification.identity, destinations=(relay, ntfy),
                routes=(Route(destination_ids=("telegram", "ntfy")),)))
            async with watcher:
                watcher.logger.error("Смешанная доставка через gateway и напрямую")
            assert sorted(received) == ["ntfy", "telegram"]
            assert gateway.stats()["provider_accepted"] == 1
        finally:
            await gateway.close()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Защита запуска и независимость очистки от отмены владельца
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["missing", "duplicate", "tls", "cancel_close"])
def test_gateway_start_guards(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:

    """Fail closed on invalid credentials and unsafe binds; preserve cleanup on caller cancellation.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param monkeypatch: Pytest environment patch fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param case: Selected test case.
    :type case: str
    """

    # notification - синтетическое уведомление с заданным временем.
    # monkeypatch - фикстура временной подмены окружения.
    # case - выбранный сценарий проверки.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Check startup guards before any provider request is possible."""

        channel = Channel("close_wait" if case == "cancel_close" else "success")
        config = settings(notification, channel, shutdown_timeout=0.1)
        if case == "missing":
            monkeypatch.delenv("RW_GW_TEST")
        elif case == "duplicate":
            config = replace(config, principals=(*config.principals, replace(config.principals[0], name="other")))
        gateway = Gateway(config)
        if case == "cancel_close":
            await gateway.start(port=0)
            task = asyncio.create_task(gateway.close())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            channel.release.set()
            await gateway.close()
            assert channel.closed
        else:
            with pytest.raises(ValueError if case == "tls" else RuntimeError):
                await gateway.start(host="0.0.0.0" if case == "tls" else "127.0.0.1", port=0)
            assert not channel.opened
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Возможный дубликат при потере подтверждения провайдера
#------------------------------------------------------------------------------------------------------------------
def test_gateway_lost_reply(notification: Notification) -> None:

    """Allow a duplicate after provider success when the caller loses the first receipt.

    :param notification: Synthetic notification fixture.
    :type notification: Notification
    """

    # notification - синтетическое уведомление с заданным временем.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Discard the first response before validation, then retry the same delivery ID."""

        channel = Channel()
        gateway = Gateway(settings(notification, channel), utc_now=lambda: notification.created_at)
        await gateway.start(port=0)
        try:
            raw = json.dumps(envelope(notification).to_dict()).encode()
            reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
            writer.write(("POST /v1/notifications HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " + TOKEN +
                "\r\nContent-Type: application/json\r\nContent-Length: " + str(len(raw)) +
                "\r\n\r\n").encode() + raw)
            await writer.drain()
            # Заголовок уже отправлен после provider success, но результат первой попытки не разбирается.
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            writer.close()
            await writer.wait_closed()
            async with aiohttp.ClientSession(headers={"Authorization": "Bearer " + TOKEN}) as client:
                async with client.post(f"http://127.0.0.1:{gateway.port}/v1/notifications",
                        json=envelope(notification).to_dict()) as response:
                    assert response.status == 200
                    result = decode_response(await response.json(), envelope(notification).delivery)
                    assert result.status is DeliveryStatus.PROVIDER_ACCEPTED
            assert len(channel.calls) == 2
            assert channel.calls[0].delivery_id == channel.calls[1].delivery_id
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничение частоты и размера chunked-запроса
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["principal", "destination", "chunked"])
def test_gateway_rate_and_chunked(
    notification: Notification,
    case: str,
) -> None:

    """Enforce bounded rates and body sizes independently of Content-Length.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param case: Selected admission scenario.
    :type case: str
    """

    # notification - уведомление с заданным временем.
    # case - ограничение частоты приложения, назначения либо тела chunked.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Отправка запросов без внешней сети
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Check explicit 429 delay and reject a streamed oversized body."""

        channel = Channel()
        config = settings(notification, channel)
        if case == "principal":
            config = replace(config, principals=(replace(config.principals[0], min_interval=10),))
        elif case == "destination":
            config = replace(config, destination_interval=10)
        gateway = Gateway(config, utc_now=lambda: notification.created_at)
        await gateway.start(port=0)
        try:
            async with aiohttp.ClientSession(headers={"Authorization": "Bearer " + TOKEN}) as client:
                url = f"http://127.0.0.1:{gateway.port}/v1/notifications"
                if case == "chunked":
                    async with client.post(url, data=b"x" * 65537, chunked=True,
                            headers={"Content-Type": "application/json"}) as response:
                        assert response.status == 413
                    assert not channel.calls
                else:
                    async with client.post(url, json=envelope(notification).to_dict()) as response:
                        assert response.status == 200
                        await response.read()
                    async with client.post(url, json=envelope(notification).to_dict()) as response:
                        assert response.status == 429
                        assert 1 <= int(response.headers["Retry-After"]) <= 10
                    assert len(channel.calls) == 1
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Раздельные права и независимая отправка четырёх приложений
#------------------------------------------------------------------------------------------------------------------
def test_gateway_multiple_apps(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Route four applications through one listener while enforcing separate grants.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param monkeypatch: Temporary environment patch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # notification - синтетическое уведомление с фиксированным временем.
    # monkeypatch - временное окружение с вымышленными сервисными токенами.

    async def scenario() -> None:

        """Exercise concurrent destinations and cross-application access denial."""

        config = load_gateway_config(Path(__file__).parents[2] / "docs/examples/gateway_config.example.json")
        channels = [Channel("wait"), Channel(), Channel(), Channel()]
        principals = tuple(replace(item, min_interval=0) for item in config.principals)
        destinations = tuple(replace(item, channel_factory=lambda channel=channel: channel)
                             for item, channel in zip(config.destinations, channels))
        config = replace(config, destinations=destinations, principals=principals, destination_interval=0)
        tokens = [f"synthetic_application_{index}_credential_123456789" for index in range(4)]
        for principal, token in zip(principals, tokens):
            monkeypatch.setenv(principal.token_env, token)

        gateway = Gateway(config, utc_now=lambda: notification.created_at)
        await gateway.start(port=0)
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{gateway.port}/v1/notifications"

                async def post(
                    index: int,
                    alias: str,
                    impersonate: bool = False,
                ) -> int:

                    """Send one correlated request using an application's own token.

                    :param index: Application index.
                    :type index: int

                    :param alias: Requested destination alias.
                    :type alias: str

                    :param impersonate: Whether to claim another application's identity.
                    :type impersonate: bool

                    :return: HTTP status after reading the response.
                    :rtype: int
                    """

                    # index - номер приложения и его токена.
                    # alias - запрошенное назначение.
                    # impersonate - попытка подменить принадлежность отправителя.

                    identity = principals[1 if impersonate else index].identity
                    item = replace(envelope(replace(notification, identity=identity)), alias=alias)
                    async with session.post(url, json=item.to_dict(), headers={
                        "Authorization": f"Bearer {tokens[index]}", "Content-Type": "application/json",
                    }) as response:
                        body = await response.read()
                        if response.status == 200:
                            result = decode_response(json.loads(body), item.delivery)
                            assert result.status is DeliveryStatus.PROVIDER_ACCEPTED
                        return response.status
                #--------------------------------------------------------------------------------------------------
                #--------------------------------------------------------------------------------------------------

                # Даже знание чужого alias или Identity не расширяет права своего токена.
                assert await post(0, principals[1].aliases[0]) == 403
                assert await post(0, principals[1].aliases[0], impersonate=True) == 403
                assert not any(channel.calls for channel in channels)

                # Задержка первого чата не останавливает отправку в три других чата.
                pending = asyncio.create_task(post(0, principals[0].aliases[0]))
                try:
                    await asyncio.wait_for(channels[0].entered.wait(), 2)
                    statuses = await asyncio.gather(*(post(index, principals[index].aliases[0])
                                                       for index in range(1, 4)))
                    assert statuses == [200, 200, 200]
                    assert not pending.done()
                    channels[0].release.set()
                    assert await pending == 200
                finally:
                    channels[0].release.set()
                    await asyncio.gather(pending, return_exceptions=True)

                for principal, channel in zip(principals, channels):
                    assert len(channel.calls) == 1
                    assert channel.calls[0].notification.identity == principal.identity
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Расхождение UTC и границы допуска через настоящий HTTP
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("offset,skew,lifetime,ttl,future,reason", [
    (120, 0, 60, 300, 0, "relay_expired"),
    (120, 180, 60, 300, 0, None),
    (-120, 0, 60, 300, 0, "relay_clock_skew"),
    (-120, 180, 60, 300, 0, None),
    (-180, 180, 60, 300, 0, None),
    (-181, 180, 60, 300, 0, "relay_clock_skew"),
    (240, 180, 60, 300, 0, "relay_expired"),
    (120, 180, 600, 5, 0, None),
    (185, 180, 600, 5, 0, "relay_expired"),
    (0, 600, 60, 300, 0, None),
    (-20, 0, 60, 300, 30, None),
    (-35, 10, 60, 300, 30, "relay_clock_skew"),
    (3600, 3600, 60, 300, 0, None),
])
def test_gateway_clock_tolerance(
    notification: Notification,
    schema: int,
    offset: int,
    skew: int,
    lifetime: int,
    ttl: int,
    future: int,
    reason: str | None,
) -> None:

    """Check clock skew, unchanged timestamps and bounded provider work over HTTP.

    :param notification: Fixed synthetic notification.
    :type notification: Notification

    :param schema: Relay response schema.
    :type schema: int

    :param offset: Gateway UTC offset from the sender, in seconds.
    :type offset: int

    :param skew: Explicit two-sided allowance, in seconds.
    :type skew: int

    :param lifetime: Notification lifetime in sender UTC.
    :type lifetime: int

    :param ttl: Server destination lifetime limit.
    :type ttl: int

    :param future: Legacy future-only allowance.
    :type future: int

    :param reason: Expected rejection or None for acceptance.
    :type reason: str | None
    """

    # notification/schema - исходное событие и проверяемая версия протокола.
    # offset/skew/future - фиксированная разница UTC и два независимых допуска.
    # lifetime/ttl - сроки события и серверного назначения.
    # reason - ожидаемый отказ; None означает одну отправку провайдеру.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Один запрос с заданными часами без ожидания реальных минут
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Use a real loopback gateway with deterministic UTC and a fake provider."""

        event = replace(notification, expires_at=notification.created_at + timedelta(seconds=lifetime))
        channel = Channel()
        config = settings(event, channel, clock_skew_tolerance=skew, future_tolerance=future)
        destination = config.destinations[0]
        config = replace(config, destinations=(replace(destination, retry=replace(destination.retry, ttl=ttl)),))
        gateway = Gateway(config, utc_now=lambda: event.created_at + timedelta(seconds=offset))
        request = replace(envelope(event), schema_version=schema)
        await gateway.start(port=0)
        try:
            async with aiohttp.ClientSession(headers={"Authorization": "Bearer " + TOKEN}) as client:
                async with client.post(f"http://127.0.0.1:{gateway.port}/v1/notifications",
                        json=request.to_dict()) as response:
                    assert response.status == 200
                    result = decode_response(await response.json(), request.delivery, schema_version=schema)

            assert result.reason_code == reason
            if reason is None:
                assert result.status is DeliveryStatus.PROVIDER_ACCEPTED
                assert len(channel.calls) == 1
                sent = channel.calls[0]
                assert sent.notification == event
                assert 0 < sent.remaining_timeout <= min(request.timeout, request.remaining_ttl, ttl)
                assert gateway.stats().get("expired", 0) == gateway.stats().get("clock_skew", 0) == 0
            else:
                assert result.status is DeliveryStatus.PERMANENT_FAILURE
                assert not channel.calls
                counter = "clock_skew" if reason == "relay_clock_skew" else "expired"
                assert gateway.stats()[counter] == 1
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Допуск часов не восстанавливает исчерпанное время запроса
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("timeout,remaining_ttl,elapsed", [(1, 10, 2), (10, 1, 2)])
def test_gateway_elapsed_budget(
    notification: Notification,
    timeout: float,
    remaining_ttl: float,
    elapsed: float,
) -> None:

    """Reject spent relative budgets even with the maximum UTC allowance.

    :param notification: Synthetic notification.
    :type notification: Notification

    :param timeout: Request processing budget.
    :type timeout: float

    :param remaining_ttl: Remaining sender lifetime.
    :type remaining_ttl: float

    :param elapsed: Already elapsed server processing time.
    :type elapsed: float
    """

    # notification - событие с фиксированными датами.
    # timeout/remaining_ttl - относительные сроки отправителя.
    # elapsed - время, которое уже потратил сервер на приём запроса.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка вычисления остатка без таймеров и ожиданий
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Model elapsed admission time directly at the dispatch boundary."""

        channel = Channel()
        gateway = Gateway(settings(notification, channel, clock_skew_tolerance=3600),
            utc_now=lambda: notification.created_at)
        # Wire требует timeout <= remaining_ttl. Для второй ветки проверяем
        # защиту непосредственно на границе dispatch с уже истёкшим общим TTL.
        request = replace(envelope(notification), timeout=min(timeout, remaining_ttl), remaining_ttl=remaining_ttl)
        response = await gateway._dispatch(request, asyncio.get_running_loop().time() - elapsed)
        assert decode_response(json.loads(response.body), request.delivery).reason_code == "relay_expired"
        assert not channel.calls and gateway.stats()["expired"] == 1
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.contract.test_gateway не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
