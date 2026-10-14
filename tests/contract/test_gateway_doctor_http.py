# Настоящий локальный HTTPS для doctor: права, свежесть времени и отсутствие побочных действий.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Тесты:
# -> test_command_probe(): Авторизация и наблюдение сессии без регистрации или продления
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#
# -> test_notification_probe(): Отсутствие доставки и изменений рабочих счётчиков
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#
# -> test_tls_failures(): Различение отказов сертификата без отключения TLS
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#
# -> test_http_failures(): Ошибки маршрута, upstream и ответа без утечки или redirect
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#       Вложенные объявления:
#       -> respond(): Тестовый ответ сервера без обращения к внешним сервисам
#
# -> test_probe_limits(): Независимая квота и ограничение размера тела
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#
# -> test_server_status(): Чтение готовности manager без управления процессами
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#
# -> test_network_faults(): Отказ TCP и порт без TLS отличаются от сертификатных ошибок
#    Вложенные объявления:
#    -> scenario(): Проверка асинхронного сценария с изолированными ресурсами
#       Вложенные объявления:
#       -> accept(): Приём тестового соединения без регистрации клиентской сессии


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from remote_watch.diagnostics._doctor_network import network_checks
from remote_watch.diagnostics.gateway_doctor import _server_online
from remote_watch.gateway.command_server import CommandHubServer
from remote_watch.gateway.doctor_protocol import ProbeEndpoint, probe_response
from remote_watch.gateway.notification_server import NotificationGateway

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

aiohttp = pytest.importorskip("aiohttp")

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from aiohttp import web  # noqa: E402
from test_command_hub import APP_TOKEN, Rig  # noqa: E402
from test_gateway import TOKEN, Channel, settings  # noqa: E402
from test_gateway_tls import certificates  # noqa: E402

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Авторизация и наблюдение сессии без регистрации или продления
#------------------------------------------------------------------------------------------------------------------
def test_command_probe(
    tmp_path: Path,
    ) -> None:

    """
    Keep session deadlines and persistent rows unchanged across authorized probes.

    :param tmp_path: Temporary journals and synthetic certificate directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельные журналы и CA этого сценария.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Exercise the isolated asynchronous server lifecycle.
        """

        # Один настоящий hub со своими SQLite и TLS; внешняя сеть не используется.
        rig = Rig(tmp_path)
        ca, cert, key = certificates(tmp_path, "trusted")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        context = ssl.create_default_context(cafile=str(ca))
        server = CommandHubServer(rig.hub)
        await server.start(ssl_context=server_context)
        endpoint = f"https://127.0.0.1:{server.port}"
        value = {"schema_version": 1, "identity": asdict(rig.identity), "permissions": ["control"]}
        try:
            rows = await asyncio.to_thread(network_checks, endpoint, context, "commands", APP_TOKEN,
                                           json.dumps(value).encode(), 2)
            assert rows[-1]["code"] == "application_not_registered"
            assert not rig.hub._issued
            await rig.client.start()
            entry = rig.hub._targets[rig.identity]
            deadline = entry.deadline
            issued = dict(rig.hub._issued)

            # Фоновые часы неподвижны. Probe не должен продлевать lease или обращаться к storage.
            original_call = rig.hub._worker.call
            blocked = AsyncMock(side_effect=AssertionError("doctor storage mutation"))
            rig.hub._worker.call = blocked
            rows = await asyncio.to_thread(network_checks, endpoint, context, "commands", APP_TOKEN,
                                           json.dumps(value).encode(), 2)
            rig.hub._worker.call = original_call
            blocked.assert_not_called()
            assert rows[-1]["code"] == "session_present"
            assert all(row["status"] == "ok" for row in rows)
            assert rig.hub._issued == issued and entry.deadline == deadline

            # Просроченное trusted time остаётся наблюдаемым: аутентификация не вызывает _check().
            rig.now += 3601
            rows = await asyncio.to_thread(network_checks, endpoint, context, "commands", APP_TOKEN,
                                           json.dumps(value).encode(), 2)
            assert any(row["code"] == "time_unavailable" for row in rows), rows
            assert rows[-1]["code"] == "application_not_registered"

            async with aiohttp.ClientSession() as session:
                for token, permissions, status in [("x" * 40, ["control"], 401),
                                                   (APP_TOKEN, ["other"], 403)]:
                    body = dict(value, permissions=permissions)
                    async with session.post(endpoint + "/v1/commands/probe", json=body, ssl=context,
                                            headers={"Authorization": "Bearer " + token}) as response:
                        assert response.status == status
                        assert response.headers["X-Remote-Watch-Probe"] == "1"
                        assert await response.json() == {"code": "denied"}
                body = dict(value, identity=dict(value["identity"], instance_id="other"))
                async with session.post(endpoint + "/v1/commands/probe", json=body, ssl=context,
                                        headers={"Authorization": "Bearer " + APP_TOKEN}) as response:
                    assert response.status == 403
        finally:
            await rig.client.close()
            await server.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отсутствие доставки и изменений рабочих счётчиков
