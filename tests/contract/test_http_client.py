# Проверка настоящего HTTP-клиента на локальном сервере без внешней сети.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-160026
#
# Функции и тесты:
# -> test_local_http(): Настоящий HTTP-клиент и управляемый локальный сервер.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from remote_watch import Delivery, DeliveryStatus, Notification, RetryPolicy
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
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_http_client не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
