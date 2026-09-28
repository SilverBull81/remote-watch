# Проверки обоих адаптеров с подменённой HTTP-сессией без доступа к сервисам.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-160026
#
# Классы:
# -> FakeResponse: Управляемый HTTP-ответ для тестов.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> iter_chunked(): Чтение тестового ответа по частям.
#    Специальные методы:
#    -> __aenter__(): Начало управляемого тестового запроса.
#    -> __aexit__(): Учёт освобождения тестового ответа.
#
# -> FakeSession: HTTP-сессия, сохраняющая запросы без отправки.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> post(): Один HTTP POST без скрытых повторов.
#    -> close(): Закрытие клиента и освобождение ресурсов.
#
# Функции и тесты:
# -> sessions(): Подмена HTTP-сессии и подготовка тестовых токенов.
# -> make_channel(): Создание адаптера с тестовыми настройками.
# -> accepted_body(): Подтверждение приёма в формате выбранного сервиса.
# -> test_success_and_lifecycle(): Отправка, размер текста и освобождение клиента.
# -> test_http_errors(): Классификация кодов HTTP без утечки тела ответа.
# -> test_invalid_success(): Неполное подтверждение не считается успешной доставкой.
# -> test_interrupted_requests(): Отмена, обрыв, таймаут и ограничение размера ответа.
# -> test_telegram_envelope_retry(): Задержка Telegram и отказ от автоматической смены чата.
# -> test_retry_after_values(): Допустимые и ошибочные значения задержки.
# -> test_endpoint_validation(): Запрет неоднозначных адресов и токенов в настройке URL.
# -> test_token_resolution_and_policy(): Позднее чтение токена и согласованность таймаутов.
# -> test_runtime_with_both_adapters(): Оба адаптера в настоящем рабочем потоке runtime.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from remote_watch import (
    Delivery,
    DeliveryStatus,
    Notification,
    NotificationRuntime,
    RetryPolicy,
    Route,
    WatcherConfig,
)
from remote_watch.adapters._common import retry_after
from remote_watch.adapters.ntfy import NtfyChannel, NtfyConfig
from remote_watch.adapters.telegram import TelegramChannel, TelegramConfig

aiohttp = pytest.importorskip("aiohttp")


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Управляемый HTTP-ответ для тестов
#------------------------------------------------------------------------------------------------------------------
class FakeResponse:
    """Model a bounded streaming response and a controllable interrupted request."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Initialize one configurable HTTP response."""

        self.status = 200
        self.headers: dict[str, str] = {}
        self.body: object = None
        self.raw: bytes | None = None
        self.error: BaseException | None = None
        self.block = False
        self.entered = asyncio.Event()
        self.released = False
        self.content = self
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение тестового ответа по частям
    #--------------------------------------------------------------------------------------------------------------
    async def iter_chunked(
        self,
        size: int,
        ) -> AsyncIterator[bytes]:

        """Yield response bytes or wait for cancellation after headers arrive.

        :param size: Maximum chunk size in bytes.
        :type size: int

        :return: The value described by this operation.
        :rtype: AsyncIterator[bytes]
        """

        # size - размер читаемой части, байт.

        if self.block:
            await asyncio.Event().wait()
        data = self.raw if self.raw is not None else json.dumps(self.body).encode()
        for offset in range(0, len(data), size):
            yield data[offset:offset + size]
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Начало управляемого тестового запроса
    #--------------------------------------------------------------------------------------------------------------
    async def __aenter__(self) -> FakeResponse:

        """Enter a request or reproduce a connection failure.

        :return: The value described by this operation.
        :rtype: FakeResponse
        """

        if self.error:
            raise self.error
        self.entered.set()
        return self
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Учёт освобождения тестового ответа
    #--------------------------------------------------------------------------------------------------------------
    async def __aexit__(
        self,
        *args: object,
        ) -> None:

        """Record response cleanup after success, cancellation or malformed data.

        :param args: Context manager exception details.
        :type args: object
        """

        # args - сведения об исключении при выходе из контекста.

        self.released = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : HTTP-сессия, сохраняющая запросы без отправки
