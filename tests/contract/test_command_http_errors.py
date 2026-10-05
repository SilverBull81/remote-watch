# Проверка кодов отказа и повреждённых ответов командного HTTP-сервера.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-200546
#
# Тесты:
# -> test_error_response(): Классификация ограниченного HTTP-ответа без раскрытия тела.
# -> test_error_response_timeout(): Сохранение статуса при зависшем теле ответа.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import traceback
from pathlib import Path

import pytest

from remote_watch.adapters.command_http import HttpsCommandTransport
from remote_watch.commands.http_wire import MAX_HTTP_BYTES
from remote_watch.commands.transport import CommandError

pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402
from test_command_hub import APP_TOKEN, Rig  # noqa: E402


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Классификация ограниченного HTTP-ответа без раскрытия тела
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("status", "body", "headers", "expected"), [
    (400, b'{"code":"invalid"}', {}, "invalid"),
    (403, b'{"code":"denied"}', {}, "denied"),
    (409, b'{"code":"stale_session"}', {}, "stale_session"),
    (409, b'{"code":"conflict"}', {}, "conflict"),
    (409, b'{"code":"already_started"}', {}, "already_started"),
    (409, b'{"code":"outcome_conflict"}', {}, "outcome_conflict"),
    (409, b'{"code":"expired"}', {}, "expired"),
    (429, b'{"code":"busy"}', {}, "busy"),
    (503, b'{"code":"capacity"}', {}, "capacity"),
    (503, b'{"code":"closed"}', {}, "closed"),
    (503, b'{"code":"unavailable"}', {}, "unavailable"),
    (401, b"PRIVATE", {}, "denied"),
    (502, b"PRIVATE", {"Content-Type": "text/html"}, "unavailable"),
    (409, b'{"code":"PRIVATE"}', {}, "conflict"),
    (503, b'{"code":"stale_session"}', {}, "unavailable"),
    (409, b'{"code":"capacity"}', {}, "conflict"),
    (409, b'{"code":"stale_session","code":"stale_session"}', {}, "conflict"),
    (409, b'{"code":"stale_session","detail":"PRIVATE"}', {}, "conflict"),
    (409, b'{"code":["PRIVATE"]}', {}, "conflict"),
    (409, b'[]', {}, "conflict"),
    (409, b'null', {}, "conflict"),
    (409, b'{"code":NaN}', {}, "conflict"),
    (409, b'\xffPRIVATE', {}, "conflict"),
    (409, '{"code":"stale_session"}'.encode("utf-16"), {}, "conflict"),
    (409, b'{"code":"stale_session"}' + b" " * 257, {}, "conflict"),
    (409, b'{"code":"stale_session"}', {"Content-Type": "text/plain"}, "conflict"),
    (409, b'{"code":"stale_session"}', {"Content-Encoding": "gzip"}, "conflict"),
    (302, b'{"code":"stale_session"}', {"Location": "/PRIVATE"}, "unavailable"),
    (200, b"PRIVATE", {}, "invalid"),
    (200, b" " * (MAX_HTTP_BYTES + 1), {}, "invalid"),
    (200, b"PRIVATE", {"Content-Type": "text/plain"}, "invalid"),
    (200, b"PRIVATE", {"Content-Encoding": "gzip"}, "invalid"),
], ids=["invalid", "denied", "stale", "conflict", "started", "outcome", "expired", "busy",
        "capacity", "closed", "unavailable", "unauthorized", "proxy", "unknown", "mismatch503",
        "mismatch409", "duplicate", "extra", "list-code", "array", "null", "nan", "encoding",
        "utf16", "oversized-error", "plain-error", "compressed-error", "redirect",
        "malformed-success", "oversized-success", "plain-success", "compressed-success"])
