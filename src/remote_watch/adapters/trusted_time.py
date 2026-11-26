# Ограниченное получение UTC из нескольких явно доверенных HTTPS-источников.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261126-232526
#
# Классы:
# -> TimeSourceConfigError: Известное поле и причина ошибки настроек UTC.
#    Конструктор:
#    -> __init__(): Сохранение только пути в схеме и категории.
#
# -> TimeSourceSettings: Явные настройки одного источника времени.
#    Интерфейс:
#    -> build(): Создание источника без запуска I/O.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек без сетевых запросов.
#
# -> RedundantTimeSource: Получение согласованного времени с резервированием.
#    Конструктор:
#    -> __init__(): Подготовка конечного набора источников.
#    Интерфейс:
#    -> sample(): Получение и сопоставление независимых показаний.
#    -> stats(): Копия безопасной диагностики последнего обновления.
#    Служебные методы:
#    -> _one(): Один ограниченный запрос и проверка его погрешности.
#
# Функции:
# -> parse_time_sources(): Строгая проверка раздела trusted_time.
#
# -> _time_source_row(): Проверка одного источника и привязка ошибки к его номеру.
#
# -> validate_time_health(): Проверка диагностического снимка перед IPC.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from remote_watch._validation import require_number
from remote_watch.adapters.time_source import HttpsDateTimeSource
from remote_watch.adapters.timeapi import TimeApiTimeSource, time_failure
from remote_watch.adapters.utctime import UtcTimeSource
from remote_watch.commands.time import TimeSample, TimeSource, TimeUnavailable, TrustedClock

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

