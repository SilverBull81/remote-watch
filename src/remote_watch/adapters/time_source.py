# Необязательный HTTPS-источник времени с проверкой TLS и ограничением ожидания.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Классы:
# -> HttpsDateTimeSource: Получение времени от явно доверенного HTTPS-сервера.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> sample(): Получение одного показания времени.
#    Служебные методы:
#    -> _fetch(): Один ограниченный HTTPS-запрос времени.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import ssl
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from secrets import token_hex
from time import monotonic
from urllib.parse import urlencode, urlsplit, urlunsplit

from remote_watch._validation import require_number, require_text
from remote_watch.commands.time import TimeSample, TimeUnavailable


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Получение времени от явно доверенного HTTPS-сервера
#------------------------------------------------------------------------------------------------------------------
class HttpsDateTimeSource:
    """Read Date from an explicitly trusted HTTPS origin; never infer its accuracy."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        url: str,
        *,
        accuracy: float,
        timeout: float = 5.0,
        ssl_context: ssl.SSLContext | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:

        """Configure explicit clock accuracy, bounded I/O and mandatory TLS verification.

        :param url: Explicit trusted HTTPS origin without credentials or query.
        :type url: str

        :param accuracy: Assumed maximum origin clock error in seconds.
        :type accuracy: float

        :param timeout: Total request timeout in seconds.
        :type timeout: float

        :param ssl_context: Verifying TLS context or default trust roots.
        :type ssl_context: ssl.SSLContext | None

        :param clock: Local monotonic clock, injectable for deterministic tests.
        :type clock: Callable[[], float]
        """

        # url — адрес выбранного доверенного сервера.
        # accuracy — заявленная максимальная погрешность источника.
        # timeout — предельное время одного запроса.
        # ssl_context — проверяющий TLS-контекст или системное доверие.
        # clock — локальный монотонный отсчёт времени.

        require_text(url, "time source URL", 2048)
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment or parts.query or any(c.isspace() for c in url)):
            raise ValueError("invalid time source URL")
        require_number(accuracy, "source accuracy", allow_zero=True)
        require_number(timeout, "source timeout")
        if accuracy > 5 or timeout > 30 or not callable(clock):
            raise ValueError("invalid time source limits")
        if ssl_context is not None and (not isinstance(ssl_context, ssl.SSLContext)
                                        or ssl_context.verify_mode != ssl.CERT_REQUIRED
                                        or not ssl_context.check_hostname):
            raise ValueError("time source requires verified TLS")
        self._url = parts
        self._accuracy = accuracy
        self._timeout = timeout
        self._ssl = ssl_context
        self._clock = clock
        self._busy = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Получение одного показания времени
    #--------------------------------------------------------------------------------------------------------------
    async def sample(self) -> TimeSample:

        """Fetch one uncached HEAD response with no redirects, retries or environment proxy.

        :return: UTC bounds associated with a local monotonic observation.
        :rtype: TimeSample
        """

        if self._busy:
            raise TimeUnavailable("trusted time unavailable")
        self._busy = True
        try:
            return await asyncio.wait_for(self._fetch(), timeout=self._timeout)
        except Exception:
            raise TimeUnavailable("trusted time unavailable") from None
        finally:
            self._busy = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Один ограниченный HTTPS-запрос времени
    #--------------------------------------------------------------------------------------------------------------
    async def _fetch(self) -> TimeSample:

        """Bound response headers and translate second-resolution Date to an interval.

        :return: UTC bounds associated with a local monotonic observation.
        :rtype: TimeSample
        """

        import aiohttp

        # Уникальный URL и запрет кеша относятся к явно выбранному серверу источника.
        # Date произвольного сайта не является доказательством точности его часов.
        url = urlunsplit(self._url._replace(query=urlencode({"remote_watch_nonce": token_hex(16)})))
        started = self._clock()
        context = self._ssl or ssl.create_default_context()
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise TimeUnavailable("trusted time unavailable")
        async with aiohttp.ClientSession(
            trust_env=False, cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
            timeout=aiohttp.ClientTimeout(total=self._timeout, ceil_threshold=float("inf")),
            max_line_size=2048, max_field_size=2048,
        ) as session:
            async with session.head(
                url, ssl=context, allow_redirects=False,
                headers={"Cache-Control": "no-cache, no-store", "Accept-Encoding": "identity"},
            ) as response:
                received = self._clock()
                dates = response.headers.getall("Date", [])
                if (response.status not in (200, 204) or len(dates) != 1 or "Age" in response.headers
                        or received < started or received - started > self._timeout):
                    raise TimeUnavailable("trusted time unavailable")
                date = parsedate_to_datetime(dates[0])
                if date.tzinfo is None or date.utcoffset().total_seconds() != 0:
                    raise TimeUnavailable("trusted time unavailable")

                # Весь RTT прибавляется к верхней границе, плюс секунда точности Date.
                # Поэтому сетевое ожидание не позволяет считать старое сообщение новым.
                stamp = date.timestamp()
                return TimeSample(lower_utc=stamp - self._accuracy,
                                  upper_utc=stamp + 1 + self._accuracy + received - started,
                                  observed_at=received)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.adapters.time_source не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