#------------------------------------------------------------------------------------------------------------------
class FakeSession:
    """Capture client settings and outgoing requests without network access."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        **settings: Any,
        ) -> None:

        """Keep the actual connector for checking ownership and cleanup.

        :param settings: Client constructor settings.
        :type settings: Any
        """

        # settings - настройки создаваемого HTTP-клиента.

        self.settings = settings
        self.response = FakeResponse()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Один HTTP POST без скрытых повторов
    #--------------------------------------------------------------------------------------------------------------
    def post(
        self,
        url: str,
        **kwargs: Any,
        ) -> FakeResponse:

        """Record exactly one request without retries or redirects.

        :param url: Fixed request URL.
        :type url: str

        :param kwargs: Captured request or client keyword arguments.
        :type kwargs: Any

        :return: Bounded HTTP reply or sanitized transport failure.
        :rtype: FakeResponse
        """

        # url - заданный адрес запроса.
        # kwargs - именованные параметры запроса или клиента.

        self.calls.append((url, kwargs))
        return self.response
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие клиента и освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close the owned connector once the channel finishes."""

        await self.settings["connector"].close()
        self.closed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подмена HTTP-сессии и подготовка тестовых токенов
#------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[FakeSession]:

    """Replace session creation and provide only synthetic test credentials.

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :return: The value described by this operation.
    :rtype: list[FakeSession]
    """

    # monkeypatch - фикстура подмены зависимостей и окружения.

    created: list[FakeSession] = []

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Создание и сохранение тестовой сессии
    #--------------------------------------------------------------------------------------------------------------
    def create(**kwargs: Any) -> FakeSession:

        """Capture one newly created client.

        :param kwargs: Captured request or client keyword arguments.
        :type kwargs: Any

        :return: The value described by this operation.
        :rtype: FakeSession
        """

        # kwargs - именованные параметры запроса или клиента.

        session = FakeSession(**kwargs)
        created.append(session)
        return session
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(aiohttp, "ClientSession", create)
    monkeypatch.setenv("RW_TEST_TELEGRAM", "12345:synthetic_test_token")
    monkeypatch.setenv("RW_TEST_NTFY", "tk_synthetic_test_token")
    return created
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание адаптера с тестовыми настройками
#------------------------------------------------------------------------------------------------------------------
def make_channel(provider: str) -> TelegramChannel | NtfyChannel:

    """Construct one provider with explicit display settings.

    :param provider: Provider selected by the test.
    :type provider: str

    :return: The value described by this operation.
    :rtype: TelegramChannel | NtfyChannel
    """

    # provider - сервис, выбранный для проверки.

    if provider == "telegram":
        return TelegramChannel(TelegramConfig(token_env="RW_TEST_TELEGRAM", chat_id=-123, message_thread_id=9))
    return NtfyChannel(NtfyConfig(
        topic="test-topic", token_env="RW_TEST_NTFY", title="Проверка", tags=("warning",),
    ))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подтверждение приёма в формате выбранного сервиса
