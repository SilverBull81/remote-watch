# Проверка возраста команд по достоверному времени без зависимости от часов VM.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Классы:
# -> TimeUnavailable: Отказ при отсутствии достоверного времени.
#
# -> TimeSample: Границы UTC в момент получения показания.
#    Специальные методы:
#    -> __post_init__(): Проверка типов и согласованности полей.
#
# -> TimeSource: Контракт источника достоверного времени.
#    Интерфейс:
#    -> sample(): Получение одного показания времени.
#
# -> FreshnessPolicy: Ограничения возраста и точности времени.
#    Специальные методы:
#    -> __post_init__(): Проверка типов и согласованности полей.
#
# -> FreshnessReason: Причина отказа до принятия команды.
#
# -> FreshnessDecision: Результат проверки возраста команды.
#    Специальные методы:
#    -> __post_init__(): Проверка типов и согласованности полей.
#
# -> TrustedClock: Локальный отсчёт времени на основе доверенного источника.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> install(): Проверка и установка нового показания.
#    -> refresh(): Явное обновление времени из источника.
#    -> bounds(): Текущий интервал UTC с учётом дрейфа.
#    -> check(): Проверка отметки сообщения и оставшегося срока.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Protocol

from remote_watch._validation import require_int, require_number
from remote_watch.commands.protocol import MAX_COMMAND_SECONDS
from remote_watch.commands.state import CommandDeadline


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Отказ при отсутствии достоверного времени
#------------------------------------------------------------------------------------------------------------------
class TimeUnavailable(RuntimeError):
    """Report unavailable trusted time without exposing provider details."""
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Границы UTC в момент получения показания
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class TimeSample:
    """Bound UTC at one local monotonic instant; never persist this anchor."""

    lower_utc: float        # Нижняя граница UTC при получении ответа, секунды Unix.
    upper_utc: float        # Верхняя граница с учётом погрешности и задержки ответа.
    observed_at: float      # Момент получения на локальных монотонных часах.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка типов и согласованности полей
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate finite ordered bounds."""

        for value in (self.lower_utc, self.upper_utc, self.observed_at):
            require_number(value, "time sample", allow_zero=True)
        if self.lower_utc > self.upper_utc:
            raise ValueError("invalid time sample")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Контракт источника достоверного времени
#------------------------------------------------------------------------------------------------------------------
class TimeSource(Protocol):
    """Provide a fresh authenticated UTC interval in the caller's monotonic domain."""

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Получение одного показания времени
    #--------------------------------------------------------------------------------------------------------------
    async def sample(self) -> TimeSample:

        """Obtain a bounded fresh sample or raise TimeUnavailable.

        :return: UTC bounds associated with a local monotonic observation.
        :rtype: TimeSample
        """

        ...
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ограничения возраста и точности времени
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class FreshnessPolicy:
    """Limit message age and uncertainty independently of VM wall clocks."""

    max_age: float = 120.0              # Предельный возраст команды по времени провайдера.
    sample_ttl: float = 300.0           # Срок использования показания источника времени.
    max_uncertainty: float = 5.0        # Допустимая ширина интервала UTC, секунды.
    drift_ppm: float = 100.0            # Верхняя оценка дрейфа локального отсчёта.
    future_tolerance: float = 2.0       # Малый допуск точности отметки провайдера.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка типов и согласованности полей
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject unbounded policies and bool-as-number values."""

        for value in (self.max_age, self.sample_ttl, self.max_uncertainty):
            require_number(value, "freshness policy")
        require_number(self.drift_ppm, "drift_ppm", allow_zero=True)
        require_number(self.future_tolerance, "future_tolerance", allow_zero=True)
        if (self.max_age > MAX_COMMAND_SECONDS or self.sample_ttl > 3600
                or self.max_uncertainty > 10 or self.drift_ppm > 1000 or self.future_tolerance > 5):
            raise ValueError("freshness policy exceeds limits")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Причина отказа до принятия команды
#------------------------------------------------------------------------------------------------------------------
class FreshnessReason(str, Enum):
    """Describe a bounded reason for refusing a command before admission."""

    TIME_UNAVAILABLE = "time_unavailable"
    TOO_OLD = "too_old"
    FUTURE = "future"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Результат проверки возраста команды
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class FreshnessDecision:
    """Carry a local admission deadline or a fixed rejection reason."""

    deadline: CommandDeadline | None    # Остаток исходного срока; None означает отказ.
    reason: FreshnessReason | None      # Причина отказа; None означает свежую команду.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка типов и согласованности полей
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Keep acceptance and rejection mutually exclusive."""

        if self.deadline is not None and type(self.deadline) is not CommandDeadline:
            raise TypeError("invalid freshness deadline")
        if self.reason is not None and type(self.reason) is not FreshnessReason:
            raise TypeError("invalid freshness reason")
        if (self.deadline is None) == (self.reason is None):
            raise ValueError("invalid freshness decision")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Локальный отсчёт времени на основе доверенного источника
