# Правила ограниченного перезапуска компонентов общего сервера.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-184007
#
# Классы:
# -> FailureKind: Причина остановки компонента.
#
# -> RestartPolicy: Пределы повторных запусков.
#    Специальные методы:
#    -> __post_init__(): Проверка чисел и взаимных ограничений.
#
# -> RestartDecision: Разрешение перезапуска либо окончательный отказ.
#
# -> RestartBudget: Учёт перезапусков одного компонента в памяти manager.
#    Конструктор:
#    -> __init__(): Создание пустого счётчика.
#    Интерфейс:
#    -> record_failure(): Учёт очередного завершившегося запуска.
#
# Функции:
# -> _bounded_seconds(): Проверка конечного срока с верхней границей.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from .._validation import require_int, require_number


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Причина остановки компонента
#------------------------------------------------------------------------------------------------------------------
class FailureKind(str, Enum):

    """Fixed failure categories; remote exception text never selects a restart."""

    CRASH = "crash"
    HANG = "hang"
    CONFIGURATION = "configuration"
    AUTHORIZATION = "authorization"
    OWNERSHIP = "ownership"
    STORAGE = "storage"
    SHUTDOWN = "shutdown"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Пределы повторных запусков
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RestartPolicy:

    """Finite per-component restart limits within one manager lifetime."""

    max_restarts: int = 3        # Число повторных запусков в скользящем окне; 0 запрещает их.
    window: float = 300.0        # Длина окна учёта, секунды монотонного времени.
    initial_delay: float = 2.0   # Начальная задержка перед повторным запуском, секунды.
    max_delay: float = 30.0      # Верхняя граница задержки с учётом случайной поправки.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка чисел и взаимных ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject unbounded histories and invalid backoff intervals."""

        require_int(self.max_restarts, "max_restarts", minimum=0)
        if self.max_restarts > 100:
            raise ValueError("max_restarts exceeds limit")

        for name in ("window", "initial_delay", "max_delay"):
            _bounded_seconds(getattr(self, name), name)

        if not self.initial_delay <= self.max_delay <= self.window:
            raise ValueError("restart intervals are inconsistent")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Разрешение перезапуска либо окончательный отказ
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RestartDecision:

    """A restart reservation; None requires operator intervention or shutdown."""

    delay: float | None     # Задержка в секундах; None означает запрет нового запуска.
    reason: str             # Фиксированный код решения, без текста исключения.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Учёт перезапусков одного компонента в памяти manager
#------------------------------------------------------------------------------------------------------------------
class RestartBudget:

    """Reserve retries for terminated children, without clocks, sleeping or process I/O."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        policy: RestartPolicy,
    ) -> None:

        """Create a budget owned by one manager and one component.

        :param policy: Validated finite restart limits.
        :type policy: RestartPolicy
        """

        # policy — пределы учёта, одинаковые для всех перезапусков компонента.

        if not isinstance(policy, RestartPolicy):
            raise TypeError("restart policy required")

        self._policy = policy
        self._attempts: deque[float] = deque()
        self._last_time: float | None = None
        self._blocked: str | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Учёт очередного завершившегося запуска
    #--------------------------------------------------------------------------------------------------------------
    def record_failure(
        self,
        now: float,
        failure: FailureKind,
        jitter: float = 0.5,
    ) -> RestartDecision:

        """Reserve at most one retry for a child that has already terminated.

        :param now: Nondecreasing monotonic failure-observation time in seconds.
        :type now: float

        :param failure: Locally classified reason for this child's termination.
        :type failure: FailureKind

        :param jitter: Injected random sample in the closed interval from zero to one.
        :type jitter: float

        :return: Bounded delay or a latched refusal requiring operator action.
        :rtype: RestartDecision
        """

        # now — монотонное время, не UTC: перестановка часов VM не влияет на лимит.
        # failure — причина завершения, выбранная manager из известного перечня.
        # jitter — подставляемая случайная поправка; тесты не используют генератор случайных чисел.

        require_number(now, "now", allow_zero=True)
        require_number(jitter, "jitter", allow_zero=True)
        if jitter > 1 or not isinstance(failure, FailureKind):
            raise ValueError("invalid restart observation")
        if self._last_time is not None and now < self._last_time:
            raise ValueError("monotonic time moved backwards")

        self._last_time = now
        if self._blocked is not None:
            return RestartDecision(None, self._blocked)

        # Ошибка настройки, прав, владельца или журнала не исправляется запуском
        # нового процесса. После исчерпания лимита тоже ждём оператора, а не таймер.
        if failure not in (FailureKind.CRASH, FailureKind.HANG):
            self._blocked = failure.value
            return RestartDecision(None, self._blocked)

        while self._attempts and now - self._attempts[0] >= self._policy.window:
            self._attempts.popleft()
        if len(self._attempts) >= self._policy.max_restarts:
            self._blocked = "restart_limit"
            return RestartDecision(None, self._blocked)

        # Резервируем попытку сейчас; повторный опрос status не должен вызывать
        # этот метод. Даже неудачный spawn расходует попытку. Степень ограничена
        # отдельно, чтобы большие настройки не создавали огромные числа.
        exponent = min(len(self._attempts), 16)
        base = min(self._policy.max_delay, self._policy.initial_delay * (2 ** exponent))
        delay = min(self._policy.max_delay, base * (0.8 + 0.4 * jitter))
        self._attempts.append(now)
        return RestartDecision(delay, "restart_allowed")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка конечного срока с верхней границей
#------------------------------------------------------------------------------------------------------------------
def _bounded_seconds(
    value: object,
    name: str,
) -> None:

    """Require a finite positive duration no longer than one day.

    :param value: Duration being checked.
    :type value: object

    :param name: Fixed schema field label.
    :type name: str
    """

    # value — проверяемый срок в секундах.
    # name — имя поля, заданное кодом, а не содержимым JSON.

    require_number(value, name)
    if value > 86400:
        raise ValueError("duration exceeds limit")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.gateway_server.policy не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
