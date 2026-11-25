# Получение UTC от независимого сервиса UTCTime с проверкой согласованности полей.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261125-102159
#
# Классы:
# -> UtcTimeSource: Независимый JSON-источник UTC.
#    Конструктор:
#    -> __init__(): Настройка HTTPS-запроса без запуска сети.
#    Служебные методы:
#    -> _parse_body(): Проверка секунд, миллисекунд и строки UTC.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import json
import re
import ssl
from collections.abc import Callable
from datetime import datetime, timezone
from time import monotonic

from remote_watch.adapters.timeapi import TimeApiTimeSource, _unique_object

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Независимый JSON-источник UTC
#------------------------------------------------------------------------------------------------------------------
class UtcTimeSource(TimeApiTimeSource):
    """
    Read UTCTime JSON using an explicit accuracy assumption, without changing OS clocks.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        *,
        accuracy: float = 1.0,
        timeout: float = 2.0,
        endpoint: str = "https://utctime.app/api/now",
        ssl_context: ssl.SSLContext | None = None,
        clock: Callable[[], float] = monotonic,
        ) -> None:

        """
        Reuse strict HTTPS transport with the independent provider's response schema.

        :param accuracy: Assumed server clock error in seconds; not an SLA.
        :type accuracy: float

        :param timeout: Total request deadline in seconds.
        :type timeout: float

        :param endpoint: Explicit HTTPS endpoint implementing UTCTime JSON.
        :type endpoint: str

        :param ssl_context: Verifying TLS context or system trust roots.
        :type ssl_context: ssl.SSLContext | None

        :param clock: Local monotonic clock for round-trip measurement.
        :type clock: Callable[[], float]
        """

        # accuracy — допущение о точности, отдельно от разрешения поля unix_ms.
        # timeout — запрос целиком, включая DNS, TLS и чтение тела.
        # endpoint — явный адрес провайдера; в тестах используется локальный HTTPS.
        # ssl_context — проверка сертификата и имени обязательна.
        # clock — часы процесса; UTC операционной системы не используется.

        super().__init__(accuracy=accuracy, timeout=timeout, endpoint=endpoint,
                         ssl_context=ssl_context, clock=clock)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка секунд, миллисекунд и строки UTC
    #--------------------------------------------------------------------------------------------------------------
    def _parse_body(
        self,
        body: bytes,
        ) -> tuple[float, float]:

        """
        Reject duplicate keys, inconsistent timestamps and ambiguous UTC text.

        :param body: Bounded UTF-8 JSON body after transport validation.
        :type body: bytes

        :return: UTC seconds and millisecond resolution.
        :rtype: tuple[float, float]
        """

        # body — HTTP-проверки выполняет общий транспорт; здесь проверяем само время.

        data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        if type(data) is not dict:
            raise ValueError("invalid UTC response")

        seconds, millis, text = data.get("unix"), data.get("unix_ms"), data.get("utc_iso")
        if (type(seconds) is not int or type(millis) is not int
                or not 0 <= millis <= 253402300799999 or millis // 1000 != seconds
                or type(text) is not str
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)):
            raise ValueError("invalid UTC response")

        stamp = datetime.fromisoformat(text[:-1]).replace(tzinfo=timezone.utc).timestamp()
        if stamp != seconds:
            raise ValueError("inconsistent UTC response")

        # Не приписываем полю unix_ms более высокую точность, чем оно передаёт.
        # Полная сетевая задержка и допущение accuracy добавляются общим транспортом.
        return millis / 1000, 0.001
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.adapters.utctime не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
