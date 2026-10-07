# Текущее состояние командного клиента и ограниченная история отказов.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261007-235742
#
# Классы:
# -> StageHealth: Ошибка одного этапа и счётчики восстановления.
# -> CommandHealth: Согласованное состояние четырёх этапов без сетевых обращений.
# -> HealthState: Потокобезопасное накопление наблюдений клиента.
#    Конструктор:
#    -> __init__(): Подготовка ограниченного состояния.
#    Интерфейс:
#    -> snapshot(): Чтение состояния без изменения истории.
#    -> lease(): Установка проверенного срока регистрации.
#    -> close(): Отметка начала остановки.
#    -> pending(): Отметка неподтверждённых результатов.
#    -> failure(): Учёт одного отказа без повторного счёта при передаче исключения.
#    -> success(): Восстановление только соответствующего этапа.
#
# Функции:
# -> observe(): Наблюдение асинхронной операции без изменения её результата.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any
from weakref import ref

from remote_watch.commands.transport import CommandError

_attempt: ContextVar[tuple[ref | None, object] | None] = ContextVar("command_health_attempt", default=None)
_STAGES = ("poll", "heartbeat", "storage", "result")
_TRANSIENT = frozenset({"busy", "unavailable", "capacity"})


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ошибка одного этапа и счётчики восстановления
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class StageHealth:
    """Separate the active failure from cumulative incident counters."""

    current_error: str | None = None     # Текущая ошибка; None после успешного восстановления.
    http_status: int | None = None       # HTTP-статус текущего отказа, если он был получен.
    error_kind: str | None = None        # Безопасная категория TLS; None для прочих отказов.
    verify_code: int | None = None       # Числовой код проверки сертификата OpenSSL.
    tls_reason: str | None = None        # Фиксированная причина TLS без адресов и текста сервера.
    busy_reason: str | None = None       # Точная фиксированная причина текущей занятости.
    failures: int = 0                    # Все наблюдавшиеся отказы этого этапа.
    transient_failures: int = 0          # Временные busy/unavailable/capacity, включая повторы.
    recoveries: int = 0                  # Переходы от временного отказа к успешной операции.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Согласованное состояние четырёх этапов без сетевых обращений
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandHealth:
    """Expose observed readiness independently of callback progress and incident history."""

    ready: bool                         # Все этапы исправны, сессия действует, результат доставлен.
    closed: bool                        # Началась остановка принадлежащих клиенту ресурсов.
    session_valid: bool                 # Проверенная сессия ещё действует на монотонных часах.
    has_pending_results: bool           # Есть неподтверждённый результат либо его проверка не завершена.
    last_error: str | None               # Последний отказ в истории; после восстановления сохраняется.
    last_error_stage: str | None         # Этап последнего отказа; None до первого инцидента.
    poll: StageHealth                   # Опрос/получение разрешения на команду.
    heartbeat: StageHealth              # Подтверждение и продление регистрации.
    storage: StageHealth                # Завершение операций локального журнала.
    result: StageHealth                 # Передача результата и получение устойчивой квитанции.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Потокобезопасное накопление наблюдений клиента