#------------------------------------------------------------------------------------------------------------------
def test_notification_probe(
    notification: object,
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """
    Observe the notification listener without consuming rate or delivery capacity.

    :param notification: Synthetic notification fixture.
    :type notification: object

    :param monkeypatch: Scoped synthetic credential environment.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # notification/monkeypatch — синтетическое событие и отдельный токен теста.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Exercise the isolated asynchronous server lifecycle.
        """

        # Это отдельный endpoint: даже успешная проверка не вызывает channel.send().
        monkeypatch.setenv("RW_GW_TEST", TOKEN)
        channel = Channel()
        gateway = NotificationGateway(settings(notification, channel))
        await gateway.start(port=0)
        try:
            before = dict(gateway._counts)
            value = {"schema_version": 1, "identity": asdict(notification.identity), "permissions": ["phone"]}
            async with aiohttp.ClientSession() as session:
                async with session.post(f"http://127.0.0.1:{gateway.port}/v1/notifications/probe", json=value,
                                        headers={"Authorization": "Bearer " + TOKEN}) as response:
                    assert response.status == 200
                    assert await response.json() == probe_response("notifications", {"ready": True})
            assert not channel.calls
            assert gateway._counts == before
            assert not gateway._principal_next and not gateway._destination_next
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Различение отказов сертификата без отключения TLS
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,code", [("expired", 10), ("wrong_name", 64), ("untrusted", 20)])
def test_tls_failures(
    tmp_path: Path,
    mode: str,
    code: int,
    ) -> None:

    """
    Preserve safe TLS verification diagnostics using an actual local handshake.

    :param tmp_path: Temporary synthetic certificate folder.
    :type tmp_path: Path

    :param mode: Selected certificate failure.
    :type mode: str

    :param code: Expected OpenSSL verification category.
    :type code: int
    """

    # tmp_path/mode/code — независимая цепочка сертификатов и ожидаемый отказ.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Exercise the isolated asynchronous server lifecycle.
        """

        # Не устанавливаем CA в ОС; каждый тест получает собственный проверяющий context.
        ca, cert, key = certificates(tmp_path, mode)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        context = ssl.create_default_context(cafile=None if mode == "untrusted" else str(ca))
        rig = Rig(tmp_path)
        server = CommandHubServer(rig.hub)
        await server.start(ssl_context=server_context)
        try:
            rows = await asyncio.to_thread(network_checks, f"https://127.0.0.1:{server.port}", context,
                                           "commands", APP_TOKEN, b"{}", 2)
            assert rows[-1]["code"] == "tls_certificate"
            assert rows[-1]["verify_code"] == code
            assert all("private" not in str(row) for row in rows)
            assert not rig.hub._issued
        finally:
            await server.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибки маршрута, upstream и ответа без утечки или redirect
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,mode,code", [
    (404, "", "route_missing"), (502, "", "upstream_unavailable"), (200, "", "route_unverified"),
    (302, "", "redirect_rejected"), (401, "marker", "credential_denied"),
    (403, "marker", "permission_denied"), (429, "marker", "probe_busy"),
    (200, "marker", "response_invalid"), (200, "large", "response_invalid"),
    (200, "slow", "http_timeout"), (200, "drip", "http_timeout"),
])
def test_http_failures(
    tmp_path: Path,
    status: int,
    mode: str,
    code: str,
    ) -> None:

    """
    Reject private error bodies and redirects over a verified TLS connection.

    :param tmp_path: Temporary certificate directory.
    :type tmp_path: Path

    :param status: Synthetic upstream HTTP status.
    :type status: int

    :param mode: Response marker and body variant.
    :type mode: str

    :param code: Expected safe result category.
    :type code: str
    """

    # tmp_path/status/mode/code — варианты отказов локального подставного upstream.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Exercise the isolated asynchronous server lifecycle.
        """

        # Проверяем реальный POST и подсчитываем запросы: перехода по Location быть не должно.
        ca, cert, key = certificates(tmp_path, "trusted")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        context = ssl.create_default_context(cafile=str(ca))
        seen = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Тестовый ответ сервера без обращения к внешним сервисам
        #----------------------------------------------------------------------------------------------------------
        async def respond(
            request: web.Request,
            ) -> web.StreamResponse:

            """
            Return an intentionally untrusted proxy response.

            :param request: Incoming diagnostic POST.
            :type request: web.Request

            :return: Synthetic response containing private data that must be discarded.
            :rtype: web.StreamResponse
            """

            # request — исходный запрос команды, связанный с разрешённым назначением.


            # Подставной ответ может содержать секреты: doctor не печатает его содержимое.
            seen.append(request.path)
            if mode == "slow":
                await asyncio.sleep(.7)
            headers = {"Location": "/private-redirect"}
            if mode:
                headers.update({"X-Remote-Watch-Probe": "1",
                                "X-Remote-Watch-Component": "commands"})
            if mode == "drip":
                response = web.StreamResponse(headers=headers)
                response.content_type = "application/json"
                await response.prepare(request)
                try:
                    for _ in range(30):
                        await response.write(b" ")
                        await asyncio.sleep(.05)
                except (ConnectionError, OSError):
                    pass
                return response
            return web.Response(status=status, headers=headers, content_type="application/json",
                                text="private" * (1000 if mode == "large" else 1))
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", respond)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context).start()
        try:
            rows = await asyncio.to_thread(network_checks, f"https://127.0.0.1:{runner.addresses[0][1]}",
                                           context, "commands", APP_TOKEN, b"{}",
                                           .4 if mode in ("slow", "drip") else 2)
            assert rows[-1]["code"] == code
            assert "private" not in json.dumps(rows)
            assert seen == ["/v1/commands/probe"]
        finally:
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимая квота и ограничение размера тела
#------------------------------------------------------------------------------------------------------------------
def test_probe_limits() -> None:

    """
    Bound slow or oversized diagnostic requests independently of application traffic.
    """

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Exercise the isolated asynchronous server lifecycle.
        """

        principal = SimpleNamespace(identities=(), scopes=())
        endpoint = ProbeEndpoint("commands", lambda request: principal, lambda identity: {"ready": True})
        app = web.Application()
        app.router.add_post("/probe", endpoint.handle)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        url = f"http://127.0.0.1:{runner.addresses[0][1]}/probe"
        try:
            async with aiohttp.ClientSession() as session:
                endpoint._active = 2
                async with session.post(url, json={}) as response:
                    assert response.status == 429
                assert endpoint._active == 2
                endpoint._active = 0
                async with session.post(url, data=b" " * 16385,
                                        headers={"Content-Type": "application/json"}) as response:
                    assert response.status == 400
                assert endpoint._active == 0
                async with session.post(url, data=b'{"schema_version":1,"schema_version":1}',
                                        headers={"Content-Type": "application/json"}) as response:
                    assert response.status == 400
        finally:
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Чтение готовности manager без управления процессами
#------------------------------------------------------------------------------------------------------------------
def test_server_status(
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """
    Filter local manager status without launching, stopping or exposing component payloads.

    :param monkeypatch: Scoped control transport replacement.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # monkeypatch — подмена только status, без запуска серверных процессов.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Exercise the isolated asynchronous server lifecycle.
        """

        # Doctor вызывает только status; неизвестные значения из ответа не попадают в отчёт.
        control = AsyncMock(return_value={"state": "degraded", "secret": "private",
            "components": {"commands": {"state": "degraded", "reason": "time_unavailable", "path": "private"},
                           "private-component": {"state": "private"}}})
        monkeypatch.setattr("remote_watch.gateway_server.manager.control_request", control)
        config = SimpleNamespace(notifications=None, commands=None)
        rows = await _server_online(config, 1)
        control.assert_awaited_once_with(config, "status")
        assert "private" not in json.dumps(rows)
        assert any(row["code"] == "time_unavailable" for row in rows)
        assert rows[-1] == {"check": "journal_unknown_outcomes", "status": "skipped", "code": "not_observed"}
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ TCP и порт без TLS отличаются от сертификатных ошибок
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["refused", "plaintext", "silent"])
def test_network_faults(
    mode: str,
    ) -> None:

    """
    Distinguish TCP refusal, a plaintext service and a stalled TLS handshake.

    :param mode: Synthetic loopback listener behavior.
    :type mode: str
    """

    # mode — сеть ограничена отдельным loopback listener без провайдеров и токенов.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка асинхронного сценария с изолированными ресурсами
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Own and close each accepted stream before returning from the test.
        """

        writers = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Приём тестового соединения без регистрации клиентской сессии
        #----------------------------------------------------------------------------------------------------------
        def accept(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            ) -> None:

            """
            Respond in plaintext or hold a synthetic handshake open.

            :param reader: Unused input stream.
            :type reader: asyncio.StreamReader

            :param writer: Owned accepted stream.
            :type writer: asyncio.StreamWriter
            """

            # reader — чтение запроса из тестового соединения.
            # writer — запись ответа в тестовое соединение.


            # Ни один ответ не содержит закрытых данных теста.
            writers.append(writer)
            if mode == "plaintext":
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        #----------------------------------------------------------------------------------------------------------

        listener = await asyncio.start_server(accept, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        if mode == "refused":
            listener.close()
            await listener.wait_closed()
        try:
            rows = await asyncio.to_thread(network_checks, f"https://127.0.0.1:{port}",
                                           ssl.create_default_context(), "commands", APP_TOKEN, b"{}",
                                           3 if mode == "refused" else .4)
            expected = {"refused": "tcp_refused", "plaintext": "tls_handshake", "silent": "tls_timeout"}
            assert rows[-1]["code"] == expected[mode]
        finally:
            listener.close()
            for writer in writers:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2)
                except OSError:
                    pass
            await asyncio.wait_for(listener.wait_closed(), 2)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Запуск проверок через pytest
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
#------------------------------------------------------------------------------------------------------------------
