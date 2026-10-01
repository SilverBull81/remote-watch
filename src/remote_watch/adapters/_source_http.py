# Ограниченный HTTP-обмен для приёма команд и отправки ответов.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-184110
#
# Классы:
# -> SourceHttp: Два ограниченных сетевых слота одного получателя.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие ресурсов и проверка владения.
#    -> call(): Один ограниченный HTTP-запрос без скрытых повторов.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.
#    -> defer(): Пауза по требованию провайдера на монотонных часах.
#
# Функции:
# -> _json(): Строгий разбор ограниченного JSON провайдера.
# -> _pairs(): Отказ от повторяющихся ключей JSON.
# -> _constant(): Отказ от нечисловых JSON-констант.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import ipaddress
import json
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from remote_watch.adapters._common import validate_endpoint
from remote_watch.commands.transport import CommandError
from remote_watch.notifications._context import delivery_context


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Два ограниченных сетевых слота одного получателя
#------------------------------------------------------------------------------------------------------------------
class SourceHttp:
    """Own two bounded HTTPS slots for provider polling and replies without implicit retries."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        endpoint: str,
        *,
        allow_loopback_http: bool = False,
    ) -> None:

        """Validate a fixed provider origin while deferring optional imports and network resources.

        :param endpoint: HTTPS origin without credentials, query or path.
        :type endpoint: str

        :param allow_loopback_http: Explicit permission for plaintext numeric loopback only.
        :type allow_loopback_http: bool
        """

        # endpoint — адрес HTTPS без секрета, параметров и пути операции.
        # allow_loopback_http — явное разрешение HTTP только на числовом loopback-адресе.

        validate_endpoint(endpoint, allow_loopback_http)
        parts = urlsplit(endpoint)

        if parts.scheme == "http" and (
            not allow_loopback_http or not ipaddress.ip_address(parts.hostname).is_loopback
        ):
            raise ValueError("provider TLS required")
        self.endpoint = endpoint.rstrip("/")
        self._client: Any = None
        self._active = 0
        self._blocked_until = 0.0
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов и проверка владения
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Create a lazy aiohttp session with finite waits, no cookies and no environment proxies."""

        import aiohttp

        if self._client is not None:
            raise CommandError("closed")
        connector = aiohttp.TCPConnector(limit=2, limit_per_host=2, force_close=True)

        try:
            self._client = aiohttp.ClientSession(connector=connector, trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
                headers={"Accept-Encoding": "identity"},
                timeout=aiohttp.ClientTimeout(total=25, connect=5, ceil_threshold=float("inf")))
        except BaseException:
            await connector.close()
            raise
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Один ограниченный HTTP-запрос без скрытых повторов
    #--------------------------------------------------------------------------------------------------------------
    async def call(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        token: str | None = None,
        stream: bool = False,
        privacy_probe: bool = False,
    ) -> tuple[int, Any]:

        """Make one bounded request and retain only a fixed private-safe failure classification.

        :param method: Fixed HTTP method or Bot API operation.
        :type method: str

        :param path: Explicit local data path or fixed HTTP operation path.
        :type path: str

        :param payload: Bounded JSON body or a serialized pending decision.
        :type payload: dict[str, Any] | None

        :param params: Explicit nonsecret HTTP query parameters.
        :type params: dict[str, str] | None

        :param token: Command-only credential, never logged or echoed.
        :type token: str | None

        :param stream: Whether to read bounded newline-delimited provider events.
        :type stream: bool

        :param privacy_probe: Whether only anonymous access denial is being verified.
        :type privacy_probe: bool

        :return: HTTP status and a bounded decoded response.
        :rtype: tuple[int, Any]
        """

        # method — явный HTTP-метод либо операция Bot API.
        # path — явный путь локальных данных либо фиксированный путь HTTP-операции.
        # payload — ограниченное тело JSON либо записанное решение источника.
        # params — явные параметры HTTP-запроса без секретов.
        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # stream — читать ли ограниченные события, разделённые переводами строк.
        # privacy_probe — проверяется ли только запрет анонимного доступа.

        if self._client is None:
            raise CommandError("closed")

        if self._active >= 2 or monotonic() < self._blocked_until:
            raise CommandError("busy")
        self._active += 1
        headers = {} if token is None else {"Authorization": "Bearer " + token}
        opened_stream = False
        # Подавляем только внутренние сетевые записи. Контекст callback приложения
        # этим не меняется: он выполняется отдельно в dispatcher.
        context_token = delivery_context.set(True)

        try:
            async with self._client.request(method, self.endpoint + path, json=payload, params=params,
                                            headers=headers, allow_redirects=False) as response:
                status = response.status
                if privacy_probe:
                    if status not in (401, 403):
                        raise CommandError("denied")
                    return status, None
                if status == 429:
                    delay = response.headers.get("Retry-After", "2")
                    seconds = int(delay) if delay.isdecimal() and len(delay) <= 5 else 2
                    # Bot API задаёт retry_after в JSON, а не обязательно в HTTP-заголовке.
                    body = await response.content.read(8193)
                    while not response.content.at_eof() and len(body) <= 8192:
                        body += await response.content.read(8193 - len(body))
                    if len(body) <= 8192:
                        try:
                            value = _json(body)
                            extra = value.get("parameters", {}).get("retry_after")
                            if type(extra) is int and extra > 0:
                                seconds = max(seconds, extra)
                        except (CommandError, AttributeError):
                            pass
                    self.defer(seconds)
                    raise CommandError("busy")
                if status in (401, 403):
                    raise CommandError("denied")
                if status == 409:
                    raise CommandError("conflict")
                if status >= 500:
                    raise CommandError("unavailable")
                if status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                    raise CommandError("invalid")
                if response.headers.get("X-Messages-Truncated") == "1":
                    raise CommandError("capacity")
                if stream:
                    values = []
                    total = 0
                    # Берём первую ограниченную порцию, закрывая HTTP после неё.
                    # Следующий запрос продолжится от последнего сохранённого ID.
                    async for line in response.content:
                        total += len(line)
                        if total > 262144 or len(line) > 65536:
                            raise CommandError("capacity")
                        if line.strip():
                            value = _json(line)
                            values.append(value)
                            opened_stream = type(value) is dict and value.get("event") in ("open", "keepalive")
                        if len(values) >= 32 or (values and type(values[-1]) is dict
                                                and values[-1].get("event") == "message"):
                            break
                    return status, values
                body = await response.content.read(262145)
                # read(n) может вернуть неполную порцию; дочитываем до EOF в том же лимите.
                while not response.content.at_eof() and len(body) <= 262144:
                    body += await response.content.read(262145 - len(body))
                if len(body) > 262144:
                    raise CommandError("capacity")
                return status, _json(body)
        except (CommandError, asyncio.CancelledError):
            raise
        except asyncio.TimeoutError:
            if stream and opened_stream:
                return 200, []
            raise CommandError("unavailable") from None
        except Exception:
            raise CommandError("unavailable") from None
        finally:
            self._active -= 1
            delivery_context.reset(context_token)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close all connections belonging to this provider reader."""

        if self._client is not None:
            await self._client.close()
            self._client = None
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Пауза по требованию провайдера на монотонных часах
    #--------------------------------------------------------------------------------------------------------------
    def defer(
        self,
        seconds: int,
    ) -> None:

        """Honor a bounded provider retry delay on the monotonic clock.

        :param seconds: Requested bounded provider retry delay in seconds.
        :type seconds: int
        """

        # seconds — запрошенная провайдером конечная пауза, секунды.

        self._blocked_until = max(self._blocked_until, monotonic() + max(2, min(seconds, 86400)))
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Строгий разбор ограниченного JSON провайдера
#------------------------------------------------------------------------------------------------------------------
def _json(body: bytes) -> Any:

    """Decode provider JSON with duplicate-key and nonfinite-number rejection.

    :param body: Bounded provider response bytes.
    :type body: bytes

    :return: HTTP response with a bounded body and fixed status semantics.
    :rtype: Any
    """

    # body — байты ответа провайдера с ограниченным размером.

    try:
        return json.loads(body, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CommandError("invalid") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Отказ от повторяющихся ключей JSON
#------------------------------------------------------------------------------------------------------------------
def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:

    """Reject ambiguous repeated fields at every JSON nesting level.

    :param pairs: Field pairs from one JSON object.
    :type pairs: list[tuple[str, Any]]

    :return: Validated configuration, synthetic provider object or aggregate service counters.
    :rtype: dict[str, Any]
    """

    # pairs — пары полей одного JSON-объекта, включая возможные повторы.

    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate provider key")
        result[key] = value
    return result
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Отказ от нечисловых JSON-констант
#------------------------------------------------------------------------------------------------------------------
def _constant(value: str) -> None:

    """Reject nonfinite JSON constants without echoing provider content.

    :param value: Untrusted configuration or provider value under validation.
    :type value: str
    """

    # value — проверяемое значение настроек либо ответа провайдера.

    raise ValueError("invalid provider constant")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль _source_http не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