#------------------------------------------------------------------------------------------------------------------
def accepted_body(provider: str) -> dict[str, object]:

    """Return the documented success envelope for one provider.

    :param provider: Provider selected by the test.
    :type provider: str

    :return: The value described by this operation.
    :rtype: dict[str, object]
    """

    # provider - сервис, выбранный для проверки.

    if provider == "telegram":
        return {"ok": True, "result": {"message_id": 42}}
    return {"event": "message", "id": "Abc123", "topic": "test-topic"}
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отправка, размер текста и освобождение клиента
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
def test_success_and_lifecycle(
    provider: str,
    notification: Notification,
    sessions: list[FakeSession],
    ) -> None:

    """Verify rendering, limits, authentication, request budgets and owned cleanup.

    :param provider: Provider selected by the test.
    :type provider: str

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param sessions: Captured fake sessions.
    :type sessions: list[FakeSession]
    """

    # provider - сервис, выбранный для проверки.
    # notification - уведомление с заданными тестовыми данными.
    # sessions - сессии, созданные адаптером в проверке.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise one full channel lifetime in its owning loop."""

        channel = make_channel(provider)
        assert not sessions
        with pytest.raises(RuntimeError, match="not open"):
            await channel.send(Delivery(notification=notification, destination_id="phone", delivery_id="d1"))
        await channel.open()
        await channel.open()
        assert len(sessions) == 1
        session = sessions[0]
        session.response.body = accepted_body(provider)
        event = replace(notification, message="😀аб" * 1000 + "<b>& markdown_*", exception="trace text")
        delivery = Delivery(notification=event, destination_id="phone", delivery_id="d1")
        result = await channel.send(delivery)
        assert result.status is DeliveryStatus.PROVIDER_ACCEPTED
        assert session.response.released
        assert len(session.calls) == 1
        url, request = session.calls[0]
        payload = request["json"]
        text = payload["text" if provider == "telegram" else "message"]
        assert "instance_id=one" in text and "event_id=event-1" in text and "delivery_id=d1" in text
        assert text.endswith("[сокращено]")
        assert "parse_mode" not in payload and "attach" not in payload
        assert request["allow_redirects"] is False
        assert session.settings["trust_env"] is False and session.settings["auto_decompress"] is False
        assert session.settings["timeout"].connect == 3 and session.settings["timeout"].total == 10
        assert session.settings["connector"].limit == 1
        if provider == "telegram":
            assert url.endswith("/bot12345:synthetic_test_token/sendMessage")
            assert payload["message_thread_id"] == 9 and payload["chat_id"] == -123
            assert len(text.encode("utf-16-le")) <= 8192
        else:
            assert url == "https://ntfy.sh/"
            assert request["headers"]["Authorization"] == "Bearer tk_synthetic_test_token"
            assert payload["title"] == "Проверка" and payload["topic"] == "test-topic"
            assert len(text.encode()) <= 4096
        assert "synthetic_test_token" not in repr(channel) + repr(channel._config) + repr(result)
        await channel.close()
        await channel.close()
        assert session.closed
        with pytest.raises(RuntimeError, match="closed"):
            await channel.open()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Классификация кодов HTTP без утечки тела ответа
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
@pytest.mark.parametrize("status,expected", [(400, "permanent_failure"), (401, "permanent_failure"),
    (403, "permanent_failure"), (404, "permanent_failure"), (413, "permanent_failure"),
    (307, "permanent_failure"), (429, "rate_limited"), (408, "transient_failure"),
    (500, "transient_failure"), (503, "transient_failure")])
def test_http_errors(
    provider: str,
    status: int,
    expected: str,
    notification: Notification,
    sessions: list[FakeSession],
    ) -> None:

    """Classify HTTP failures without returning their sensitive response bodies.

    :param provider: Provider selected by the test.
    :type provider: str

    :param status: HTTP response status.
    :type status: int

    :param expected: Expected classification or delay.
    :type expected: str

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param sessions: Captured fake sessions.
    :type sessions: list[FakeSession]
    """

    # provider - сервис, выбранный для проверки.
    # status - код HTTP-ответа.
    # expected - ожидаемый результат проверки.
    # notification - уведомление с заданными тестовыми данными.
    # sessions - сессии, созданные адаптером в проверке.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Send one rejected request and verify the fixed result code."""

        channel = make_channel(provider)
        await channel.open()
        try:
            response = sessions[0].response
            response.status = status
            response.headers["Retry-After"] = "17"
            response.body = {"description": "secret payload", "error": "synthetic_test_token"}
            delivery = Delivery(notification=notification, destination_id="phone", delivery_id="d1")
            result = await channel.send(delivery)
            assert result.status.value == expected
            assert "secret" not in repr(result) and "synthetic" not in repr(result)
            assert result.retry_after == (17 if expected in ("rate_limited", "transient_failure") else None)
            assert len(sessions[0].calls) == 1
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Неполное подтверждение не считается успешной доставкой
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
@pytest.mark.parametrize("body", [None, [], {"ok": True}, {"ok": 1, "result": {"message_id": True}},
    {"event": "message", "id": "secret\nvalue", "topic": "test-topic"},
    {"event": "message", "id": "abc", "topic": "wrong-topic"}])
