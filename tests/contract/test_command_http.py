# Обмен командами через настоящий локальный TLS и проверки сетевых ограничений.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-165638
#
# Тесты:
# -> test_command_https_exchange(): Реальный TLS до изменения подставного состояния.
# -> test_command_http_rejection(): Безопасные отказы некорректным HTTP-запросам.
# -> test_command_transport_endpoint(): Запрет небезопасных или неоднозначных адресов.
# -> test_command_offer_wire(): Проверка внешней упаковки предложения команды.
# -> test_command_http_capacity(): Сохранение места для heartbeat при занятом long poll.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import ssl
from dataclasses import replace
from pathlib import Path

import pytest

from remote_watch.adapters.command_http import HttpsCommandTransport
from remote_watch.commands.client import CommandClient
from remote_watch.commands.http_wire import decode_response, encode_response
from remote_watch.commands.protocol import (
    CommandClaim,
    CommandOutcome,
    CommandReason,
    CommandResult,
    callback_result,
    encode_command,
    message_digest,
)
from remote_watch.commands.transport import CommandError, CommandOffer
from remote_watch.gateway.command_server import CommandHubServer

aiohttp = pytest.importorskip("aiohttp")
from test_command_hub import APP_TOKEN, SOURCE_TOKEN, Rig  # noqa: E402
from test_gateway_tls import certificates  # noqa: E402


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Реальный TLS до изменения подставного состояния
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["trusted", "untrusted", "expired", "wrong_name"])
def test_command_https_exchange(
    tmp_path: Path,
    mode: str,
) -> None:

    """Verify real TLS before fake callback execution through both durable journals.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param mode: Selected TLS certificate verification scenario.
    :type mode: str
    """

    # tmp_path — отдельный временный каталог теста.
    # mode — сценарий проверки настоящего TLS-сертификата.

    ca, cert, key = certificates(tmp_path, mode)
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(cert, key)
    client_ssl = ssl.create_default_context(cafile=None if mode == "untrusted" else str(ca))


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Run an isolated HTTPS hub with synthetic application state."""

        rig = Rig(tmp_path)
        server = CommandHubServer(rig.hub)
        await server.start(ssl_context=server_ssl)
        transport = HttpsCommandTransport(f"https://127.0.0.1:{server.port}", APP_TOKEN, ssl_context=client_ssl)
        client = CommandClient(rig.registration, transport, rig.local, clock=lambda: rig.now)

        try:
            if mode != "trusted":
                with pytest.raises(CommandError, match="unavailable"):
                    await client.start()
                assert rig.store.pending() == ()
                return
            await client.start()
            await rig.submit(rig.request())
            ticket = await client.acquire()
            assert ticket is not None and client.begin(ticket)
            state = {"resumed": False}


            #------------------------------------------------------------------------------------------------------
            # ФУНКЦИЯ : Изменение только подставного состояния приложения
            #------------------------------------------------------------------------------------------------------
            def callback() -> str:

                """Change only local synthetic application state.

                :return: Short confirmation of the synthetic application state change.
                :rtype: str
                """

                state["resumed"] = True
                return "resumed"
            #------------------------------------------------------------------------------------------------------


            result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, callback())
            await client.complete(ticket, result)
            assert state["resumed"] and (await rig.hub.results(SOURCE_TOKEN))[0].record.result == result
            assert await client.acquire() is None

            # Сохраняем UNKNOWN при ещё работающем обработчике и проверяем отдельный
            # HTTPS release. Поддельный claim не должен снимать блокировку журнала.
            request = rig.request(2)
            await rig.submit(request)
            ticket = await client.acquire()
            assert ticket is not None and client.begin(ticket)
            unknown = CommandResult(ref=request.ref, claim_id=ticket.grant.claim_id,
                                    outcome=CommandOutcome.UNKNOWN, reason=CommandReason.TIMEOUT)
            await client.complete(ticket, unknown, execution_finished=False)
            assert rig.store.get(request.ref).execution_active
            wrong = CommandClaim(ref=request.ref, claim_id="f" * 32, request_digest=message_digest(request))
            with pytest.raises(CommandError, match="conflict"):
                await transport.exchange("release", wrong)
            assert rig.store.get(request.ref).execution_active
            await client.release(ticket)
            assert not rig.store.get(request.ref).execution_active
            assert rig.store.get(request.ref).record.result == unknown
            assert rig.local.get(request.ref).acknowledged
        finally:
            await client.close()
            await server.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасные отказы некорректным HTTP-запросам
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["auth", "source", "duplicate_auth", "size", "content", "expect", "wrong_kind"])
def test_command_http_rejection(
    tmp_path: Path,
    case: str,
) -> None:

    """Reject malformed requests without registering or admitting a command.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param case: Selected malformed request or response scenario.
    :type case: str
    """

    # tmp_path — отдельный временный каталог теста.
    # case — выбранный сценарий нарушения формата или границ запроса.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Send one bounded adversarial request over explicit test-only loopback HTTP."""

        rig = Rig(tmp_path)
        server = CommandHubServer(rig.hub)
        await server.start(allow_loopback_http=True)
        headers = [("Authorization", "Bearer " + APP_TOKEN), ("Content-Type", "application/json")]
        body = encode_command(rig.registration)
        expected = 400

        if case == "auth":
            headers[0] = ("Authorization", "Bearer wrong")
            expected = 403
        elif case == "source":
            headers[0] = ("Authorization", "Bearer " + SOURCE_TOKEN)
            expected = 403
        elif case == "duplicate_auth":
            headers.append(headers[0])
            expected = 403
        elif case == "size":
            body = b"x" * 70001
        elif case == "content":
            headers[1] = ("Content-Type", "text/plain")
        elif case == "expect":
            headers.append(("Expect", "100-continue"))
            expected = 417

        try:
            path = "claim" if case == "wrong_kind" else "register"
            async with aiohttp.ClientSession() as client:
                async with client.post(f"http://127.0.0.1:{server.port}/v1/commands/{path}",
                                       data=body, headers=headers) as response:
                    assert response.status == expected
                    text = await response.text()
                    assert APP_TOKEN not in text and SOURCE_TOKEN not in text
            with pytest.raises(CommandError, match="stale_session"):
                rig.hub.target(SOURCE_TOKEN, rig.identity)
        finally:
            await server.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет небезопасных или неоднозначных адресов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("endpoint", ["http://example.com", "http://127.0.0.1", "https://u:p@host",
                                      "https://host/path", "https://host?token=secret", "ftp://127.0.0.1"])