def test_error_response(
    tmp_path: Path,
    status: int,
    body: bytes,
    headers: dict[str, str],
    expected: str,
) -> None:

    """Exercise real loopback HTTP with valid errors and adversarial response bodies.

    :param tmp_path: Isolated directory for synthetic registration settings.
    :type tmp_path: Path

    :param status: HTTP response status emitted by the test server.
    :type status: int

    :param body: Synthetic response body without real credentials.
    :type body: bytes

    :param headers: Overrides for response metadata.
    :type headers: dict[str, str]

    :param expected: Safe public error code expected at the client boundary.
    :type expected: str
    """

    # tmp_path — изолированная конфигурация; status/body/headers — подставной ответ.
    # expected — фиксированный код, без текста ответа и перенаправления запросов.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Один запрос к подставному серверу и обязательное освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Make one request and retain no credentials or private response text in errors."""

        calls = 0

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Отправка выбранного ответа с подсчётом обращений
        #----------------------------------------------------------------------------------------------------------
        async def reply(request: web.Request) -> web.Response:

            """Return a synthetic response without logging request contents.

            :param request: Local HTTP request, used only to complete body consumption.
            :type request: web.Request

            :return: Selected test response.
            :rtype: web.Response
            """

            # request — только локальное соединение; учёт доказывает отсутствие retry/redirect.
            nonlocal calls
            calls += 1
            await request.read()
            return web.Response(status=status, body=body, headers={"Content-Type": "application/json", **headers})
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", reply)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        transport = HttpsCommandTransport(f"http://127.0.0.1:{port}", APP_TOKEN, allow_loopback_http=True)

        try:
            await transport.open()
            with pytest.raises(CommandError) as caught:
                await transport.exchange("register", Rig(tmp_path).registration)
            assert caught.value.code == expected and caught.value.http_status == status
            rendered = "".join(traceback.format_exception(type(caught.value), caught.value,
                                                         caught.value.__traceback__))
            assert "PRIVATE" not in rendered and APP_TOKEN not in rendered
            assert calls == 1 and transport._active == 0
        finally:
            await transport.close()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение статуса при зависшем теле ответа
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("status", "expected"), [(409, "conflict"), (200, "unavailable")])
def test_error_response_timeout(
    tmp_path: Path,
    status: int,
    expected: str,
) -> None:

    """Bound a stalled body while retaining headers already received.

    :param tmp_path: Isolated synthetic registration directory.
    :type tmp_path: Path

    :param status: Status sent before the body stalls.
    :type status: int

    :param expected: Public fallback code after the bounded read expires.
    :type expected: str
    """

    # tmp_path — временная конфигурация; status/expected — наблюдаемый статус и итоговый код.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Зависание только чтения тела после полученных заголовков
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Keep the server responsive while intentionally withholding body completion."""

        release = asyncio.Event()
        started = asyncio.Event()

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Отправка заголовков и ожидание завершения теста
        #----------------------------------------------------------------------------------------------------------
        async def reply(request: web.Request) -> web.StreamResponse:

            """Hold an incomplete chunked response until test cleanup.

            :param request: Local HTTP request to prepare the response for.
            :type request: web.Request

            :return: Response with headers sent before the client timeout.
            :rtype: web.StreamResponse
            """

            # request — локальное соединение; освобождение сервера не зависит от timeout клиента.
            response = web.StreamResponse(status=status, headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await response.write(b'{"code":')
            started.set()
            await release.wait()
            return response
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_post("/v1/commands/register", reply)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        transport = HttpsCommandTransport(f"http://127.0.0.1:{port}", APP_TOKEN,
                                          allow_loopback_http=True, timeout=0.5)
        try:
            await transport.open()
            with pytest.raises(CommandError) as caught:
                await asyncio.wait_for(transport.exchange("register", Rig(tmp_path).registration), timeout=3)
            assert started.is_set()
            assert caught.value.code == expected and caught.value.http_status == status
            assert transport._active == 0
        finally:
            release.set()
            await transport.close()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_http_errors не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
