# Управляемый HTTP-клиент для одной попытки отправки без скрытых повторов.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-222548
#
# Классы:
# -> HttpSender: HTTP-клиент с ограниченным чтением ответа.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Подготовка клиента и данных авторизации.
#    -> post(): Один HTTP POST без скрытых повторов.
#    -> close(): Закрытие клиента и освобождение ресурсов.
#    Служебные методы:
#    -> _check_loop(): Проверка принадлежности текущему циклу asyncio.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from .._validation import require_int
from ..config import RetryPolicy
from ..delivery import DeliveryResult, DeliveryStatus

if TYPE_CHECKING:
    import aiohttp


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : HTTP-клиент с ограниченным чтением ответа
#------------------------------------------------------------------------------------------------------------------
class HttpSender:
    """Own a lazy aiohttp session, bounded responses and cancellation-safe requests."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        policy: RetryPolicy,
        *,
        response_limit: int = 65536,
        json_decoder: Callable[[bytes], object] = json.loads,
        ) -> None:

        """Store request budgets without importing or creating a network client.

        :param policy: Timeout and retry policy.
        :type policy: RetryPolicy

        :param response_limit: Maximum response body size in bytes.
        :type response_limit: int

        :param json_decoder: Bounded JSON decoder, optionally rejecting duplicate fields.
        :type json_decoder: Callable[[bytes], object]
        """

        # policy - политика времени ожидания и повторов.
        # response_limit - предел размера ответа до разбора JSON.
        # json_decoder - выбранный разборщик тела ответа без вывода его содержимого.

        if not isinstance(policy, RetryPolicy):
            raise TypeError("retry must be RetryPolicy")
        require_int(response_limit, "response_limit")
        if not callable(json_decoder):
            raise TypeError("json_decoder must be callable")

        self._policy = policy
        self._response_limit = response_limit
        self._json_decoder = json_decoder
        self._client: aiohttp.ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка клиента и данных авторизации
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Create a session in its owning loop without sending a health request."""

        if self._closed:
            raise RuntimeError("a closed channel cannot be reopened")

        self._check_loop()
        if self._client is not None:
            return

        try:
            import aiohttp
        except ImportError:
            raise ImportError(
                "install remote-watch[telegram], remote-watch[ntfy] or remote-watch[relay] to send notifications",
            ) from None

        # Один канал обрабатывается последовательно. Cookies, proxy из окружения и распаковка не нужны.
        # Запрет распаковки позволяет ограничить размер ответа до выделения памяти под большой JSON.
        self._loop = asyncio.get_running_loop()
        connector = aiohttp.TCPConnector(limit=1, limit_per_host=1)
        try:
            self._client = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(
                    total=self._policy.attempt_timeout,
                    connect=self._policy.connect_timeout,
                    sock_connect=self._policy.connect_timeout,
                    ceil_threshold=float("inf"),
                ),
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                auto_decompress=False,
                headers={"Accept-Encoding": "identity"},
            )
        except Exception:
            # Сессия ещё не приняла владение connector: освобождаем его сами при ошибке создания.
            await connector.close()
            raise RuntimeError("HTTP client could not be created") from None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Один HTTP POST без скрытых повторов
    #--------------------------------------------------------------------------------------------------------------
    async def post(
        self,
        url: str,
        payload: dict[str, object],
        token: str | None = None,
        ) -> tuple[int, dict[str, str], object] | DeliveryResult:

        """Send exactly one POST with bounded response storage and sanitized failures.

        :param url: Fixed request URL.
        :type url: str

        :param payload: JSON request fields.
        :type payload: dict[str, object]

        :param token: Optional bearer credential.
        :type token: str | None

        :return: Bounded HTTP reply or sanitized transport failure.
        :rtype: tuple[int, dict[str, str], object] | DeliveryResult
        """

        # url - заданный адрес запроса.
        # payload - данные JSON-запроса.
        # token - токен Bearer либо отсутствие авторизации.

        self._check_loop()
        if self._client is None:
            raise RuntimeError("channel is not open")

        import aiohttp

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            # Только POST: aiohttp не повторяет неидемпотентные запросы при обрыве keep-alive.
            # Redirect не выполняется даже в пределах того же host: получатель всегда задан настройкой.
            async with self._client.post(url, json=payload, headers=headers, allow_redirects=False) as response:
                data = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    if len(data) + len(chunk) > self._response_limit:
                        return DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="response_too_large")
                    data.extend(chunk)

                try:
                    body = self._json_decoder(bytes(data))
                except (ValueError, UnicodeError, RecursionError):
                    body = None
                return response.status, {"retry-after": response.headers.get("Retry-After", "")}, body
        except asyncio.CancelledError:
            # Отменой владеет runtime; нельзя превратить её в новую попытку внутри адаптера.
            raise
        except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientConnectorSSLError):
            return DeliveryResult(status=DeliveryStatus.PERMANENT_FAILURE, reason_code="tls_certificate")
        except aiohttp.ClientConnectorError:
            return DeliveryResult(status=DeliveryStatus.TRANSIENT_FAILURE, reason_code="connect_failed")
        except aiohttp.ConnectionTimeoutError:
            return DeliveryResult(status=DeliveryStatus.TRANSIENT_FAILURE, reason_code="connect_timeout")
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
            # После начала записи сервер мог принять сообщение. Повтор допустим, но возможен дубликат.
            return DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="transport_unknown")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие клиента и освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Release the session in its owning loop; repeated cleanup is harmless."""

        self._check_loop()
        self._closed = True
        if self._client is not None:
            # Ссылку очищаем после await: отменённую очистку разрешено повторить.
            await self._client.close()
            self._client = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка принадлежности текущему циклу asyncio
    #--------------------------------------------------------------------------------------------------------------
    def _check_loop(self) -> None:

        """Reject use from a different event loop before touching the session."""

        if self._loop is not None and self._loop is not asyncio.get_running_loop():
            raise RuntimeError("channel must be used in its owning event loop")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.adapters._http не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