#------------------------------------------------------------------------------------------------------------------
class TrustedClock:
    """Maintain bounded UTC estimates without reading or setting the system UTC clock."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        policy: FreshnessPolicy = FreshnessPolicy(),
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:

        """Keep an initially unavailable process-local time estimate.

        :param policy: Freshness limits and drift bound.
        :type policy: FreshnessPolicy

        :param clock: Local monotonic clock, injectable for deterministic tests.
        :type clock: Callable[[], float]
        """

        # policy — ограничения возраста команды и погрешности времени.
        # clock — локальный монотонный отсчёт времени.

        if type(policy) is not FreshnessPolicy or not callable(clock):
            raise TypeError("invalid trusted clock configuration")
        self.policy = policy
        self._clock = clock
        self._sample: TimeSample | None = None
        self._last: float | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка и установка нового показания
    #--------------------------------------------------------------------------------------------------------------
    def install(
        self,
        sample: TimeSample,
    ) -> None:

        """Install only a currently usable sample from a trusted source.

        :param sample: Authenticated UTC interval at its local receipt time.
        :type sample: TimeSample
        """

        # sample — доверенное показание и момент его получения.

        if type(sample) is not TimeSample:
            raise TypeError("invalid time sample type")
        now = self._clock()
        require_number(now, "monotonic time", allow_zero=True)

        # Сначала снимаем прежнее доверие. Ошибочное обновление не должно
        # незаметно оставить действующим старое показание.
        self._sample = None
        self._last = now
        elapsed = now - sample.observed_at
        width = sample.upper_utc - sample.lower_utc + 2 * max(0, elapsed) * self.policy.drift_ppm / 1e6
        if elapsed < 0 or elapsed >= self.policy.sample_ttl or width > self.policy.max_uncertainty:
            raise TimeUnavailable("trusted time unavailable")
        self._sample = sample
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Явное обновление времени из источника
    #--------------------------------------------------------------------------------------------------------------
    async def refresh(
        self,
        source: TimeSource,
    ) -> None:

        """Replace the anchor explicitly; failure invalidates the previous estimate.

        :param source: Explicit trusted time provider.
        :type source: TimeSource
        """

        # source — явно выбранный источник времени.

        self._sample = None
        try:
            self.install(await source.sample())
        except Exception:
            self._sample = None
            raise TimeUnavailable("trusted time unavailable") from None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Текущий интервал UTC с учётом дрейфа
    #--------------------------------------------------------------------------------------------------------------
    def bounds(self) -> TimeSample:

        """Return conservative current bounds or invalidate an expired estimate.

        :return: UTC bounds associated with a local monotonic observation.
        :rtype: TimeSample
        """

        now = self._clock()
        require_number(now, "monotonic time", allow_zero=True)
        sample = self._sample
        if self._last is not None and now < self._last:
            self._sample = None
        self._last = now
        if self._sample is None or sample is None:
            raise TimeUnavailable("trusted time unavailable")

        elapsed = now - sample.observed_at
        drift = elapsed * self.policy.drift_ppm / 1e6
        lower, upper = sample.lower_utc + elapsed - drift, sample.upper_utc + elapsed + drift
        if elapsed < 0 or elapsed >= self.policy.sample_ttl or upper - lower > self.policy.max_uncertainty:
            self._sample = None
            raise TimeUnavailable("trusted time unavailable")
        return TimeSample(lower_utc=lower, upper_utc=upper, observed_at=now)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка отметки сообщения и оставшегося срока
    #--------------------------------------------------------------------------------------------------------------
    def check(
        self,
        message_date: int,
        hub_epoch: str,
    ) -> FreshnessDecision:

        """Check provider seconds and preserve only the remaining original lifetime.

        :param message_date: Provider-assigned Unix timestamp in whole seconds.
        :type message_date: int

        :param hub_epoch: Current hub incarnation identifier.
        :type hub_epoch: str

        :return: Remaining command lifetime or a fixed rejection reason.
        :rtype: FreshnessDecision
        """

        # message_date — целочисленная отметка сообщения провайдера.
        # hub_epoch — идентификатор запуска hub.

        require_int(message_date, "message_date")
        try:
            sample = self.bounds()
        except TimeUnavailable:
            return FreshnessDecision(deadline=None, reason=FreshnessReason.TIME_UNAVAILABLE)

        # Для возраста берём наиболее позднее возможное текущее время.
        # Округление message.date вниз до секунды только сокращает допустимый срок.
        if message_date > sample.lower_utc + self.policy.future_tolerance:
            return FreshnessDecision(deadline=None, reason=FreshnessReason.FUTURE)
        remaining = self.policy.max_age - max(0.0, sample.upper_utc - message_date)
        if remaining <= 0:
            return FreshnessDecision(deadline=None, reason=FreshnessReason.TOO_OLD)
        deadline = CommandDeadline.from_response(remaining, sample.observed_at, sample.observed_at, hub_epoch)
        return FreshnessDecision(deadline=deadline, reason=None)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.commands.time не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
