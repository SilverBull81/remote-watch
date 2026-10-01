# Отдельный HTTP-сервер команд, не включаемый настройками relay.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-165638
#
# Классы:
# -> CommandHubServer: Сетевые endpoints регистрации и выполнения команд.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> start(): Запуск объекта и подготовка состояния.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.
#    Служебные методы:
#    -> _expect(): Отказ от Expect до чтения тела запроса.
#    -> _handle(): Авторизация до чтения тела и вызов подходящей операции.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import ipaddress
import ssl
from typing import Any

from remote_watch._validation import require_int
from remote_watch.commands.http_wire import MAX_HTTP_BYTES, OPERATIONS, encode_response
from remote_watch.commands.hub import CommandHub
from remote_watch.commands.protocol import (
    CommandClaim,
    CommandRegistration,
    CommandResult,
    CommandSession,
    decode_command,
)
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Сетевые endpoints регистрации и выполнения команд
#------------------------------------------------------------------------------------------------------------------
class CommandHubServer:
    """Expose client command routes independently of outbound relay routes and credentials."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        hub: CommandHub,
        *,
        max_requests: int = 16,
        max_polls: int = 8,
    ) -> None:

        """Bind one hub and reserve capacity for heartbeat, claims and results.

        :param hub: Independently configured command hub.
        :type hub: CommandHub

        :param max_requests: Maximum admitted handlers including long polls.
        :type max_requests: int

        :param max_polls: Maximum concurrent long polls, below total request capacity.
        :type max_polls: int
        """

        # hub — отдельно настроенный командный hub.
        # max_requests — общий предел допущенных обработчиков запросов.
        # max_polls — предел long poll, оставляющий места для остальных запросов.

        require_int(max_requests, "command request capacity")
        require_int(max_polls, "command poll capacity")

        if not max_polls < max_requests <= 256:
            raise ValueError("invalid command server capacity")
        self.hub = hub
        self.port: int | None = None
        self._max_requests = max_requests
        self._max_polls = max_polls
        self._active = 0
        self._polls = 0
        self._runner = None
        self._closed = False
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск объекта и подготовка состояния
    #--------------------------------------------------------------------------------------------------------------
    async def start(
        self,
        *,
        host: str = '127.0.0.1',
        port: int = 0,
        ssl_context: ssl.SSLContext | None = None,
        allow_loopback_http: bool = False,
    ) -> None:

        """Start an owned hub on TLS or explicitly allowed loopback HTTP.

        :param host: Explicit listener address.
        :type host: str

        :param port: Listener port; zero requests a free local port.
        :type port: int

        :param ssl_context: TLS context; clients must require certificate and hostname verification.
        :type ssl_context: ssl.SSLContext | None

        :param allow_loopback_http: Explicit permission for plaintext numeric loopback only.
        :type allow_loopback_http: bool
        """

        # host — явный адрес прослушивания сервера.
        # port — порт сервера; ноль выбирает свободный локальный порт.
        # ssl_context — контекст TLS; клиент обязан проверять сертификат и имя сервера.
        # allow_loopback_http — явное разрешение HTTP только на числовом loopback-адресе.

        if self._runner is not None or self._closed:
            raise CommandError("closed")
        require_int(port, "command server port", minimum=0)

        if port > 65535:
            raise ValueError("invalid command server port")

        if ssl_context is None:
            if not allow_loopback_http or not ipaddress.ip_address(host).is_loopback:
                raise ValueError("TLS required outside loopback")
        from aiohttp import web
        app = web.Application(client_max_size=MAX_HTTP_BYTES)

        for operation in sorted(OPERATIONS):
            app.router.add_post("/v1/commands/" + operation, self._handle, expect_handler=self._expect)
        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=1, handler_cancellation=True,
                                     max_line_size=2048, max_field_size=2048)

        try:
            await self.hub.start()
            await self._runner.setup()
            site = web.TCPSite(self._runner, host, port, ssl_context=ssl_context)
            await site.start()
            self.port = self._runner.addresses[0][1]
        except BaseException:
            await self.close()
            raise
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Wake hub polls before stopping the listener and its active handlers."""

        if self._closed:
            return
        self._closed = True
        await self.hub.close()

        if self._runner is not None:
            await self._runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Отказ от Expect до чтения тела запроса
    #--------------------------------------------------------------------------------------------------------------
    async def _expect(
        self,
        request: Any,
    ) -> Any:

        """Reject Expect before accepting a request body or disclosing authentication state.

        :param request: Incoming request validated before dispatch.
        :type request: Any

        :return: HTTP response with a bounded body and fixed status semantics.
        :rtype: Any
        """

        # request — входящий запрос, проверяемый перед обработкой.

        from aiohttp import web
        response = web.Response(status=417)
        response.force_close()
        return response
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Авторизация до чтения тела и вызов подходящей операции
    #--------------------------------------------------------------------------------------------------------------
    async def _handle(
        self,
        request: Any,
    ) -> Any:

        """Authenticate before bounded body reading and dispatch one typed operation.

        :param request: Incoming request validated before dispatch.
        :type request: Any

        :return: HTTP response with a bounded body and fixed status semantics.
        :rtype: Any
        """

        # request — входящий запрос, проверяемый перед обработкой.

        from aiohttp import web
        operation = request.path.rsplit("/", 1)[-1]
        polling = operation == "poll"
        counted = False

        try:
            auth = request.headers.getall("Authorization", [])
            if len(auth) != 1 or not auth[0].startswith("Bearer "):
                raise CommandError("denied")
            token = auth[0][7:]
            self.hub.authenticate(token)
            if self._active >= self._max_requests or (polling and self._polls >= self._max_polls):
                raise CommandError("busy")
            self._active += 1
            self._polls += int(polling)
            counted = True
            if request.content_type != "application/json" or request.headers.get("Content-Encoding"):
                raise CommandError("invalid")
            if request.content_length is not None and request.content_length > MAX_HTTP_BYTES:
                raise CommandError("invalid")
            body = await asyncio.wait_for(request.read(), 5)
            message = decode_command(body)
            expected = {"register": CommandRegistration, "heartbeat": CommandSession,
                        "poll": CommandSession, "claim": CommandClaim, "result": CommandResult,
                        "release": CommandClaim}[operation]
            if type(message) is not expected:
                raise CommandError("invalid")
            method = self.hub.finish if operation == "result" else getattr(self.hub, operation)
            result = await method(token, message)
            if result is None:
                return web.Response(status=204)
            return web.Response(body=encode_response(result), content_type="application/json")
        except CommandError as error:
            status = {"denied": 403, "invalid": 400, "busy": 429,
                      "conflict": 409, "stale_session": 409, "already_started": 409,
                      "outcome_conflict": 409, "expired": 409}.get(error.code, 503)
            response = web.json_response({"code": error.code}, status=status)
        except Exception:
            response = web.json_response({"code": "invalid"}, status=400)
        finally:
            if counted:
                self._active -= 1
                self._polls -= int(polling)
        response.force_close()
        return response
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль command_server не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