TIME_ERRORS = frozenset({"source_failed", "timeout", "dns", "connect", "tls_certificate", "tls",
    "http_status", "invalid_response", "uncertainty", "source_conflict", "busy", "sources_unavailable"})

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Известное поле и причина ошибки настроек UTC
#------------------------------------------------------------------------------------------------------------------
class TimeSourceConfigError(ValueError):
    """
    Carry a schema path and a fixed reason without including the configured value.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        field: str,
        reason: str,
        ) -> None:

        """
        Record the diagnostic supplied by the trusted-time validator.

        :param field: Known relative schema path.
        :type field: str

        :param reason: Fixed validation reason.
        :type reason: str
        """

        # field — формируется валидатором, а не берётся из ключей файла.
        # reason — фиксированная категория, которую переведёт CLI.

        self.field = field
        self.reason = reason
        self.related: tuple[TimeSourceConfigError, ...] = ()
        self.entry_indices: tuple[int, ...] = ()
        super().__init__("invalid time source configuration")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Явные настройки одного источника времени
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class TimeSourceSettings:
    """
    Describe an explicitly trusted provider and its assumed accuracy.

    :param provider: Built-in JSON provider or explicitly configured HTTPS Date origin.
    :type provider: str

    :param accuracy: Assumed server clock error in seconds, without an SLA claim.
    :type accuracy: float

    :param timeout: Finite request budget, at most three seconds.
    :type timeout: float

    :param endpoint: Optional explicit HTTPS endpoint; mandatory for https_date.
    :type endpoint: str | None
    """

    provider: str                    # Формат ответа: utctime, timeapi либо https_date.
    accuracy: float = 1.0            # Допущение о погрешности серверных часов, секунды.
    timeout: float = 2.0             # Весь запрос: DNS, TLS, заголовки и тело.
    endpoint: str | None = None      # Явно доверенный адрес; в diagnostics не выводится.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Создание источника без запуска I/O
    #--------------------------------------------------------------------------------------------------------------
    def build(
        self,
        ) -> TimeSource:

        """
        Construct one lazy provider using mandatory TLS and explicit accuracy.

        :return: Configured source without an active connection.
        :rtype: TimeSource
        """

        if self.provider == "https_date":
            return HttpsDateTimeSource(self.endpoint, accuracy=self.accuracy, timeout=self.timeout)

        options = {"accuracy": self.accuracy, "timeout": self.timeout}
        if self.endpoint is not None:
            options["endpoint"] = self.endpoint
        return UtcTimeSource(**options) if self.provider == "utctime" else TimeApiTimeSource(**options)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек без сетевых запросов
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(
        self,
        ) -> None:

        """
        Reject unknown providers and excessive budgets before constructing any listener.
        """

        if type(self.provider) is not str or self.provider not in {"utctime", "timeapi", "https_date"}:
            raise TimeSourceConfigError("provider", "time_provider")
        try:
            require_number(self.timeout, "time timeout")
            if not 0.1 <= self.timeout <= 3:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise TimeSourceConfigError("timeout", "time_timeout") from None

        try:
            require_number(self.accuracy, "time accuracy", allow_zero=True)
            if self.accuracy > 2:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise TimeSourceConfigError("accuracy", "time_accuracy") from None

        if self.endpoint is None and self.provider != "https_date":
            endpoint = ("https://utctime.app/api/now" if self.provider == "utctime"
                        else "https://timeapi.io/api/time/current/zone")
            object.__setattr__(self, "endpoint", endpoint)

        # Конструкторы проверяют HTTPS и отсутствие credentials, но не запускают сеть.
        try:
            self.build()
        except (TypeError, ValueError, OverflowError):
            raise TimeSourceConfigError("endpoint", "time_endpoint") from None
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Получение согласованного времени с резервированием
#------------------------------------------------------------------------------------------------------------------
class RedundantTimeSource:
    """
    Query at most four trusted origins concurrently; fail closed on incompatible usable samples.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        settings: tuple[TimeSourceSettings, ...] | None = None,
        ) -> None:

        """
        Configure independent JSON defaults without opening connections.

        :param settings: Explicit trusted providers; None selects UTCTime and TimeAPI.
        :type settings: tuple[TimeSourceSettings, ...] | None
        """

        # settings — список заменяет defaults целиком; дополнений из окружения нет.

        self.settings = parse_time_sources(None) if settings is None else settings
        if (type(self.settings) is not tuple or not 1 <= len(self.settings) <= 4
                or any(type(item) is not TimeSourceSettings for item in self.settings)):
            raise ValueError("invalid time sources")
        if len({(item.provider, item.endpoint) for item in self.settings}) != len(self.settings):
            raise ValueError("duplicate time sources")

        self._sources = tuple(item.build() for item in self.settings)
        self._busy = False
        self._health = {"selected": None, "refresh_error": None, "sources": []}
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Получение и сопоставление независимых показаний
    #--------------------------------------------------------------------------------------------------------------
    async def sample(
        self,
        ) -> TimeSample:

        """
        Accept a usable source, but reject disagreement between concurrently usable origins.

        :return: Original interval from the first agreeing source, with its original receipt time.
        :rtype: TimeSample
        """

        if self._busy:
            raise TimeUnavailable(reason="busy", transient=True)

        self._busy = True
        try:
            # Все запросы имеют собственный конечный timeout. Общая задержка не равна
            # сумме timeout, а отмена владельца отменяет и эти запросы без фоновых хвостов.
            outcomes = await asyncio.gather(*(self._one(index) for index in range(len(self._sources))))
            self._health = {"selected": None, "refresh_error": None,
                            "sources": [row for _, row, _ in outcomes]}
            usable = []
            for index, (sample, _, _) in enumerate(outcomes):
                if sample is None:
                    continue
                clock = TrustedClock()
                try:
                    clock.install(sample)
                    usable.append((index, sample, clock.bounds()))
                except TimeUnavailable:
                    self._health["sources"][index]["error_kind"] = "uncertainty"

            if not usable:
                self._health["refresh_error"] = "sources_unavailable"
                raise TimeUnavailable(transient=all(transient for _, _, transient in outcomes))

            # Приводим интервалы к одному монотонному моменту с тем же допущением дрейфа,
            # что и TrustedClock. Усреднение и выбор большинства здесь не применяются.
            now = monotonic()
            lower, upper = [], []
            for _, _, sample in usable:
                elapsed = now - sample.observed_at
                lower.append(sample.lower_utc + elapsed * (1 - 100 / 1e6))
                upper.append(sample.upper_utc + elapsed * (1 + 100 / 1e6))
            if max(lower) > min(upper):
                self._health["refresh_error"] = "source_conflict"
                raise TimeUnavailable(reason="source_conflict")

            selected, sample, _ = usable[0]
            self._health["selected"] = selected + 1
            return sample
        finally:
            self._busy = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Копия безопасной диагностики последнего обновления
    #--------------------------------------------------------------------------------------------------------------
    def stats(
        self,
        ) -> dict:

        """
        Read cached fixed diagnostics without network activity or private endpoint names.

        :return: Validated independent snapshot of the last completed refresh.
        :rtype: dict
        """

        return validate_time_health(self._health)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Один ограниченный запрос и проверка его погрешности
    #--------------------------------------------------------------------------------------------------------------
    async def _one(
        self,
        index: int,
        ) -> tuple[TimeSample | None, dict, bool]:

        """
        Classify one request and reject excessive uncertainty before choosing a fallback.

        :param index: Internal zero-based provider position.
        :type index: int

        :return: Usable sample, safe diagnostics and transient failure flag.
        :rtype: tuple[TimeSample | None, dict, bool]
        """

        # index — в отчёте выводится только номер, без URL, имени VM или тела ответа.

        started = monotonic()
        row = {"index": index + 1, "provider": self.settings[index].provider, "error_kind": None,
               "http_status": None, "verify_code": None, "elapsed_ms": 0, "uncertainty_ms": None}
        sample, transient = None, True
        try:
            candidate = await asyncio.wait_for(self._sources[index].sample(), self.settings[index].timeout)
            clock = TrustedClock()
            try:
                clock.install(candidate)
            except TimeUnavailable:
                if clock.unavailable_reason == "uncertainty":
                    raise TimeUnavailable(reason="uncertainty", transient=True) from None
                raise TimeUnavailable(reason="invalid_response") from None
            row["uncertainty_ms"] = min(10000, int((candidate.upper_utc - candidate.lower_utc) * 1000))
            sample = candidate
        except Exception as error:
            failure = time_failure(error)
            row.update(error_kind=failure.reason, http_status=failure.http_status, verify_code=failure.verify_code)
            transient = failure.transient
        row["elapsed_ms"] = min(10000, int((monotonic() - started) * 1000))
        return sample, row, transient
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Строгая проверка раздела trusted_time
#------------------------------------------------------------------------------------------------------------------
def parse_time_sources(
    value: object,
    ) -> tuple[TimeSourceSettings, ...]:

    """
    Validate an explicit bounded provider list or select independent JSON defaults.

    :param value: Optional trusted_time JSON object.
    :type value: object

    :return: Immutable validated source settings.
    :rtype: tuple[TimeSourceSettings, ...]
    """

    # value — значения адресов никогда не включаются в текст ошибки.

    if value is None:
        return (TimeSourceSettings(provider="utctime"), TimeSourceSettings(provider="timeapi"))
    if type(value) is not dict:
        raise TimeSourceConfigError("trusted_time", "config_object")
    if "sources" not in value:
        raise TimeSourceConfigError("trusted_time.sources", "config_missing")
    if set(value) != {"sources"}:
        error = TimeSourceConfigError("trusted_time", "config_unknown")
        error.entry_indices = tuple(index for index, name in enumerate(value) if name != "sources")[:8]
        raise error
    rows = value["sources"]
    if type(rows) is not list or not 1 <= len(rows) <= 4:
        raise TimeSourceConfigError("trusted_time.sources", "time_sources")

    result = []
    errors = []
    seen = set()
    for index, row in enumerate(rows):
        section = f"trusted_time.sources[{index}]"
        try:
            item = _time_source_row(row, section)
        except TimeSourceConfigError as error:
            errors.append(error)
            continue

        marker = (item.provider, item.endpoint)
        if marker in seen:
            errors.append(TimeSourceConfigError(section, "duplicate_time_source"))
            continue
        seen.add(marker)
        result.append(item)

    # Ошибки разных источников независимы: оператор может исправить их за один проход.
    # Список конечен, адреса и значения не входят в итоговое исключение.
    if errors:
        errors[0].related = tuple(errors[1:])
        raise errors[0] from None

    return tuple(result)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка одного источника и привязка ошибки к его номеру