def test_invalid_success(
    provider: str,
    body: object,
    notification: Notification,
    sessions: list[FakeSession],
    ) -> None:

    """Treat ambiguous success as unknown instead of claiming delivery.

    :param provider: Provider selected by the test.
    :type provider: str

    :param body: Test response body.
    :type body: object

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param sessions: Captured fake sessions.
    :type sessions: list[FakeSession]
    """

    # provider - сервис, выбранный для проверки.
    # body - тестовое тело ответа.
    # notification - уведомление с заданными тестовыми данными.
    # sessions - сессии, созданные адаптером в проверке.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Read one malformed acknowledgement."""

        channel = make_channel(provider)
        await channel.open()
        try:
            sessions[0].response.body = body
            delivery = Delivery(notification=notification, destination_id="phone", delivery_id="d1")
            result = await channel.send(delivery)
            assert result.status is DeliveryStatus.UNKNOWN
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отмена, обрыв, таймаут и ограничение размера ответа
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
@pytest.mark.parametrize("mode", ["cancel", "timeout", "disconnect", "oversize", "invalid_json"])
def test_interrupted_requests(
    provider: str,
    mode: str,
    notification: Notification,
    sessions: list[FakeSession],
    ) -> None:

    """Verify unknown outcomes, response bounds and propagation of cancellation.

    :param provider: Provider selected by the test.
    :type provider: str

    :param mode: Selected failure or success scenario.
    :type mode: str

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param sessions: Captured fake sessions.
    :type sessions: list[FakeSession]
    """

    # provider - сервис, выбранный для проверки.
    # mode - выбранный сценарий ответа сервера.
    # notification - уведомление с заданными тестовыми данными.
    # sessions - сессии, созданные адаптером в проверке.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Interrupt an attempt and still release owned resources."""

        channel = make_channel(provider)
        await channel.open()
        try:
            response = sessions[0].response
            response.block = mode == "cancel"
            if mode == "timeout":
                response.error = asyncio.TimeoutError("secret token")
            if mode == "disconnect":
                response.error = aiohttp.ServerDisconnectedError("secret token")
            if mode == "oversize":
                response.raw = b"x" * 65537
            if mode == "invalid_json":
                response.raw = b"not json\xff"
            delivery = Delivery(notification=notification, destination_id="phone", delivery_id="d1")
            task = asyncio.create_task(channel.send(delivery))
            if mode == "cancel":
                await asyncio.wait_for(response.entered.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert response.released
            else:
                result = await task
                assert result.status is DeliveryStatus.UNKNOWN
                assert "secret" not in repr(result)
            assert len(sessions[0].calls) == 1
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Задержка Telegram и отказ от автоматической смены чата
#------------------------------------------------------------------------------------------------------------------
def test_telegram_envelope_retry(
    notification: Notification,
    sessions: list[FakeSession],
    ) -> None:

    """Respect Telegram parameters and refuse automatic chat migration.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param sessions: Captured fake sessions.
    :type sessions: list[FakeSession]
    """

    # notification - уведомление с заданными тестовыми данными.
    # sessions - сессии, созданные адаптером в проверке.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Check a logical API error even when HTTP itself succeeds."""

        channel = make_channel("telegram")
        await channel.open()
        try:
            response = sessions[0].response
            response.headers["Retry-After"] = "10"
            response.body = {"ok": False, "error_code": 429, "parameters": {"retry_after": 30}}
            delivery = Delivery(notification=notification, destination_id="phone", delivery_id="d1")
            result = await channel.send(delivery)
            assert result.status is DeliveryStatus.RATE_LIMITED and result.retry_after == 30
            response.body = {"ok": False, "error_code": 400, "parameters": {"migrate_to_chat_id": -99}}
            assert (await channel.send(delivery)).status is DeliveryStatus.PERMANENT_FAILURE
            assert all(call[1]["json"]["chat_id"] == -123 for call in sessions[0].calls)
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Допустимые и ошибочные значения задержки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [(0, 0), (12, 12), ("1.5", 1.5), ("-1", None),
    ("NaN", None), (float("inf"), None), (True, None), ("invalid", None), (10**1000, None)])
