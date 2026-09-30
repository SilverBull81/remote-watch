# Проверки источника времени через настоящий локальный HTTPS без внешней сети.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260930-220144
#
# Состав модуля:
# -> test_verified_https_time(): Настоящий TLS и недопустимые ответы источника времени.
#
# -> test_time_source_configuration(): Проверка адреса до открытия сетевого клиента.
#


#******************************************************************************************************************
# ИМПОРТ И ОПРЕДЕЛЕНИЯ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import ssl
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
from aiohttp import web
from test_gateway_tls import certificates

from remote_watch.adapters.time_source import HttpsDateTimeSource
from remote_watch.command_time import TimeUnavailable, TrustedClock


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящий TLS и недопустимые ответы источника времени
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["ok", "cached", "redirect", "bad_date", "duplicate_date", "untrusted",
                                "wrong_name", "expired", "timeout", "disabled_verification"])
def test_verified_https_time(
    tmp_path: Path,
    case: str,
) -> None:

    """Use real local TLS and reject cached, redirected, malformed or unauthenticated time.

    :param tmp_path: Pytest temporary directory.
    :type tmp_path: Path

    :param case: Selected success or failure scenario.
    :type case: str
    """

    # tmp_path — временный каталог теста.
    # case — выбранный тестовый сценарий.

    ca, cert, key = certificates(tmp_path, case)
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(cert, key)
    client_ssl = ssl.create_default_context(cafile=str(ca)) if case != "untrusted" else ssl.create_default_context()

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Локальный TLS-сценарий проверки источника времени
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Serve a controlled Date header over a temporary trusted certificate."""

        seen = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной HTTP-ответ времени
        #----------------------------------------------------------------------------------------------------------
        async def handler(request: web.Request) -> web.Response:

            """Return the selected protocol failure without forwarding to another server.

            :param request: Validated command intent, or None for a rejected source event.
            :type request: web.Request

            :return: Return the selected protocol failure without forwarding to another server.
            :rtype: web.Response
            """

            # request — команда либо отсутствие принятой команды.

            seen.append(request.query["remote_watch_nonce"])
            assert request.method == "HEAD"
            assert "no-cache" in request.headers["Cache-Control"]
            headers = {"Date": "Wed, 30 Sep 2026 12:00:00 GMT"}
            if case == "cached":
                headers["Age"] = "0"
            if case == "redirect":
                headers["Location"] = "https://private.invalid/secret"
            if case == "bad_date":
                headers["Date"] = "private-malformed-date"
            if case == "timeout":
                await asyncio.sleep(0.15)
            response = web.Response(status=302 if case == "redirect" else 204, headers=headers)
            if case == "duplicate_date":
                response.headers.add("Date", headers["Date"])
            return response
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_head("/time", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        source = HttpsDateTimeSource(f"https://127.0.0.1:{port}/time", accuracy=0.5,
                                     timeout=0.05 if case == "timeout" else 5, ssl_context=client_ssl)
        if case == "disabled_verification":
            client_ssl.check_hostname = False
            client_ssl.verify_mode = ssl.CERT_NONE
        try:
            if case == "ok":
                first = await source.sample()
                clock = TrustedClock()
                clock.install(first)
                second = await source.sample()
                assert 2 <= second.upper_utc - second.lower_utc < 5
                assert len(seen) == 2 and seen[0] != seen[1]
                assert clock.check(1790769600, "a" * 32).reason is None
            else:
                with pytest.raises(TimeUnavailable) as caught:
                    await source.sample()
                assert str(caught.value) == "trusted time unavailable"
                assert len(seen) <= 1
        finally:
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка адреса до открытия сетевого клиента
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["http://host/time", "https://user:pass@host/time", "https://host/time#x", "https://host/time?q=secret"])
def test_time_source_configuration(url: str) -> None:

    """Reject unsafe endpoints before creating a network client.

    :param url: Explicit trusted HTTPS origin without credentials or query.
    :type url: str
    """

    # url — адрес выбранного доверенного сервера.

    with pytest.raises(ValueError):
        HttpsDateTimeSource(url, accuracy=1)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.contract.test_time_source не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
