# Исходящий relay-сервер с точными правами приложений и ограниченной обработкой.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260929-180009
#
# Классы:
# -> Gateway: Исходящий HTTP-сервер с управляемым временем работы.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска ресурсов.
#    Интерфейс:
#    -> port(): Чтение назначенного TCP-порта.
#    -> stats(): Копия счётчиков без приватных данных.
#    -> start(): Подготовка каналов и запуск HTTP-сервера.
#    -> close(): Ограниченное завершение и очистка.
#    Служебные методы:
#    -> _check_loop(): Проверка принадлежности циклу событий.
#    -> _open_channels(): Подготовка прямых каналов.
#    -> _expect(): Отказ от неподдерживаемого Expect до чтения тела.
#    -> _reject(): Безопасный отказ с закрытием соединения.
#    -> _authenticate(): Проверка отдельного сервисного токена.
#    -> _handle(): Проверка доступа и приём ограниченного запроса.
#    -> _read_body(): Чтение тела с ограничением размера.
#    -> _dispatch(): Одна попытка с проверкой alias и сроков.
#    -> _shutdown(): Завершение запросов и каналов в пределах общего срока.
#    -> _close_channel(): Закрытие одного канала без раскрытия ошибки.
#
# Функции:
# -> _utc_now(): Текущее время UTC.
# -> _consume_task(): Чтение результата поздней очистки.
# -> _serve(): Работа отдельного процесса до сигнала остановки.
# -> main(): Запуск gateway с JSON или доверенной Python-фабрикой.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import importlib
import ipaddress
import logging
import math
import os
import re
import signal
import ssl
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ._context import delivery_context
from .channels import NotificationChannel
from .delivery import DeliveryResult, DeliveryStatus, ResultSource
from .gateway_config import GatewayConfig, GatewayPrincipal
from .relay import MAX_REQUEST_BYTES, RelayRequest, encode_response

if TYPE_CHECKING:
    from aiohttp import web


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Текущее время UTC
#------------------------------------------------------------------------------------------------------------------
def _utc_now() -> datetime:

    """Read aware UTC time for expiry validation.

    :return: The value described by this operation.
    :rtype: datetime
    """

    return datetime.now(timezone.utc)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Исходящий HTTP-сервер с управляемым временем работы