def test_command_transport_endpoint(endpoint: str) -> None:

    """Reject plaintext defaults and endpoint credentials or ambiguous routing paths.

    :param endpoint: HTTPS origin without credentials, query or path.
    :type endpoint: str
    """

    # endpoint — адрес HTTPS без секрета, параметров и пути операции.

    with pytest.raises(ValueError):
        HttpsCommandTransport(endpoint, APP_TOKEN)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка внешней упаковки предложения команды
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["duplicate", "unknown", "nan", "wrong_kind", "oversized", "utf16"])
def test_command_offer_wire(
    tmp_path: Path,
    case: str,
) -> None:

    """Reject malformed outer envelopes independently of command JSON validation.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param case: Selected malformed request or response scenario.
    :type case: str
    """

    # tmp_path — отдельный временный каталог теста.
    # case — выбранный сценарий нарушения формата или границ запроса.

    rig = Rig(tmp_path)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Build a valid offer and corrupt a single wire boundary."""

        await rig.start()

        try:
            offer = CommandOffer(sequence=1, request=rig.request(), remaining_ttl=10)
            body = encode_response(offer)
            assert decode_response(body) == offer
            if case == "duplicate":
                body = body[:-1] + b',"sequence":2}'
            elif case == "unknown":
                body = body[:-1] + b',"unexpected":1}'
            elif case == "nan":
                body = body.replace(b'"remaining_ttl":10', b'"remaining_ttl":NaN')
            elif case == "wrong_kind":
                body = b'{"offer":{},"sequence":1,"remaining_ttl":10}'
            elif case == "utf16":
                body = body.decode("utf-8").encode("utf-16")
            else:
                body = b" " * 70001
            with pytest.raises(CommandError, match="invalid"):
                decode_response(body)
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение места для heartbeat при занятом long poll
#------------------------------------------------------------------------------------------------------------------
def test_command_http_capacity(tmp_path: Path) -> None:

    """Keep heartbeat capacity while a long poll occupies its separate limit.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Hold one poll and verify that only extra polls are rejected."""

        rig = Rig(tmp_path)
        rig.hub.config = replace(rig.hub.config, poll_timeout=0.3)
        server = CommandHubServer(rig.hub, max_requests=2, max_polls=1)
        await server.start(allow_loopback_http=True)
        transport = HttpsCommandTransport(f"http://127.0.0.1:{server.port}", APP_TOKEN, allow_loopback_http=True)
        await transport.open()

        try:
            session = await transport.exchange("register", rig.registration)
            pending = asyncio.create_task(transport.exchange("poll", session))
            await asyncio.sleep(0.05)
            with pytest.raises(CommandError, match="busy"):
                await transport.exchange("poll", session)
            renewed = await transport.exchange("heartbeat", session)
            assert renewed.session_id == session.session_id
            assert await pending is None
        finally:
            await transport.close()
            await server.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_http не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