def test_retry_after_values(
    value: object,
    expected: float | None,
    ) -> None:

    """Parse delay fields without accepting nonfinite values or booleans.

    :param value: Untrusted delay value.
    :type value: object

    :param expected: Expected classification or delay.
    :type expected: float | None
    """

    # value - значение задержки из ответа.
    # expected - ожидаемый результат проверки.

    assert retry_after(value) == expected
    assert retry_after("Mon, 28 Sep 2026 12:00:10 GMT", datetime(2026, 9, 28, 12, tzinfo=timezone.utc)) == 10
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет неоднозначных адресов и токенов в настройке URL
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("endpoint", [
    "http://example.com", "https://user:secret@example.com", "https://example.com/?key=x",
    "https://example.com/#fragment", "https://example.com/../other", "https://example.com/%2e",
    "https://x:bad", "https://x/\n",
])
def test_endpoint_validation(endpoint: str) -> None:

    """Reject unsafe endpoints without echoing their contents.

    :param endpoint: Configured provider base URL.
    :type endpoint: str
    """

    # endpoint - адрес сервера из настроек.

    with pytest.raises(ValueError) as caught:
        NtfyConfig(topic="test", token_env=None, endpoint=endpoint)
    assert endpoint not in str(caught.value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Позднее чтение токена и согласованность таймаутов
#------------------------------------------------------------------------------------------------------------------
def test_token_resolution_and_policy(
    monkeypatch: pytest.MonkeyPatch,
    sessions: list[FakeSession],
    ) -> None:

    """Resolve secrets at open and use identical timeout policies on both boundaries.

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param sessions: Captured fake sessions.
    :type sessions: list[FakeSession]
    """

    # monkeypatch - фикстура подмены зависимостей и окружения.
    # sessions - сессии, созданные адаптером в проверке.

    config = TelegramConfig(token_env="RW_MISSING_TOKEN", chat_id="@test_channel")
    policy = RetryPolicy(connect_timeout=1, attempt_timeout=2)
    destination = config.destination("phone", retry=policy, outstanding_capacity=7)
    assert destination.retry is policy and destination.outstanding_capacity == 7
    channel = destination.channel_factory()
    assert channel._http._policy is policy
    assert not sessions

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Retry opening only after the application supplies its missing credential."""

        with pytest.raises(ValueError, match="missing or invalid"):
            await channel.open()
        assert not sessions
        monkeypatch.setenv("RW_MISSING_TOKEN", "1:fake")
        await channel.open()
        await channel.close()
        anonymous = NtfyChannel(NtfyConfig(topic="test", token_env=None))
        await anonymous.open()
        assert anonymous._token is None
        await anonymous.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Оба адаптера в настоящем рабочем потоке runtime
#------------------------------------------------------------------------------------------------------------------
def test_runtime_with_both_adapters(
    notification: Notification,
    sessions: list[FakeSession],
    ) -> None:

    """Exercise logger routing and two managed adapters in the actual worker thread.

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param sessions: Captured fake sessions.
    :type sessions: list[FakeSession]
    """

    # notification - уведомление с заданными тестовыми данными.
    # sessions - сессии, созданные адаптером в проверке.

    telegram = TelegramConfig(token_env="RW_TEST_TELEGRAM", chat_id=-123).destination("telegram")
    ntfy = NtfyConfig(topic="test-topic", token_env="RW_TEST_NTFY").destination("ntfy")
    runtime = NotificationRuntime(WatcherConfig(identity=notification.identity,
        destinations=(telegram, ntfy), routes=(Route(destination_ids=("telegram", "ntfy")),)))
    logger = logging.Logger("adapter-test", level=logging.ERROR)
    logger.addHandler(runtime.handler)
    try:
        with runtime:
            sessions[0].response.body = accepted_body("telegram")
            sessions[1].response.body = accepted_body("ntfy")
            logger.error("Проверка двух сервисов")
        assert runtime.stats().accepted == 2
        assert all(session.closed for session in sessions)
    finally:
        logger.removeHandler(runtime.handler)
        runtime.handler.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_adapters не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