#------------------------------------------------------------------------------------------------------------------
class HealthState:
    """Retain a fixed number of immutable stage snapshots under one local lock."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Initialize inactive readiness without threads or external resources."""

        self._lock = threading.Lock()
        self._stages = {stage: StageHealth() for stage in _STAGES}
        self._expires = 0.0
        self._closed = False
        self._fenced = False
        self._pending = True
        self._last_error: str | None = None
        self._last_stage: str | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение состояния без изменения истории
    #--------------------------------------------------------------------------------------------------------------
    def snapshot(
        self,
        now: float,
        floor: float,
    ) -> CommandHealth:

        """Read immutable observations using the client's monotonic time domain.

        :param now: Current monotonic reading; invalid readings prohibit readiness.
        :type now: float

        :param floor: Most recent validated client clock reading.
        :type floor: float

        :return: A coherent snapshot without polling, storage or recovery side effects.
        :rtype: CommandHealth
        """

        # now/floor — только показания часов; чтение статистики не продлевает сессию.
        with self._lock:
            valid = (math.isfinite(now) and floor <= now < self._expires
                     and not self._closed and not self._fenced)
            ready = valid and not self._pending and all(s.current_error is None for s in self._stages.values())
            return CommandHealth(ready=ready, closed=self._closed, session_valid=valid,
                has_pending_results=self._pending, last_error=self._last_error,
                last_error_stage=self._last_stage, **self._stages)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Установка проверенного срока регистрации
    #--------------------------------------------------------------------------------------------------------------
    def lease(
        self,
        expires: float,
    ) -> None:

        """Retain only a fully validated lease deadline without clearing other stages.

        :param expires: Deadline in the client's monotonic time domain.
        :type expires: float
        """

        # expires — проверенный срок; ранее потерянная сессия не возрождается.
        with self._lock:
            self._expires = expires
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Отметка начала остановки
    #--------------------------------------------------------------------------------------------------------------
    def close(self) -> None:

        """Withdraw readiness immediately while retaining all incident counters."""

        with self._lock:
            self._closed = True
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Отметка неподтверждённых результатов
    #--------------------------------------------------------------------------------------------------------------
    def pending(
        self,
        value: bool,
    ) -> None:

        """Retain whether durable result acknowledgement remains unresolved.

        :param value: True until a journal check proves no current-session result remains.
        :type value: bool
        """

        # value — False устанавливается по журналу, а не по успеху callback/HTTP запроса.
        with self._lock:
            self._pending = value
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Учёт одного отказа без повторного счёта при передаче исключения
    #--------------------------------------------------------------------------------------------------------------
    def failure(
        self,
        stage: str,
        error: CommandError,
        attempt: object,
    ) -> None:

        """Attribute a failure to its innermost observed stage exactly once per attempt.

        :param stage: Fixed stage name controlled by library code.
        :type stage: str

        :param error: Safe public failure without exception text.
        :type error: CommandError

        :param attempt: Local identity shared by nested operations in one attempt.
        :type attempt: object
        """

        # stage/error/attempt — этап, безопасный код и метка одного прохода вызова.
        # Повтор того же объекта ошибки в следующем запросе считается новым отказом.
        if getattr(error, "_health_observation", None) == (self, attempt):
            return
        error._health_observation = (self, attempt)
        with self._lock:
            previous = self._stages[stage]
            self._stages[stage] = replace(previous, current_error=error.code, http_status=error.http_status,
                error_kind=error.error_kind, verify_code=error.verify_code, tls_reason=error.tls_reason,
                busy_reason=error.busy_reason,
                failures=previous.failures + 1,
                transient_failures=previous.transient_failures + int(error.code in _TRANSIENT))
            self._last_error = error.code
            self._last_stage = stage
            if error.code in {"stale_session", "closed"}:
                self._fenced = True
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Восстановление только соответствующего этапа
    #--------------------------------------------------------------------------------------------------------------
    def success(
        self,
        stage: str,
    ) -> None:

        """Clear a transient failure only after the matching stage completes successfully.

        :param stage: Fixed stage name controlled by library code.
        :type stage: str
        """

        # stage — только успешно завершившийся этап; история последнего отказа сохраняется.
        with self._lock:
            previous = self._stages[stage]
            if stage == "result" and self._pending:
                return
            if previous.current_error in _TRANSIENT:
                self._stages[stage] = replace(previous, current_error=None, http_status=None,
                                            error_kind=None, verify_code=None, tls_reason=None, busy_reason=None,
                                            recoveries=previous.recoveries + 1)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Наблюдение асинхронной операции без изменения её результата
#------------------------------------------------------------------------------------------------------------------
def observe(stage: str) -> Callable:

    """Observe nested client operations while preserving their existing exception semantics.

    :param stage: Library-controlled stage name.
    :type stage: str

    :return: Decorator for asynchronous methods on an object owning HealthState.
    :rtype: Callable
    """

    # stage — этап метода; вложенное storage/result получает приоритет при отказе.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Сохранение сигнатуры наблюдаемого метода
    #--------------------------------------------------------------------------------------------------------------
    def decorate(function: Callable) -> Callable:

        """Preserve the wrapped public interface.

        :param function: Asynchronous client operation.
        :type function: Callable

        :return: Instrumented operation with the original metadata.
        :rtype: Callable
        """

        # function — исходная операция; наблюдение не меняет её правила повторов.

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Учёт отказа или успешного окончания операции
        #----------------------------------------------------------------------------------------------------------
        @wraps(function)
        async def observed(
            self: Any,
            *args: Any,
            **kwargs: Any,
        ) -> Any:

            """Record one stage outcome without treating cancellation as recovery.

            :param self: Client owning the health accumulator.
            :type self: Any

            :param args: Original positional arguments.
            :type args: Any

            :param kwargs: Original keyword arguments.
            :type kwargs: Any

            :return: Unchanged operation result.
            :rtype: Any
            """

            # self/args/kwargs — исходный вызов; произвольные значения не входят в статистику.
            previous = _attempt.get()
            task = asyncio.current_task()
            # Фоновый heartbeat наследует context, но не должен удерживать родительскую
            # задачу вместе с её результатом/traceback после завершения запуска.
            attempt = (previous[1] if previous is not None and previous[0] is not None
                       and previous[0]() is task else object())
            marker = _attempt.set((ref(task) if task is not None else None, attempt))
            try:
                result = await function(self, *args, **kwargs)
            except CommandError as error:
                self._health.failure(stage, error, attempt)
                raise
            except Exception as error:
                if getattr(error, "_health_observation", None) != (self._health, attempt):
                    self._health.failure(stage, CommandError("unavailable"), attempt)
                    error._health_observation = (self._health, attempt)
                raise
            else:
                self._health.success(stage)
                return result
            finally:
                _attempt.reset(marker)
        #----------------------------------------------------------------------------------------------------------

        return observed
    #--------------------------------------------------------------------------------------------------------------

    return decorate
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль health не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
