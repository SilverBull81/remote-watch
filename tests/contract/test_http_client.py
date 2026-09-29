# Проверка настоящего HTTP-клиента на локальном сервере без внешней сети.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260929-192353
#
# Функции и тесты:
# -> test_local_http(): Настоящий HTTP-клиент и управляемый локальный сервер.
# -> test_ntfy_wire_limits(): Размер фактически отправленного JSON и текста ntfy.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace

import pytest

from remote_watch import Delivery, DeliveryStatus, Notification, RetryPolicy
from remote_watch.adapters._common import render
from remote_watch.adapters.ntfy import NtfyChannel, NtfyConfig
from remote_watch.adapters.telegram import TelegramChannel, TelegramConfig

pytest.importorskip("aiohttp")


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящий HTTP-клиент и управляемый локальный сервер
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
@pytest.mark.parametrize("mode", ["success", "redirect", "disconnect", "timeout", "cancel", "oversize"])
def test_local_http(
    provider: str,
    mode: str,
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    ) -> None:

    """Verify the real session, cancellation, logging and absence of hidden POST retries.

    :param provider: Provider selected by the test.
    :type provider: str

    :param mode: Selected failure or success scenario.
    :type mode: str

    :param notification: Immutable notification fixture.
    :type notification: Notification

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param caplog: Pytest log capture fixture.
    :type caplog: pytest.LogCaptureFixture
    """

    # provider - сервис, выбранный для проверки.
    # mode - выбранный сценарий ответа сервера.
    # notification - уведомление с заданными тестовыми данными.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # caplog - перехват журнала для проверки отсутствия токенов.

    monkeypatch.setenv("RW_LOCAL_TOKEN", "123:synthetic" if provider == "telegram" else "tk_synthetic")
    caplog.set_level(logging.DEBUG)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Serve a controlled response entirely on the local loopback interface."""

        received = asyncio.Event()
        release = asyncio.Event()
        workers: set[asyncio.Task[None]] = set()
        requests: list[bytes] = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Ответ локального сервера в выбранном режиме
        #----------------------------------------------------------------------------------------------------------
        async def respond(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            ) -> None:

            """Consume one POST and reproduce a selected server behavior.

            :param reader: Incoming connection stream.
            :type reader: asyncio.StreamReader

            :param writer: Outgoing connection stream.
            :type writer: asyncio.StreamWriter
            """

            # reader - поток чтения входящего соединения.
            # writer - поток записи ответа клиенту.

            task = asyncio.current_task()
            assert task is not None
            workers.add(task)
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:"))
                await reader.readexactly(length)
                requests.append(headers)
                received.set()
                if mode in ("timeout", "cancel"):
                    await release.wait()
                    return
                if mode == "disconnect":
                    return
                body = ({"ok": True, "result": {"message_id": 42}} if provider == "telegram" else
                    {"event": "message", "topic": "test", "id": "Abc123"})
                content = json.dumps(body).encode() if mode != "oversize" else b"x" * 70000
                status = b"307 Temporary Redirect" if mode == "redirect" else b"200 OK"
                reply = (b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
                    b"Location: http://127.0.0.1:1/forbidden\r\nConnection: close\r\nContent-Length: "
                    + str(len(content)).encode() + b"\r\n\r\n" + content)
                writer.write(reply)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                workers.discard(task)
        #----------------------------------------------------------------------------------------------------------

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        policy = RetryPolicy(connect_timeout=0.2, attempt_timeout=0.5)
        channel = (TelegramChannel(TelegramConfig(token_env="RW_LOCAL_TOKEN", chat_id=123,
            endpoint=endpoint, allow_http=True), retry=policy) if provider == "telegram" else
            NtfyChannel(NtfyConfig(topic="test", token_env="RW_LOCAL_TOKEN",
                endpoint=endpoint, allow_http=True), retry=policy))
        try:
            await channel.open()
            assert not requests
            client = channel._http._client
            task = asyncio.create_task(channel.send(Delivery(notification=notification,
                destination_id="phone", delivery_id="local")))
            await asyncio.wait_for(received.wait(), 2)
            if mode == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await asyncio.wait_for(task, 2)
                expected = (DeliveryStatus.PROVIDER_ACCEPTED if mode == "success" else
                    DeliveryStatus.PERMANENT_FAILURE if mode == "redirect" else DeliveryStatus.UNKNOWN)
                assert result.status is expected
            assert len(requests) == 1
            assert requests[0].startswith(b"POST ")
        finally:
            await channel.close()
            release.set()
            server.close()
            await server.wait_closed()
            if workers:
                await asyncio.wait_for(asyncio.gather(*workers), 2)
        assert client.closed
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
    assert "synthetic" not in caplog.text
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Размер фактически отправленного JSON и текста ntfy
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("message", "rendered_size"), [
    ("Короткий текст 📡", None), ("Длинный текст 📡 " * 220, None), ('\x00"\\\n' * 1100, None),
    *[(pattern, size) for pattern in ("x", "Я📡") for size in (4095, 4096, 4097, 5000)],
], ids=["short", "unicode", "escaped",
        *[f"{alphabet}-{size}" for alphabet in ("ascii", "unicode") for size in (4095, 4096, 4097, 5000)]])
@pytest.mark.parametrize("large_metadata", [False, True])
def test_ntfy_wire_limits(
    notification: Notification,
    message: str,
    rendered_size: int | None,
    large_metadata: bool,
    ) -> None:

    """Exercise actual aiohttp serialization against ntfy's two size limits.

    :param notification: Synthetic source notification.
    :type notification: Notification

    :param message: Text exercising UTF-8 or JSON escaping.
    :type message: str

    :param rendered_size: Exact UTF-8 size including the rendered header, if specified.
    :type rendered_size: int | None

    :param large_metadata: Whether metadata consumes most of the request budget.
    :type large_metadata: bool
    """

    # notification - исходное уведомление с вымышленной принадлежностью.
    # message - короткий текст либо текст, требующий усечения.
    # rendered_size - точный размер вместе с шапкой; None оставляет заданный текст.
    # large_metadata - большой допустимый набор меток и заголовок.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка настоящих байтов запроса на локальном сервере
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Reject oversized wire requests before parsing their JSON contents."""

        from aiohttp import web

        requests: list[bytes] = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Воспроизведение ограничений JSON publish API
        #----------------------------------------------------------------------------------------------------------
        async def publish(request: web.Request) -> web.Response:

            """Apply the provider limits to raw bytes, then to the decoded message.

            :param request: Incoming loopback request.
            :type request: web.Request

            :return: Provider-shaped acceptance or size rejection.
            :rtype: web.Response
            """

            # request - локальный запрос без реальных credentials.

            raw = await request.read()
            requests.append(raw)
            if len(raw) > 8192:
                return web.json_response({"code": 41301}, status=413)

            payload = json.loads(raw)
            # Оба полевых отчёта dev5: 4095 байт приняты, ровно 4096 дали 50001.
            # Сервер воспроизводит наблюдённый отказ, чтобы поймать возврат к старой границе.
            if len(payload["message"].encode("utf-8")) >= 4096:
                return web.json_response({"code": 50001}, status=500)
            return web.json_response({"event": "message", "topic": payload["topic"], "id": "synthetic"})
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_post("/", publish)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        server = await asyncio.get_running_loop().create_server(runner.server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        # Полные метаданные дополнительно проверяют, что бюджет не сводится к длине message.
        tags = tuple(str(i) + "т" * 127 for i in range(16)) if large_metadata else ("проверка",)
        config = NtfyConfig(topic="test", token_env=None, endpoint=f"http://127.0.0.1:{port}",
            allow_http=True, title="З" * 128 if large_metadata else "Проверка 📡", tags=tags)
        channel = NtfyChannel(config)
        delivery = Delivery(notification=replace(notification, message=message),
            destination_id="phone", delivery_id="wire-size")
        if rendered_size is not None:
            # Размер включает служебную шапку; простое повторение message не проверяет
            # границу 4095/4096. Остаток заполняем ASCII, не разрывая Unicode-символы.
            empty = replace(delivery, notification=replace(notification, message=""))
            budget = rendered_size - len(render(empty).encode("utf-8"))
            repeats, remainder = divmod(budget, len(message.encode("utf-8")))
            text = message * repeats + "." * remainder
            delivery = replace(delivery, notification=replace(notification, message=text))
            assert len(render(delivery).encode("utf-8")) == rendered_size

        original = render(delivery)
        try:
            await channel.open()
            result = await asyncio.wait_for(channel.send(delivery), 3)
            assert result.status is DeliveryStatus.PROVIDER_ACCEPTED
            assert len(requests) == 1
            assert len(requests[0]) <= 8192
            payload = json.loads(requests[0])
            assert payload["title"] == config.title
            assert payload["tags"] == list(tags)
            assert "delivery_id=wire-size" in payload["message"]
            sent = payload["message"]
            assert len(sent.encode("utf-8")) <= 4095
            assert result.message_bytes == len(sent.encode("utf-8"))
            assert result.request_bytes == len(requests[0])
            if len(original.encode("utf-8")) > 4095 or sent != original:
                assert payload["message"].endswith("\n[сокращено]")
                assert original.startswith(sent.removesuffix("\n[сокращено]"))
            else:
                assert sent == original
            if not large_metadata and rendered_size == 4095:
                assert sent == original
        finally:
            await channel.close()
            server.close()
            await server.wait_closed()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_http_client не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
