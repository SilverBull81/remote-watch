# HTTPS-транспорт команд с проверкой TLS и ограничением одновременных запросов.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-153416
#
# Классы:
# -> HttpsCommandTransport: Защищённые запросы без неявных повторов.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие принадлежащих объекту ресурсов.
#    -> exchange(): Один обмен с проверкой ответа и без скрытого retry.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import ipaddress
import ssl
from urllib.parse import urlsplit

from remote_watch._validation import require_number
from remote_watch.commands.http_wire import MAX_HTTP_BYTES, OPERATIONS, decode_response
from remote_watch.commands.hub_config import _token
from remote_watch.commands.protocol import CommandMessage, encode_command
from remote_watch.commands.transport import CommandError, CommandOffer
from remote_watch.notifications._context import delivery_context


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Защищённые запросы без неявных повторов
#------------------------------------------------------------------------------------------------------------------
class HttpsCommandTransport:
    """Make bounded authenticated exchanges without implicit retries or redirects."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float = 25.0,
        allow_loopback_http: bool = False,
    ) -> None:

        """Validate a command-only endpoint and retain credentials outside representations.

        :param endpoint: HTTPS origin without credentials, query or path.
        :type endpoint: str

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param ssl_context: TLS context; clients must require certificate and hostname verification.
        :type ssl_context: ssl.SSLContext | None

        :param timeout: Finite wait limit in seconds.
        :type timeout: float

        :param allow_loopback_http: Explicit permission for plaintext numeric loopback only.
        :type allow_loopback_http: bool
        """

        # endpoint — адрес HTTPS без секрета, параметров и пути операции.
        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # ssl_context — контекст TLS; клиент обязан проверять сертификат и имя сервера.
        # timeout — конечный предел ожидания, секунды.
        # allow_loopback_http — явное разрешение HTTP только на числовом loopback-адресе.

        _token(token)
        require_number(timeout, "command HTTP timeout")

        if timeout > 30:
            raise ValueError("invalid command HTTP timeout")
        parsed = urlsplit(endpoint)

        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise ValueError("invalid command endpoint")

        if parsed.path not in ("", "/") or parsed.port == 0:
            raise ValueError("invalid command endpoint")

        if parsed.scheme != "https":
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = False
            if parsed.scheme != "http" or not allow_loopback_http or not loopback:
                raise ValueError("HTTPS required")
        context = ssl_context or ssl.create_default_context()

        if (not isinstance(context, ssl.SSLContext) or not context.check_hostname
                or context.verify_mode != ssl.CERT_REQUIRED):
            raise ValueError("TLS verification required")
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._ssl = context
        self._timeout = timeout
        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active = 0
        self._closed = False
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Create the optional HTTP client only on explicit startup."""

        if self._loop is not None or self._closed:
            raise CommandError("closed")
        import aiohttp
        self._loop = asyncio.get_running_loop()
        self._client = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=2, limit_per_host=2, ssl=self._ssl),
            timeout=aiohttp.ClientTimeout(total=self._timeout), trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
            max_line_size=2048, max_field_size=2048,
        )
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Один обмен с проверкой ответа и без скрытого retry
    #--------------------------------------------------------------------------------------------------------------
    async def exchange(
        self,
        operation: str,
        message: CommandMessage,
    ) -> CommandMessage | CommandOffer | None:

        """Send one request and return a validated response without retrying it.

        :param operation: Single operation executed within the documented limits.
        :type operation: str

        :param message: Typed request for the selected command operation.
        :type message: CommandMessage

        :return: Validated response, or None when polling found no work.
        :rtype: CommandMessage | CommandOffer | None
        """

        # operation — одна операция в пределах доступной ёмкости.
        # message — типизированное сообщение выбранной командной операции.

        if self._closed or self._client is None or asyncio.get_running_loop() is not self._loop:
            raise CommandError("closed")

        if operation not in OPERATIONS:
            raise CommandError("invalid")

        if self._active >= 2:
            raise CommandError("busy")

        if not self._ssl.check_hostname or self._ssl.verify_mode != ssl.CERT_REQUIRED:
            raise CommandError("unavailable")
        self._active += 1
        # Ошибки HTTP-библиотеки не должны стать удалёнными уведомлениями приложения.
        # За пределами одного обмена сохраняется прежний контекст пользовательского кода.
        context_token = delivery_context.set(True)

        try:
            # Два места нужны для poll и heartbeat. При переполнении нет скрытой очереди.
            async with self._client.post(
                self._endpoint + "/v1/commands/" + operation,
                data=encode_command(message), allow_redirects=False,
                headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"},
            ) as response:
                if response.status == 204 and operation == "poll":
                    return None
                if response.status != 200:
                    code = {401: "denied", 403: "denied", 409: "conflict", 429: "busy"}.get(
                        response.status, "unavailable")
                    raise CommandError(code, http_status=response.status)
                if response.content_type != "application/json" or response.headers.get("Content-Encoding"):
                    raise CommandError("invalid", http_status=response.status)
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) > MAX_HTTP_BYTES:
                        raise CommandError("invalid")
                return decode_response(bytes(body))
        except CommandError:
            raise
        except Exception:
            raise CommandError("unavailable") from None
        finally:
            self._active -= 1
            delivery_context.reset(context_token)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close the HTTP session and prohibit reuse after shutdown."""

        self._closed = True

        if self._client is not None:
            await self._client.close()
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль command_http не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
