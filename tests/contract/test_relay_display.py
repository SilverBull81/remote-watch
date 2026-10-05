# Клиентское отображение через работающий gateway без изменения его конфигурации.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-221259
#
# Тесты:
# -> test_gateway_display_override(): Разные режимы клиентов при одном неизменном provider-канале.
# -> test_display_old_gateway(): Несовместимый gateway не вызывает скрытого повторного POST.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from remote_watch import Delivery, DeliveryStatus, Destination, Notification, RetryPolicy
from remote_watch.adapters._common import render
from remote_watch.adapters.ntfy import NtfyChannel, NtfyConfig
from remote_watch.adapters.relay import RelayChannel, RelayConfig
from remote_watch.adapters.telegram import TelegramChannel, TelegramConfig
from remote_watch.gateway.config import GatewayConfig, GatewayPrincipal
from remote_watch.gateway.server import Gateway


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Разные режимы клиентов при одном неизменном provider-канале
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
def test_gateway_display_override(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:

    """Exercise real relay HTTP and provider rendering while retaining one gateway lifecycle.

    :param notification: Immutable event fixture with synthetic identity.
    :type notification: Notification

    :param monkeypatch: Replace only the provider's outbound HTTP attempt.
    :type monkeypatch: pytest.MonkeyPatch

    :param provider: Concrete notification adapter hosted by the gateway.
    :type provider: str
    """

    # Сеть используется только на loopback. Provider token и service token вымышлены.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Один gateway и последовательные запуски клиентов с разным отображением
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Keep gateway configuration and provider instance unchanged between client restarts."""

        now = datetime.now(timezone.utc)
        event = replace(notification, created_at=now, expires_at=now + timedelta(seconds=60))
        delivery = Delivery(notification=event, destination_id="local", delivery_id="display-test")
        settings = {"display_mode": "compact", "display_fields": ("identity", "time")}
        config = (TelegramConfig(token="123456:" + "A" * 35, chat_id=123, **settings) if provider == "telegram"
                  else NtfyConfig(topic="synthetic", **settings))
        channel = TelegramChannel(config) if provider == "telegram" else NtfyChannel(config)
        payloads: list[dict[str, Any]] = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Перехват итогового запроса к провайдеру
        #----------------------------------------------------------------------------------------------------------
        async def post(
            url: str,
            payload: dict[str, Any],
            token: str | None = None,
        ) -> tuple[int, dict[str, str | None], dict[str, Any]]:

            """Capture concrete provider output without external network calls.

            :param url: Configured provider endpoint.
            :type url: str

            :param payload: Rendered provider-specific request.
            :type payload: dict[str, Any]

            :param token: Synthetic optional provider credential.
            :type token: str | None

            :return: Fixed successful provider response.
            :rtype: tuple[int, dict[str, str | None], dict[str, Any]]
            """

            # url/token принадлежат серверу; запрос клиента не может заменить их.
            assert url.startswith("https://")
            payloads.append(payload)
            body = ({"ok": True, "result": {"message_id": 1}} if provider == "telegram"
                    else {"id": "receipt", "event": "message", "topic": "synthetic"})
            return 200, {"retry-after": None}, body
        #----------------------------------------------------------------------------------------------------------

        monkeypatch.setattr(channel._http, "post", post)
        gateway = Gateway(GatewayConfig(
            destinations=(Destination(destination_id="phone", channel_factory=lambda: channel,
                                      retry=RetryPolicy(max_attempts=1)),),
            principals=(GatewayPrincipal(name="application", token="x" * 32, identity=event.identity,
                                         aliases=("phone",), min_interval=0),), destination_interval=0))
        await gateway.start(port=0)
        base = {"endpoint": f"http://127.0.0.1:{gateway.port}", "alias": "phone", "token": "x" * 32,
                "allow_http": True}
        try:
            for changes in ({}, {"schema_version": 2}, {"schema_version": 3},
                            {"display_mode": "full"}, {"display_mode": "compact"}, {"display_mode": "text"},
                            {"display_mode": "full", "display_fields": ["level"]}, {}):
                client = RelayChannel(RelayConfig(**base, **changes))
                try:
                    await client.open()
                    result = await client.send(delivery)
                    assert result.status is DeliveryStatus.PROVIDER_ACCEPTED, (changes, result)
                    expected_mode = changes.get("display_mode", config.display_mode)
                    expected_fields = (changes.get("display_fields") if "display_mode" in changes
                                       else config.display_fields)
                    assert payloads[-1]["text" if provider == "telegram" else "message"] == render(
                        delivery, expected_mode, expected_fields)
                    assert payloads[-1]["chat_id" if provider == "telegram" else "topic"] == (
                        123 if provider == "telegram" else "synthetic")
                finally:
                    await client.close()
            assert len(payloads) == 8 and config.display_fields == ("identity", "time")
            assert delivery.display_mode is None and delivery.notification is event

            # Клиентский text не меняет проверку Identity/alias до provider.send.
            client = RelayChannel(RelayConfig(**base, display_mode="text"))
            try:
                await client.open()
                foreign = replace(delivery, notification=replace(event,
                                  identity=replace(event.identity, host="other")))
                assert (await client.send(foreign)).reason_code == "relay_auth_denied"
            finally:
                await client.close()
            client = RelayChannel(RelayConfig(**{**base, "alias": "other"}, display_mode="text"))
            try:
                await client.open()
                assert (await client.send(delivery)).reason_code == "relay_auth_denied"
            finally:
                await client.close()
            assert len(payloads) == 8
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Несовместимый gateway не вызывает скрытого повторного POST
#------------------------------------------------------------------------------------------------------------------
def test_display_old_gateway(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Reject an unsupported schema without downgrading or replaying the request.

    :param notification: Immutable synthetic event.
    :type notification: Notification

    :param monkeypatch: Replace the gateway response with an old-schema rejection.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # Старый сервер возвращает отказ; повтор с прежней схемой мог бы скрыть настройку пользователя.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка одной попытки без автоматического понижения схемы
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Count requests without contacting a real gateway."""

        now = datetime.now(timezone.utc)
        event = replace(notification, created_at=now, expires_at=now + timedelta(seconds=60))
        client = RelayChannel(RelayConfig(endpoint="https://gateway.invalid", alias="phone",
                                          token="x" * 32, display_mode="text"))
        calls = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Ответ старого сервера на новую схему
        #----------------------------------------------------------------------------------------------------------
        async def post(
            url: str,
            payload: dict[str, Any],
            token: str | None,
        ) -> tuple[int, dict[str, str | None], dict[str, Any]]:

            """Return the existing unsupported-request status.

            :param url: Synthetic endpoint.
            :type url: str

            :param payload: New-schema request.
            :type payload: dict[str, Any]

            :param token: Synthetic credential.
            :type token: str | None

            :return: Rejection without a provider attempt.
            :rtype: tuple[int, dict[str, str | None], dict[str, Any]]
            """

            # Содержимое ответа не раскрывает подробностей сервера.
            calls.append(payload)
            return 400, {"retry-after": None}, {"code": "relay_invalid_request"}
        #----------------------------------------------------------------------------------------------------------

        monkeypatch.setattr(client._http, "post", post)
        try:
            await client.open()
            result = await client.send(Delivery(notification=event, destination_id="local", delivery_id="one"))
            assert result.status is DeliveryStatus.PERMANENT_FAILURE
            assert len(calls) == 1 and calls[0]["schema_version"] == 3
        finally:
            await client.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_relay_display не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
