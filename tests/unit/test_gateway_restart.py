# Проверки ограничений перезапуска без процессов, сети и реального ожидания.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-184007
#
# Тесты:
# -> test_backoff_and_latched_limit(): Задержки, верхняя граница и окончательное исчерпание.
# -> test_permanent_failures(): Отказы, которые нельзя исправлять перезапуском.
# -> test_window_and_disabled_restarts(): Граница окна и явный запрет повторов.
# -> test_invalid_policy(): Недопустимые сроки и ёмкость истории.
# -> test_invalid_observation(): Ошибки монотонного времени и случайной поправки.
# -> test_long_running_budget(): Ограничение истории при долгой работе.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from typing import Any

import pytest

from remote_watch.gateway_server import FailureKind, RestartBudget, RestartPolicy


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Задержки, верхняя граница и окончательное исчерпание
#------------------------------------------------------------------------------------------------------------------
def test_backoff_and_latched_limit() -> None:

    """Cap both restart count and jittered delays, keeping exhaustion latched."""

    budget = RestartBudget(RestartPolicy(max_restarts=3, initial_delay=2, max_delay=5))
    assert budget.record_failure(0, FailureKind.CRASH, jitter=0).delay == pytest.approx(1.6)
    assert budget.record_failure(10, FailureKind.HANG, jitter=0.5).delay == 4
    assert budget.record_failure(20, FailureKind.CRASH, jitter=1).delay == 5
    blocked = budget.record_failure(30, FailureKind.CRASH)
    assert blocked.delay is None
    assert blocked.reason == "restart_limit"

    # Истечение окна не оживляет бесконечно падающий компонент без оператора.
    assert budget.record_failure(100000, FailureKind.CRASH) == blocked
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказы, которые нельзя исправлять перезапуском
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("failure", [FailureKind.CONFIGURATION, FailureKind.AUTHORIZATION,
                                     FailureKind.OWNERSHIP, FailureKind.STORAGE, FailureKind.SHUTDOWN])
def test_permanent_failures(failure: FailureKind) -> None:

    """Never turn configuration, authorization, storage or ownership faults into retries.

    :param failure: Non-retryable termination reason.
    :type failure: FailureKind
    """

    # failure — выбранная manager причина, не текст исключения дочернего процесса.

    budget = RestartBudget(RestartPolicy())
    decision = budget.record_failure(1, failure)
    assert decision.delay is None
    assert decision.reason == failure.value
    assert budget.record_failure(1000, FailureKind.CRASH) == decision
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Граница окна и явный запрет повторов
#------------------------------------------------------------------------------------------------------------------
def test_window_and_disabled_restarts() -> None:

    """Expire old reservations at the exact boundary while honoring zero restarts."""

    policy = RestartPolicy(max_restarts=1, window=10, initial_delay=1, max_delay=2)
    before = RestartBudget(policy)
    before.record_failure(0, FailureKind.CRASH)
    assert before.record_failure(9.999, FailureKind.CRASH).reason == "restart_limit"

    boundary = RestartBudget(policy)
    boundary.record_failure(0, FailureKind.CRASH)
    assert boundary.record_failure(10, FailureKind.CRASH).delay == 1
    assert RestartBudget(RestartPolicy(max_restarts=0)).record_failure(0, FailureKind.CRASH).delay is None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Недопустимые сроки и ёмкость истории
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("values", [
    {"max_restarts": True}, {"max_restarts": -1}, {"max_restarts": 101},
    {"window": float("nan")}, {"window": float("inf")}, {"window": 86401},
    {"initial_delay": 0}, {"initial_delay": -1}, {"initial_delay": 31},
    {"max_delay": True}, {"max_delay": 301}, {"window": 1},
])
def test_invalid_policy(values: dict[str, Any]) -> None:

    """Reject unbounded or inconsistent restart settings before acquiring any resource.

    :param values: Invalid overrides for policy defaults.
    :type values: dict[str, Any]
    """

    # values — только подставные граничные значения.

    with pytest.raises((TypeError, ValueError)):
        RestartPolicy(**values)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибки монотонного времени и случайной поправки
#------------------------------------------------------------------------------------------------------------------
def test_invalid_observation() -> None:

    """Reject invalid samples without consuming a valid restart reservation."""

    budget = RestartBudget(RestartPolicy())
    budget.record_failure(10, FailureKind.CRASH)
    for now, jitter in ((9, 0.5), (11, -1), (11, 1.01), (float("nan"), 0.5), (11, True)):
        with pytest.raises((ValueError, TypeError)):
            budget.record_failure(now, FailureKind.CRASH, jitter)
    with pytest.raises(ValueError):
        budget.record_failure(11, "crash")
    assert budget.record_failure(11, FailureKind.HANG).delay == 4
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничение истории при долгой работе
#------------------------------------------------------------------------------------------------------------------
def test_long_running_budget() -> None:

    """Retain only active reservations even across thousands of widely spaced failures."""

    budget = RestartBudget(RestartPolicy(max_restarts=2, window=10, initial_delay=1, max_delay=2))
    for index in range(10000):
        assert budget.record_failure(index * 10, FailureKind.CRASH).delay == 1
    assert len(budget._attempts) == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль тестов не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
