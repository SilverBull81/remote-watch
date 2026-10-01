# Получение UTC от выбранного сервиса TimeAPI без изменения часов операционной системы.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-114053
#
# Классы:
# -> TimeApiTimeSource: Ограниченный HTTPS-запрос текущего UTC в формате TimeAPI.
#    Конструктор:
#    -> __init__(): Настройки источника и допустимой погрешности.
#    Интерфейс:
#    -> sample(): Одно показание времени с учётом сетевой задержки.
#    Служебные методы:
#    -> _fetch(): Проверяемое HTTPS-соединение и ограниченное чтение ответа.
#
# Функции:
# -> _unique_object(): Проверка отсутствия повторяющихся полей JSON.
# -> _parse_time(): Проверка UTC и согласованности полей ответа.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import re
import ssl
from collections.abc import Callable
from datetime import datetime, timezone
from secrets import token_hex
from time import monotonic
from urllib.parse import urlencode, urlsplit, urlunsplit

from remote_watch._validation import require_number, require_text
from remote_watch.commands.time import TimeSample, TimeUnavailable


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Время от TimeAPI с обязательной проверкой TLS
#------------------------------------------------------------------------------------------------------------------
class TimeApiTimeSource:
    """Read UTC from TimeAPI under an explicit assumption about its clock accuracy."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        *,
        accuracy: float = 1.0,
        timeout: float = 5.0,
        endpoint: str = "https://timeapi.io/api/time/current/zone",
        ssl_context: ssl.SSLContext | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:

        """Configure one bounded request without opening a connection.

        :param accuracy: Assumed maximum server clock error, not a provider guarantee, in seconds.
        :type accuracy: float

        :param timeout: Total request deadline in seconds.
        :type timeout: float

        :param endpoint: HTTPS endpoint implementing the TimeAPI current-zone response.
        :type endpoint: str

        :param ssl_context: Verifying TLS context, or None for system trust roots.
        :type ssl_context: ssl.SSLContext | None

        :param clock: Local monotonic clock used to measure the entire round trip.
        :type clock: Callable[[], float]
        """

        # accuracy — принятое допущение о погрешности серверных часов, секунды.
        # timeout — общий предел ожидания одного запроса, секунды.
        # endpoint — адрес API; возможность замены нужна также для локальных TLS-тестов.
        # ssl_context — доверенные сертификаты; None означает системные настройки.
        # clock — монотонные часы процесса, не зависящие от корректировки UTC машины.

        require_number(accuracy, "source accuracy", allow_zero=True)
        require_number(timeout, "source timeout")
        if accuracy > 5 or timeout > 30 or not callable(clock):
            raise ValueError("invalid time source limits")

        require_text(endpoint, "time source endpoint", 2048)
        parts = urlsplit(endpoint)
        if (
            parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.fragment or parts.query
            or any(character.isspace() for character in endpoint)
        ):
            raise ValueError("invalid time source endpoint")

        if ssl_context is not None and (
            not isinstance(ssl_context, ssl.SSLContext)
            or ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname
        ):
            raise ValueError("time source requires verified TLS")

        self._endpoint = parts
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

        """Fetch one sample, rejecting concurrent calls and hiding provider error details.

        :return: UTC bounds at the local monotonic response-completion time.
        :rtype: TimeSample
        """

        # Не копим очередь обновлений. Жизненным циклом и периодичностью управляет hub.
        if self._busy:
            raise TimeUnavailable("trusted time unavailable")
        self._busy = True

        try:
            return await asyncio.wait_for(self._fetch(), timeout=self._timeout)
        except Exception:
            raise TimeUnavailable("trusted time unavailable") from None
        finally:
            # Отмена запроса также освобождает объект для последующего обновления.
            self._busy = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Один HTTPS-запрос с ограничением размера и времени
    #--------------------------------------------------------------------------------------------------------------
    async def _fetch(self) -> TimeSample:

        """Fetch and validate one fresh JSON response over authenticated HTTPS.

        :return: Conservative UTC interval including precision and full round-trip delay.
        :rtype: TimeSample
        """

        import aiohttp

        # Случайный параметр и запрет кеширования снижают риск получить старый ответ.
        # Они не заменяют доверие к самому сервису и не доказывают точность его часов.
        query = urlencode({"timeZone": "UTC", "remote_watch_nonce": token_hex(16)})
        url = urlunsplit(self._endpoint._replace(query=query))
        started = self._clock()
        context = self._ssl or ssl.create_default_context()
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise TimeUnavailable("trusted time unavailable")

        # Не принимаем настройки proxy из окружения, cookies и перенаправления.
        # Заголовки и тело ограничены отдельно; распаковка сжатого ответа выключена.
        async with aiohttp.ClientSession(
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(total=self._timeout, ceil_threshold=float("inf")),
            max_line_size=2048,
            max_field_size=2048,
        ) as session:
            async with session.get(
                url,
                ssl=context,
                allow_redirects=False,
                headers={"Cache-Control": "no-cache, no-store", "Accept-Encoding": "identity"},
            ) as response:
                if (
                    response.status != 200 or "Age" in response.headers
                    or response.content_type != "application/json"
                    or response.headers.get("Content-Encoding", "identity") != "identity"
                ):
                    raise TimeUnavailable("trusted time unavailable")

                body = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    body.extend(chunk)
                    if len(body) > 16384:
                        raise TimeUnavailable("trusted time unavailable")

                # Отсчёт заканчивается после всего тела: медленная передача тоже
                # расходует время и не должна искусственно омолаживать команды.
                received = self._clock()
                if received < started or received - started > self._timeout:
                    raise TimeUnavailable("trusted time unavailable")
                stamp, resolution = _parse_time(bytes(body))

                return TimeSample(
                    lower_utc=stamp - self._accuracy,
                    upper_utc=stamp + resolution + self._accuracy + received - started,
                    observed_at=received,
                )
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрет неоднозначных полей ответа JSON
#------------------------------------------------------------------------------------------------------------------
def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:

    """Reject duplicate keys at every object level.

    :param pairs: Object members emitted by the JSON decoder.
    :type pairs: list[tuple[str, object]]

    :return: A mapping with unique keys.
    :rtype: dict[str, object]
    """

    # pairs — поля одного JSON-объекта в исходном порядке.

    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("ambiguous time response")
    return result
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка времени UTC и взаимного соответствия полей
#------------------------------------------------------------------------------------------------------------------
def _parse_time(body: bytes) -> tuple[float, float]:

    """Validate the TimeAPI UTC representation without consulting local wall time.

    :param body: Bounded UTF-8 JSON response bytes.
    :type body: bytes

    :return: UTC timestamp and conservative decimal representation resolution, in seconds.
    :rtype: tuple[float, float]
    """

    # body — уже ограниченное по размеру тело ответа.

    data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    if type(data) is not dict or data.get("timeZone") != "UTC" or data.get("dstActive") is not False:
        raise ValueError("invalid UTC response")

    # TimeAPI возвращает время без суффикса зоны. UTC принимается только после
    # явной проверки timeZone; местная зона машины никогда не участвует в разборе.
    text = data.get("dateTime")
    if type(text) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?", text):
        raise ValueError("invalid UTC response")
    # Python 3.10 принимает только некоторые длины дробной части в fromisoformat.
    # Разбираем целую секунду отдельно, а дробь дополняем/обрезаем до микросекунд.
    whole, _, fraction = text.partition(".")
    date = datetime.fromisoformat(whole).replace(
        microsecond=int((fraction + "000000")[:6]), tzinfo=timezone.utc,
    )

    # Дублирующие числовые поля помогают обнаружить повреждённый или неожиданный
    # ответ API. Bool не допускается вместо числа, хотя в Python это подкласс int.
    expected = {
        "year": date.year, "month": date.month, "day": date.day,
        "hour": date.hour, "minute": date.minute, "seconds": date.second,
        "milliSeconds": date.microsecond // 1000,
    }
    for name, value in expected.items():
        if type(data.get(name)) is not int or data[name] != value:
            raise ValueError("inconsistent UTC response")

    # Python хранит шесть десятичных знаков. Дополнительный знак TimeAPI
    # отбрасывается при разборе; верхняя граница учитывает это округление.
    digits = len(fraction)
    resolution = 10 ** -min(digits, 6)
    return date.timestamp(), resolution
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.adapters.timeapi не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
