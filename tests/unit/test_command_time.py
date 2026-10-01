# Проверки свежести команд при независимых часах и ошибках источника времени.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Тесты:
# -> test_vm_clocks_do_not_authorize_commands(): Независимость допуска команды от UTC машин.
#
# -> test_untrusted_time_fails_closed(): Отказ при истечении, дрейфе и возврате времени назад.
#
# -> test_refresh_failure_invalidates_old_anchor(): Снятие доверия после неудачного обновления.
#
# -> test_time_policy_limits(): Ограничения настроек проверки времени.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from remote_watch.commands.time import (
    FreshnessPolicy,
    FreshnessReason,
    TimeSample,
    TimeUnavailable,
    TrustedClock,
)


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимость допуска команды от UTC машин
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("offset", [-86400, -120, 0, 120, 86400])
def test_vm_clocks_do_not_authorize_commands(
    monkeypatch: pytest.MonkeyPatch,
    offset: int,
) -> None:

    """Use the trusted interval despite either sign of VM UTC error.

    :param monkeypatch: Pytest scoped dependency replacement.
    :type monkeypatch: pytest.MonkeyPatch

    :param offset: Synthetic VM wall-clock error in seconds.
    :type offset: int
    """

    # monkeypatch — временная подмена зависимости в тесте.
    # offset — подставное расхождение UTC машины.

    monkeypatch.setattr("time.time", lambda: 1000000 + offset)
    now = [100.0]
    clock = TrustedClock(clock=lambda: now[0])
    clock.install(TimeSample(lower_utc=1000000, upper_utc=1000002, observed_at=100))
    fresh = clock.check(999990, "a" * 32)
    assert fresh.reason is None
    assert fresh.deadline.remaining(100, "a" * 32) == 108
    now[0] += 5
    assert clock.check(999990, "a" * 32).deadline.remaining(105, "a" * 32) < 103
    assert clock.check(999882, "a" * 32).reason is FreshnessReason.TOO_OLD
    assert clock.check(1000010, "a" * 32).reason is FreshnessReason.FUTURE
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ при истечении, дрейфе и возврате времени назад
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["missing", "old_sample", "wide_sample", "rollback", "drift", "future_sample"])
def test_untrusted_time_fails_closed(case: str) -> None:

    """Invalidate unavailable, expired, imprecise and rolled-back anchors.

    :param case: Selected success or failure scenario.
    :type case: str
    """

    # case — выбранный тестовый сценарий.

    now = [100.0]
    policy = FreshnessPolicy(sample_ttl=1000, max_uncertainty=2, drift_ppm=1000)
    clock = TrustedClock(policy, clock=lambda: now[0])
    if case != "missing":
        clock.install(TimeSample(lower_utc=1000, upper_utc=1001, observed_at=100))
    if case == "old_sample":
        now[0] = 1100
    elif case == "rollback":
        now[0] = 99
    elif case == "drift":
        now[0] = 700
    elif case in ("wide_sample", "future_sample"):
        with pytest.raises(TimeUnavailable):
            clock.install(TimeSample(lower_utc=1000, upper_utc=1004 if case == "wide_sample" else 1001,
                                     observed_at=101 if case == "future_sample" else 100))
    assert clock.check(1000, "a" * 32).reason is FreshnessReason.TIME_UNAVAILABLE
    now[0] = 100
    assert clock.check(1000, "a" * 32).reason is FreshnessReason.TIME_UNAVAILABLE
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Снятие доверия после неудачного обновления
#------------------------------------------------------------------------------------------------------------------
def test_refresh_failure_invalidates_old_anchor() -> None:

    """Do not reuse a previous successful sample after a failed refresh."""

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Источник с подставной ошибкой
    #--------------------------------------------------------------------------------------------------------------
    class Source:
        """Supply synthetic failure without contacting a provider."""

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Получение одного показания времени
        #----------------------------------------------------------------------------------------------------------
        async def sample(self) -> TimeSample:

            """Raise an error containing synthetic private detail.

            :return: UTC bounds associated with a local monotonic observation.
            :rtype: TimeSample
            """

            raise OSError("private-provider-detail")
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    clock = TrustedClock(clock=lambda: 100)
    clock.install(TimeSample(lower_utc=1000, upper_utc=1001, observed_at=100))
    with pytest.raises(TimeUnavailable) as caught:
        asyncio.run(clock.refresh(Source()))
    assert "private" not in str(caught.value)
    assert clock.check(1000, "a" * 32).deadline is None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничения настроек проверки времени
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("max_age", True), ("max_age", 301), ("max_age", float("nan")),
    ("sample_ttl", 3601), ("max_uncertainty", 11), ("drift_ppm", -1),
    ("future_tolerance", 6), ("max_age", 0),
])
def test_time_policy_limits(
    field: str,
    value: object,
) -> None:

    """Reject policies that would silently relax freshness beyond bounded limits.

    :param field: Configuration field being tested.
    :type field: str

    :param value: Candidate value checked against the contract.
    :type value: object
    """

    # field — проверяемое поле настройки.
    # value — проверяемое значение.

    with pytest.raises((TypeError, ValueError)):
        replace(FreshnessPolicy(), **{field: value})
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.unit.test_command_time не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