#------------------------------------------------------------------------------------------------------------------
class Gateway:
    """Own a single-loop outbound server; construction has no network side effects."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: GatewayConfig,
        *,
        utc_now: Callable[[], datetime] = _utc_now,
        ) -> None:

        """Store validated configuration without reading tokens or starting channels.

        :param config: Validated server configuration.
        :type config: GatewayConfig

        :param utc_now: Aware UTC clock used for expiry checks.
        :type utc_now: Callable[[], datetime]
        """

        # config - проверенные настройки сервера.
        # utc_now - часы UTC для проверки срока уведомления.

        if not isinstance(config, GatewayConfig) or not callable(utc_now):
            raise TypeError("gateway requires GatewayConfig and a clock")
        self._config = config
        self._utc_now = utc_now
        self._state = "new"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._channels: dict[str, NotificationChannel] = {}
        self._tokens: list[tuple[bytes, GatewayPrincipal]] = []
        self._active: set[asyncio.Task[object]] = set()
        self._principal_active: Counter[str] = Counter()
        self._busy: set[str] = set()
        self._principal_next: dict[str, float] = {}
        self._destination_next: dict[str, float] = {}
        self._counts: Counter[str] = Counter()
        self._close_task: asyncio.Task[None] | None = None
        self._start_task: asyncio.Task[object] | None = None
        self._port: int | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение назначенного TCP-порта
    #--------------------------------------------------------------------------------------------------------------
    @property
    def port(self) -> int:

        """Return the listening TCP port after a successful start.

        :return: The value described by this operation.
        :rtype: int
        """

        if self._state != "running" or self._port is None:
            raise RuntimeError("gateway is not running")
        return self._port
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Копия счётчиков без приватных данных
    #--------------------------------------------------------------------------------------------------------------
    def stats(self) -> dict[str, int]:

        """Return aggregate counters without credentials, bodies or remote identifiers.

        :return: The value described by this operation.
        :rtype: dict[str, int]
        """

        self._check_loop()
        return dict(self._counts, active=len(self._active), destinations_busy=len(self._busy))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка каналов и запуск HTTP-сервера
    #--------------------------------------------------------------------------------------------------------------
    async def start(
        self,
        *,
        host: str = '127.0.0.1',
        port: int = 8765,
        ssl_context: ssl.SSLContext | None = None,
        ) -> None:

        """Open providers, then listen; plaintext is restricted to numeric loopback addresses.

        :param host: Explicit bind address.
        :type host: str

        :param port: Listening port; zero asks the OS to select a free port.
        :type port: int

        :param ssl_context: Server TLS context or None for loopback HTTP.
        :type ssl_context: ssl.SSLContext | None
        """

        # host - явный адрес прослушивания.
        # port - TCP-порт; ноль выбирает свободный порт.
        # ssl_context - настройки TLS; None разрешён только на loopback.

        try:
            from aiohttp import web
        except ImportError:
            raise ImportError("install remote-watch[gateway] to run a gateway") from None

        self._check_loop()
        if self._state != "new":
            raise RuntimeError("gateway can only be started once")
        if not isinstance(host, str) or type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("invalid bind address")
        if ssl_context is not None and not isinstance(ssl_context, ssl.SSLContext):
            raise TypeError("ssl_context must be SSLContext")
        if ssl_context is None:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise ValueError("TLS is required outside numeric loopback")

        self._loop = asyncio.get_running_loop()
        self._state = "starting"
        self._start_task = asyncio.current_task()
        try:
            # Токены читаются только при запуске. В памяти сервера для поиска остаются их хеши.
            seen = set()
            for principal in self._config.principals:
                token = os.environ.get(principal.token_env, "")
                if re.fullmatch(r"[A-Za-z0-9_-]{32,512}", token) is None:
                    raise ValueError("missing or invalid gateway credential")
                digest = hashlib.sha256(token.encode("ascii")).digest()
                if digest in seen:
                    raise ValueError("gateway credentials must be distinct")
                seen.add(digest)
                self._tokens.append((digest, principal))

            await asyncio.wait_for(self._open_channels(), self._config.startup_timeout)
            app = web.Application(client_max_size=MAX_REQUEST_BYTES)
            # Единственный маршрут не создаёт ни command endpoints, ни общего HTTP proxy.
            app.router.add_post("/v1/notifications", self._handle, expect_handler=self._expect)
            quiet = logging.Logger("remote_watch.gateway.http", level=logging.CRITICAL + 1)
            quiet.addHandler(logging.NullHandler())
            quiet.propagate = False
            self._runner = web.AppRunner(app, access_log=None, logger=quiet,
                handler_cancellation=True, auto_decompress=False, keepalive_timeout=2,
                max_line_size=4096, max_field_size=4096, shutdown_timeout=0.1,
                lingering_time=0, timeout_ceil_threshold=float("inf"))
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, host, port, ssl_context=ssl_context, backlog=64)
            await self._site.start()
            self._port = self._runner.addresses[0][1]
            self._state = "running"
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise RuntimeError("gateway startup failed; private transport details are hidden") from None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ограниченное завершение и очистка
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Reject new work and share one bounded cleanup task across callers."""

        self._check_loop()
        if self._state == "starting" and asyncio.current_task() is not self._start_task:
            raise RuntimeError("cancel and await start before closing a starting gateway")
        if self._close_task is None:
            self._state = "closing"
            self._close_task = asyncio.create_task(self._shutdown())
        # Отмена ожидающего владельца не отменяет уже начатое освобождение ресурсов.
        await asyncio.shield(self._close_task)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка принадлежности циклу событий
    #--------------------------------------------------------------------------------------------------------------
    def _check_loop(self) -> None:

        """Reject calls from a different event loop."""

        if self._loop is not None and self._loop is not asyncio.get_running_loop():
            raise RuntimeError("gateway must be used in its owning loop")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Подготовка прямых каналов
    #--------------------------------------------------------------------------------------------------------------
    async def _open_channels(self) -> None:

        """Open each owned direct channel under diagnostic recursion protection."""

        context = delivery_context.set(True)
        try:
            for destination in self._config.destinations:
                channel = destination.channel_factory()
                if any(channel is previous for previous in self._channels.values()):
                    raise ValueError("gateway factories must create separate channels")
                self._channels[destination.destination_id] = channel
                await channel.open()
        finally:
            delivery_context.reset(context)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Отказ от неподдерживаемого Expect до чтения тела
    #--------------------------------------------------------------------------------------------------------------
    async def _expect(
        self,
        request: web.Request,
        ) -> web.StreamResponse:

        """Reject Expect before reading a body or acknowledging an unauthenticated request.

        :param request: Incoming HTTP request.
        :type request: web.Request

        :return: The value described by this operation.
        :rtype: web.StreamResponse
        """

        # request - входящий HTTP-запрос.

        return self._reject(417, "relay_expect_unsupported")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Безопасный отказ с закрытием соединения
    #--------------------------------------------------------------------------------------------------------------
    def _reject(
        self,
        status: int,
        reason: str,
        retry_after: float = 1.0,
        ) -> web.Response:

        """Return a fixed safe error and close rather than drain an untrusted body.

        :param status: Fixed HTTP status.
        :type status: int

        :param reason: Safe fixed machine-readable reason.
        :type reason: str

        :param retry_after: Minimum delay before another attempt, in seconds.
        :type retry_after: float

        :return: The value described by this operation.
        :rtype: web.Response
        """

        # status - заданный числовой HTTP-статус.
        # reason - безопасный фиксированный код причины.
        # retry_after - нижняя граница повторной попытки, секунды.

        from aiohttp import web

        response = web.json_response({"reason": reason}, status=status,
            headers={"Retry-After": str(math.ceil(retry_after))} if status == 429 else None)
        response.force_close()
        return response
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка отдельного сервисного токена
    #--------------------------------------------------------------------------------------------------------------
    def _authenticate(
        self,
        request: web.Request,
        ) -> GatewayPrincipal | None:

        """Compare bounded bearer hashes without accepting alternate credential locations.

        :param request: Incoming HTTP request.
        :type request: web.Request

        :return: The value described by this operation.
        :rtype: GatewayPrincipal | None
        """

        # request - входящий HTTP-запрос.

        headers = request.headers.getall("Authorization", [])
        if len(headers) != 1 or re.fullmatch(r"Bearer [A-Za-z0-9_-]{32,512}", headers[0]) is None:
            return None
        digest = hashlib.sha256(headers[0][7:].encode("ascii")).digest()
        match = None
        for expected, principal in self._tokens:
            if hmac.compare_digest(digest, expected):
                match = principal
        return match
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка доступа и приём ограниченного запроса
    #--------------------------------------------------------------------------------------------------------------
    async def _handle(
        self,
        request: web.Request,
        ) -> web.Response:

        """Authenticate and reserve capacity before bounded body reading and dispatch.

        :param request: Incoming HTTP request.
        :type request: web.Request

        :return: The value described by this operation.
        :rtype: web.Response
        """

        # request - входящий HTTP-запрос.

        self._counts["requests"] += 1
        if self._state != "running":
            return self._reject(503, "relay_closing")
        principal = self._authenticate(request)
        if principal is None:
            self._counts["auth_denied"] += 1
            return self._reject(401, "relay_auth_denied")
        if (len(self._active) >= self._config.capacity or
                self._principal_active[principal.name] >= principal.capacity):
            self._counts["overloaded"] += 1
            return self._reject(429, "relay_overloaded")
        now = asyncio.get_running_loop().time()
        delay = self._principal_next.get(principal.name, 0) - now
        if delay > 0:
            self._counts["rate_limited"] += 1
            return self._reject(429, "relay_principal_rate", delay)
        self._principal_next[principal.name] = now + principal.min_interval
        # Сжатие запрещено до чтения; parser не распаковывает тело автоматически.
        if request.query_string or request.headers.get("Content-Encoding", "identity") != "identity":
            return self._reject(400, "relay_invalid_request")
        if request.content_type != "application/json":
            return self._reject(415, "relay_content_type")
        if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
            return self._reject(413, "relay_request_too_large")

        task = asyncio.current_task()
        assert task is not None
        self._active.add(task)
        self._principal_active[principal.name] += 1
        started = asyncio.get_running_loop().time()
        context = delivery_context.set(True)
        try:
            # Отдельный timeout защищает от медленного тела. Для chunked действует тот же предел байтов.
            data = await asyncio.wait_for(self._read_body(request), self._config.body_timeout)
            if data is None:
                self._counts["invalid"] += 1
                return self._reject(413, "relay_request_too_large")
            envelope = RelayRequest.from_bytes(data)
            if (envelope.delivery.notification.identity != principal.identity or
                    envelope.alias not in principal.aliases):
                self._counts["auth_denied"] += 1
                return self._reject(403, "relay_auth_denied")
            return await self._dispatch(envelope, started)
        except asyncio.CancelledError:
            self._counts["cancelled_requests"] += 1
            raise
        except asyncio.TimeoutError:
            self._counts["body_timeout"] += 1
            return self._reject(408, "relay_body_timeout")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            self._counts["invalid"] += 1
            return self._reject(400, "relay_invalid_request")
        except Exception:
            self._counts["request_errors"] += 1
            return self._reject(500, "relay_request_failed")
        finally:
            self._active.discard(task)
            self._principal_active[principal.name] -= 1
            delivery_context.reset(context)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Чтение тела с ограничением размера
    #--------------------------------------------------------------------------------------------------------------
    async def _read_body(
        self,
        request: web.Request,
        ) -> bytes | None:

        """Read at most the wire limit plus one small chunk without decompression.

        :param request: Incoming HTTP request.
        :type request: web.Request

        :return: The value described by this operation.
        :rtype: bytes | None
        """

        # request - входящий HTTP-запрос.

        data = bytearray()
        async for chunk in request.content.iter_chunked(4096):
            if len(data) + len(chunk) > MAX_REQUEST_BYTES:
                return None
            data.extend(chunk)
        return bytes(data)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Одна попытка с проверкой alias и сроков
    #--------------------------------------------------------------------------------------------------------------
    async def _dispatch(
        self,
        envelope: RelayRequest,
        started: float,
        ) -> web.Response:

        """Authorize one direct attempt with server, expiry and elapsed-time budgets.

        :param envelope: Validated relay request.
        :type envelope: RelayRequest

        :param started: Monotonic request admission time.
        :type started: float

        :return: The value described by this operation.
        :rtype: web.Response
        """

        # envelope - проверенный запрос одной попытки.
        # started - монотонное время приёма запроса.

        from aiohttp import web

        alias = envelope.alias
        if alias in self._busy:
            self._counts["overloaded"] += 1
            return self._reject(429, "relay_destination_busy")
        delay = self._destination_next.get(alias, 0) - asyncio.get_running_loop().time()
        if delay > 0:
            self._counts["rate_limited"] += 1
            return self._reject(429, "relay_destination_rate", delay)
        destination = next(item for item in self._config.destinations if item.destination_id == alias)
        now = self._utc_now()
        event = envelope.delivery.notification
        elapsed = asyncio.get_running_loop().time() - started
        remaining = min(envelope.timeout - elapsed, envelope.remaining_ttl - elapsed,
            (event.expires_at - now).total_seconds(),
            destination.retry.ttl - (now - event.created_at).total_seconds(),
            self._config.attempt_timeout, destination.retry.attempt_timeout)
        if remaining <= 0 or (event.created_at - now).total_seconds() > self._config.future_tolerance:
            self._counts["expired"] += 1
            result = DeliveryResult(status=DeliveryStatus.PERMANENT_FAILURE, reason_code="relay_expired")
        else:
            # В одном alias работает только один канал. Очереди ожидания и второго retry loop нет.
            self._busy.add(alias)
            self._destination_next[alias] = asyncio.get_running_loop().time() + self._config.destination_interval
            self._counts["attempts"] += 1
            try:
                delivery = replace(envelope.delivery, remaining_timeout=remaining)
                result = await asyncio.wait_for(self._channels[alias].send(delivery), remaining)
                if not isinstance(result, DeliveryResult) or result.source is not ResultSource.PROVIDER:
                    raise TypeError("gateway requires a provider result")
            except asyncio.CancelledError:
                self._counts["unknown"] += 1
                raise
            except Exception:
                # Provider мог успеть принять сообщение до timeout/ошибки. Повтор решает клиент.
                result = DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="relay_attempt_unknown")
            finally:
                self._busy.remove(alias)
            self._counts[result.status.value] += 1
        response = web.Response(body=encode_response(envelope.delivery, result), content_type="application/json")
        response.force_close()
        return response
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Завершение запросов и каналов в пределах общего срока
    #--------------------------------------------------------------------------------------------------------------
    async def _shutdown(self) -> None:

        """Drain requests, cancel the remainder and close providers within one shared deadline."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.shutdown_timeout
        context = delivery_context.set(True)
        try:
            if self._site is not None:
                await self._site.stop()
            # Часть общего срока оставляем на отмену и закрытие всех клиентов, а не только первого.
            if self._active:
                _, pending = await asyncio.wait(tuple(self._active), timeout=max(0, deadline - loop.time()) * 0.6)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=max(0, deadline - loop.time()) * 0.25)
            tasks = [asyncio.create_task(self._close_channel(channel)) for channel in self._channels.values()]
            if self._runner is not None:
                tasks.append(asyncio.create_task(self._runner.cleanup()))
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=max(0, deadline - loop.time()))
                for task in pending:
                    task.cancel()
                for task in done:
                    if not task.cancelled() and task.exception() is not None:
                        self._counts["close_failed"] += 1
                if pending:
                    self._counts["close_failed"] += len(pending)
                    # Завершение отмены обеспечивается контрактом адаптера; исключения забирает callback.
                    for task in pending:
                        task.add_done_callback(_consume_task)
        finally:
            self._tokens.clear()
            self._state = "closed"
            delivery_context.reset(context)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Закрытие одного канала без раскрытия ошибки
    #--------------------------------------------------------------------------------------------------------------
    async def _close_channel(
        self,
        channel: NotificationChannel,
        ) -> None:

        """Close one channel without exposing transport exception details.

        :param channel: Owned provider channel.
        :type channel: NotificationChannel
        """

        # channel - канал, принадлежащий серверу.

        try:
            await channel.close()
        except Exception:
            self._counts["close_failed"] += 1
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение результата поздней очистки
#------------------------------------------------------------------------------------------------------------------
def _consume_task(task: asyncio.Task[object]) -> None:

    """Retrieve a late cleanup exception without logging provider data.

    :param task: Tracked asynchronous operation.
    :type task: asyncio.Task[object]
    """

    # task - учтённая асинхронная операция.

    if not task.cancelled():
        task.exception()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Работа отдельного процесса до сигнала остановки
#------------------------------------------------------------------------------------------------------------------
async def _serve(
    config: GatewayConfig,
    host: str,
    port: int,
    context: ssl.SSLContext | None,
    ) -> None:

    """Run the configured gateway until the process is interrupted.

    :param config: Validated server configuration.
    :type config: GatewayConfig

    :param host: Explicit bind address.
    :type host: str

    :param port: Listening port; zero asks the OS to select a free port.
    :type port: int

    :param context: Server TLS context or None for loopback HTTP.
    :type context: ssl.SSLContext | None
    """

    # config - проверенные настройки сервера.
    # host - явный адрес прослушивания.
    # port - TCP-порт; ноль выбирает свободный порт.
    # context - настройки шифрования серверного соединения.

    gateway = Gateway(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal_installed = False
    try:
        # На Unix служебный SIGTERM запускает ту же очистку, что Ctrl+C. Windows использует Ctrl+C.
        try:
            loop.add_signal_handler(signal.SIGTERM, stop.set)
            signal_installed = True
        except NotImplementedError:
            pass
        await gateway.start(host=host, port=port, ssl_context=context)
        print(f"Gateway запущен, TCP-порт {gateway.port}. Для остановки нажмите Ctrl+C.")
        await stop.wait()
    finally:
        await gateway.close()
        if signal_installed:
            loop.remove_signal_handler(signal.SIGTERM)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запуск gateway с JSON или доверенной Python-фабрикой
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Load local JSON or a trusted Python factory and run a separate relay process.

    :param argv: Explicit CLI arguments or None.
    :type argv: Sequence[str] | None

    :return: The value described by this operation.
    :rtype: int
    """

    # argv - аргументы запуска; None читает командную строку процесса.

    parser = argparse.ArgumentParser(description="Исходящий gateway Remote Watch без контура команд.")
    parser.add_argument("factory", nargs="?", help="Доверенная локальная функция module:function")
    parser.add_argument("--config", help="JSON-файл настроек gateway, схема версии 1")
    parser.add_argument("--check-config", action="store_true",
                        help="Проверить настройки без запуска gateway и чтения токенов")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    args = parser.parse_args(argv)
    if (args.factory is None) == (args.config is None):
        parser.error("Укажите либо --config, либо Python-фабрику module:function.")
    try:
        # JSON предназначен для обычного развёртывания без собственного Python-кода.
        # Фабрика остаётся альтернативой для пользовательских адаптеров и выполняется
        # как доверенный код, в том числе при --check-config.
        if args.config is not None:
            from .gateway_json import load_gateway_config

            config = load_gateway_config(args.config)
        else:
            module, name = args.factory.split(":")
            if not name.isidentifier():
                raise ValueError("invalid factory")
            config = getattr(importlib.import_module(module), name)()
        if not isinstance(config, GatewayConfig):
            raise TypeError("factory must return GatewayConfig")
        if bool(args.cert) != bool(args.key):
            raise ValueError("both certificate and key are required")
        if args.check_config:
            print("Настройки gateway корректны. Токены, сертификаты и доступность сети не проверялись.")
            return 0
        context = None
        if args.cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(args.cert, args.key)
        asyncio.run(_serve(config, args.host, args.port, context))
    except KeyboardInterrupt:
        return 130
    except Exception:
        print("Gateway не запущен или завершился с ошибкой. Проверьте настройки, credentials и extra gateway.")
        return 1
    return 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Запуск исходящего gateway отдельным процессом
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