#------------------------------------------------------------------------------------------------------------------
def _time_source_row(
    row: object,
    section: str,
    ) -> TimeSourceSettings:

    """
    Validate one independently configured UTC provider.

    :param row: JSON element from the bounded list of sources.
    :type row: object

    :param section: Fixed schema path with the element index.
    :type section: str

    :return: Validated settings for a single source.
    :rtype: TimeSourceSettings
    """

    # row — данные одного источника, его адрес и значения не идут в исключение.
    # section — номер элемента вычисляется валидатором.

    if type(row) is not dict:
        raise TimeSourceConfigError(section, "config_object")
    if "provider" not in row:
        raise TimeSourceConfigError(section + ".provider", "config_missing")

    known = {"provider", "accuracy", "timeout", "endpoint"}
    if row.keys() - known:
        error = TimeSourceConfigError(section, "config_unknown")
        error.entry_indices = tuple(index for index, name in enumerate(row) if name not in known)[:8]
        raise error

    if row["provider"] == "https_date":
        if "endpoint" not in row:
            raise TimeSourceConfigError(section + ".endpoint", "time_endpoint")
        if "accuracy" not in row:
            raise TimeSourceConfigError(section + ".accuracy", "time_accuracy")
    try:
        return TimeSourceSettings(**row)
    except TimeSourceConfigError as error:
        raise TimeSourceConfigError(section + "." + error.field, error.reason) from None
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка диагностического снимка перед IPC
#------------------------------------------------------------------------------------------------------------------
def validate_time_health(
    value: object,
    ) -> dict:

    """
    Reject arbitrary strings, endpoint names and unbounded numbers before status serialization.

    :param value: Untrusted component diagnostic snapshot.
    :type value: object

    :return: Independent allowlisted snapshot.
    :rtype: dict
    """

    # value — через границу процесса проходят только фиксированные поля и категории.

    if (type(value) is not dict or set(value) != {"selected", "refresh_error", "sources"}
            or type(value["sources"]) is not list or len(value["sources"]) > 4):
        raise ValueError("invalid time health")
    selected, error = value["selected"], value["refresh_error"]
    if ((selected is not None and (type(selected) is not int or not 1 <= selected <= len(value["sources"])))
            or (error is not None and (type(error) is not str or error not in TIME_ERRORS))):
        raise ValueError("invalid time health")

    rows = []
    for index, row in enumerate(value["sources"], 1):
        if (type(row) is not dict or set(row) != {"index", "provider", "error_kind", "http_status",
                                                "verify_code", "elapsed_ms", "uncertainty_ms"}
                or type(row["index"]) is not int or row["index"] != index
                or type(row["provider"]) is not str or row["provider"] not in {"utctime", "timeapi", "https_date"}
                or (row["error_kind"] is not None
                    and (type(row["error_kind"]) is not str or row["error_kind"] not in TIME_ERRORS))):
            raise ValueError("invalid time source health")
        for key, maximum in (("http_status", 599), ("verify_code", 1000), ("elapsed_ms", 10000),
                             ("uncertainty_ms", 10000)):
            number = row[key]
            if number is not None and (type(number) is not int or not 0 <= number <= maximum):
                raise ValueError("invalid time source health")
        rows.append(dict(row))
    return {"selected": selected, "refresh_error": error, "sources": rows}
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.adapters.trusted_time не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
