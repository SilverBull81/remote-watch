# Выполнение пользовательских команд с явным владением потоками и неизвестными исходами.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-210047
#
# Классы:
# -> DispatcherStats: Счётчики и состояние исполнителя без приватных данных.
#
# -> CommandDispatcher: Последовательное выполнение команд приложения.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> stats(): Чтение счётчиков и фактической занятости обработчика.
#    -> start(): Запуск объекта и подготовка состояния.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.
#    Служебные методы:
#    -> _run(): Последовательное получение команд без очереди пользовательских вызовов.
#    -> _validate(): Проверка аргументов в отдельном потоке до выдачи grant.
#    -> _execute(): Исполнение одной команды и удержание слота после timeout.
#    -> _invoke_sync(): Последняя проверка срока и вызов обработчика в рабочем потоке.
#    -> _invoke_async(): Вызов и ожидание async-обработчика в цикле приложения.
#    -> _settle(): Повтор сохранения и доставки результата без повторного callback.
#    -> _shutdown(): Ограниченная остановка с сохранением неизвестного исполнения.
#
# Функции:
# -> is_async(): Определение объявленного асинхронного обработчика.
# -> _unknown(): Подготовка UNKNOWN без текста пользовательского исключения.
# -> _consume(): Извлечение поздней ошибки без записи её деталей в лог.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from typing import Any

from remote_watch._validation import require_number
from remote_watch.commands.client import CommandClient, CommandTicket
from remote_watch.commands.health import CommandHealth
from remote_watch.commands.protocol import (
    CommandOutcome,
    CommandReason,
    CommandRequest,
    CommandResult,
    callback_result,
    describe_commands,
)
from remote_watch.commands.registry import CommandRegistry, CommandSpec
from remote_watch.commands.transport import CommandError

#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************
# Запрещает рекурсивный lifecycle из обработчика; обычное логирование остаётся доступно.
command_context: ContextVar[bool] = ContextVar("remote_watch_command", default=False)


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Определение объявленного асинхронного обработчика
#------------------------------------------------------------------------------------------------------------------
def is_async(callback: Any) -> bool:

    """Recognize coroutine functions, partials and asynchronous callable objects.

    :param callback: Application-defined callable, possibly partial or a callable object.
    :type callback: Any

    :return: True for a declared coroutine function or asynchronous callable object.
    :rtype: bool
    """

    # callback — обработчик приложения, включая partial и вызываемые объекты.

    return (inspect.iscoroutinefunction(callback)
            or inspect.iscoroutinefunction(getattr(callback, "__call__", None)))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Счётчики и состояние исполнителя без приватных данных
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class DispatcherStats:
    """Expose bounded counters without command text, arguments or credentials."""

    completed: int              # Обработчики, вернувшие допустимый результат.
    unknown: int                # Неизвестные исходы, включая timeout и исключения.
    rejected: int               # Запросы, не прошедшие локальную проверку аргументов.
    running: bool               # Пользовательская работа ещё фактически не закончилась.
    closed: bool                # Новые команды больше не принимаются.
    last_error: str | None       # Фиксированный код последнего отказа; None при отсутствии.
    ready: bool = False          # Наблюдаемая исправность всех этапов при действующей сессии.
    last_error_stage: str | None = None      # Этап последнего отказа; история не стирается успехом.
    health: CommandHealth | None = None     # Отдельные текущие ошибки, отказы и восстановления этапов.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Последовательное выполнение команд приложения
#------------------------------------------------------------------------------------------------------------------
class CommandDispatcher:
    """Execute one registered callback at a time with explicit thread and event-loop ownership."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        registry: CommandRegistry,
        client: CommandClient,
        *,
        poll_interval: float = 0.1,
        retry_interval: float = 0.5,
        shutdown_timeout: float = 5.0,
    ) -> None:

        """Validate an exact registry-to-registration binding without starting resources.

        :param registry: Application-owned immutable command registry.
        :type registry: CommandRegistry

        :param client: Explicit command client with matching identity and capabilities.
        :type client: CommandClient

        :param poll_interval: Minimum pause between successful polling cycles, seconds.
        :type poll_interval: float

        :param retry_interval: Pause before another storage or result-delivery attempt, seconds.
        :type retry_interval: float

        :param shutdown_timeout: Maximum asynchronous journal shutdown wait, seconds.
        :type shutdown_timeout: float
        """

        # registry — реестр пользовательских обработчиков и правил проверки.
        # client — отдельный командный клиент с согласованными Identity и командами.
        # poll_interval — минимальная пауза между опросами, секунды.
        # retry_interval — пауза перед повтором хранения или доставки результата, секунды.
        # shutdown_timeout — конечное ожидание закрытия журнала, секунды.

        if type(registry) is not CommandRegistry or type(client) is not CommandClient:
            raise TypeError("invalid command dispatcher")

        if describe_commands(registry) != client.registration.capabilities:
            raise ValueError("command registration differs from registry")

        for value in (poll_interval, retry_interval, shutdown_timeout):
            require_number(value, "dispatcher interval")
            if value > 30:
                raise ValueError("invalid dispatcher interval")
        self.registry = registry
        self.client = client
        self._poll_interval = poll_interval
        self._retry_interval = retry_interval
        self._shutdown_timeout = shutdown_timeout
        self._pool: ThreadPoolExecutor | None = None
        self._runner: asyncio.Task | None = None
        self._job: asyncio.Future | None = None
        self._ticket: CommandTicket | None = None
        self._stopping = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_job = False
        self._completed = 0
        self._unknown = 0
        self._rejected = 0
        self._error: str | None = None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение счётчиков и фактической занятости обработчика
    #--------------------------------------------------------------------------------------------------------------
    @property
    def stats(self) -> DispatcherStats:

        """Read counters without formatting callback values or exception details.

        :return: Snapshot of bounded counters and actual callback ownership.
        :rtype: DispatcherStats
        """

        health = self.client.health
        return DispatcherStats(completed=self._completed, unknown=self._unknown, rejected=self._rejected,
            running=self._job is not None and not self._job.done(), closed=self._stopping.is_set(),
            last_error=health.last_error, last_error_stage=health.last_error_stage, health=health,
            ready=health.ready and self._runner is not None and not self._stopping.is_set())
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск объекта и подготовка состояния
    #--------------------------------------------------------------------------------------------------------------
    async def start(self) -> None:

        """Bind asynchronous callbacks to this application loop and open the command client."""

        if command_context.get() or self._loop is not None or self._stopping.is_set():
            raise CommandError("closed")
        self._loop = asyncio.get_running_loop()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remote-watch-callback")

        try:
            await self.client.start()
            self._runner = asyncio.create_task(self._run())
            self._runner.add_done_callback(_consume)
        except BaseException:
            await self.close()
            raise
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> bool:

        """Stop admission within a finite wait without pretending a live callback terminated.

        :return: True only when user work and the dispatcher runner have both finished.
        :rtype: bool
        """

        if command_context.get():
            raise RuntimeError("command lifecycle cannot run inside a callback")

        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("command dispatcher belongs to another event loop")
        first = not self._stopping.is_set()
        self._stopping.set()

        if self._runner is not None and not self._runner.done():
            if first:
                self._runner.cancel()
            await asyncio.wait({self._runner}, timeout=self._shutdown_timeout)
        elif self._runner is None:
            await self.client.close()

        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        return not self.stats.running and (self._runner is None or self._runner.done())
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Последовательное получение команд без очереди пользовательских вызовов
    #--------------------------------------------------------------------------------------------------------------
    async def _run(self) -> None:

        """Poll sequentially and never acquire another command while user work remains active."""

        try:
            while not self._stopping.is_set():
                try:
                    # Даже зависший валидатор занимает единственный рабочий поток.
                    # Отклонение запроса не разрешает запускать за ним очередь новых работ.
                    if self._job is not None and not self._job.done():
                        await asyncio.shield(self._job)
                    ticket = await self.client.acquire(self._validate)
                    if ticket is not None:
                        self._ticket = ticket
                        await self._execute(ticket)
                        self._ticket = None
                    await asyncio.sleep(self._poll_interval)
                except CommandError as error:
                    self._error = error.code
                    if getattr(error, "_health_observation", (None,))[0] is not self.client._health:
                        self.client._health.failure("poll", error, object())
                    if error.code not in ("busy", "unavailable", "capacity"):
                        break
                    await asyncio.sleep(self._retry_interval)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._error = "unavailable"
            if getattr(error, "_health_observation", (None,))[0] is not self.client._health:
                self.client._health.failure("poll", CommandError("unavailable"), object())
        finally:
            self._stopping.set()
            await self._shutdown()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка аргументов в отдельном потоке до выдачи grant
    #--------------------------------------------------------------------------------------------------------------
    async def _validate(
        self,
        request: CommandRequest,
        remaining: float,
    ) -> CommandReason | None:

        """Run a pure synchronous validator off-loop before the hub issues an execution grant.

        :param request: Incoming request validated before dispatch.
        :type request: CommandRequest

        :param remaining: Conservative remaining command lifetime in seconds.
        :type remaining: float

        :return: Fixed validation refusal reason, or None when arguments are accepted.
        :rtype: CommandReason | None
        """

        # request — входящий запрос, проверяемый перед обработкой.
        # remaining — остаток исходного срока команды, секунды.

        spec = self.registry.get(request.name)

        if spec is None:
            return CommandReason.DENIED

        if spec.validate_arguments is None:
            return CommandReason.INVALID_ARGUMENTS if request.arguments else None

        if remaining <= 0 or self._stopping.is_set():
            return CommandReason.INVALID_ARGUMENTS


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Чистая проверка аргументов без запуска обработчика
        #----------------------------------------------------------------------------------------------------------
        def validate() -> bool:

            """Normalize validator failure without returning private exception messages.

            :return: True only when the validator returns None without raising.
            :rtype: bool
            """

            marker = command_context.set(True)

            try:
                if self._stopping.is_set():
                    return False
                value = spec.validate_arguments(request.arguments)
                if inspect.iscoroutine(value):
                    value.close()
                return value is None
            except BaseException:
                return False
            finally:
                command_context.reset(marker)
        #----------------------------------------------------------------------------------------------------------


        self._async_job = False
        self._job = self._loop.run_in_executor(self._pool, copy_context().run, validate)
        self._job.add_done_callback(_consume)
        done, _ = await asyncio.wait({self._job}, timeout=min(remaining, spec.timeout))
        valid = bool(done) and self._job.result()

        if not valid:
            self._rejected += 1
            return CommandReason.INVALID_ARGUMENTS
        return None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Исполнение одной команды и удержание слота после timeout
    #--------------------------------------------------------------------------------------------------------------
    async def _execute(
        self,
        ticket: CommandTicket,
    ) -> None:

        """Record timeout promptly while retaining the slot until actual callback completion.

        :param ticket: Exact ticket returned by this client, never a reconstructed copy.
        :type ticket: CommandTicket
        """

        # ticket — тот же объект разрешения, который вернул этот клиент.

        spec = self.registry[ticket.grant.request.name]
        # STARTED уже записан в журнале. Теперь потребляем единственное разрешение;
        # дополнительная проверка внутри worker исключает просрочку при передаче в поток.
        allowed = self.client.begin(ticket)

        if not allowed or self._stopping.is_set():
            self._unknown += 1
            await self._settle(ticket, _unknown(ticket, CommandReason.TIMEOUT), True)
            return
        self._async_job = is_async(spec.callback)

        if self._async_job:
            self._job = asyncio.create_task(self._invoke_async(spec, ticket))
        else:
            self._job = self._loop.run_in_executor(self._pool, copy_context().run, self._invoke_sync, spec, ticket)
        self._job.add_done_callback(_consume)
        done, _ = await asyncio.wait({self._job}, timeout=self.client.remaining(ticket))
        timed_out = not done or self.client.remaining(ticket) <= 0

        # UNKNOWN фиксируется сразу по окончании срока, но сам рабочий слот остаётся
        # занят. Отмена async-задачи — лишь просьба, а не доказательство её завершения.
        if timed_out:
            result = _unknown(ticket, CommandReason.TIMEOUT)
            if self._async_job and not self._job.done():
                self._job.cancel()
        else:
            result = (_unknown(ticket, CommandReason.CALLBACK_ERROR)
                      if self._job.cancelled() else self._job.result())

        if result.outcome is CommandOutcome.COMPLETED:
            self._completed += 1
        else:
            self._unknown += 1
        finished = self._job.done()
        await self._settle(ticket, result, finished)

        if not finished:
            # Shield не позволяет отмене dispatcher выдать остановку sync-потока за факт.
            # Поздний ответ callback не заменяет уже сохранённый UNKNOWN.
            try:
                await asyncio.shield(self._job)
            except asyncio.CancelledError:
                if not self._job.cancelled():
                    raise
            await self._settle(ticket, result, True)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Последняя проверка срока и вызов обработчика в рабочем потоке
    #--------------------------------------------------------------------------------------------------------------
    def _invoke_sync(
        self,
        spec: CommandSpec,
        ticket: CommandTicket,
    ) -> CommandResult:

        """Invoke a synchronous handler only after the worker checks its final start conditions.

        :param spec: Exact locally registered command specification.
        :type spec: CommandSpec

        :param ticket: Exact ticket returned by this client, never a reconstructed copy.
        :type ticket: CommandTicket

        :return: Correlated bounded outcome without callback exception details.
        :rtype: CommandResult
        """

        # spec — локальное описание именно этой команды.
        # ticket — тот же объект разрешения, который вернул этот клиент.

        marker = command_context.set(True)

        try:
            if self._stopping.is_set() or self.client.remaining(ticket) <= 0:
                return _unknown(ticket, CommandReason.TIMEOUT)
            value = spec.callback(ticket.grant.request.context()) if spec.takes_context else spec.callback()
            if inspect.iscoroutine(value):
                value.close()
            return callback_result(ticket.grant.request.ref, ticket.grant.claim_id, value)
        except BaseException:
            return _unknown(ticket, CommandReason.CALLBACK_ERROR)
        finally:
            command_context.reset(marker)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Вызов и ожидание async-обработчика в цикле приложения
    #--------------------------------------------------------------------------------------------------------------
    async def _invoke_async(
        self,
        spec: CommandSpec,
        ticket: CommandTicket,
    ) -> CommandResult:

        """Invoke and await an asynchronous handler on the explicitly owning application loop.

        :param spec: Exact locally registered command specification.
        :type spec: CommandSpec

        :param ticket: Exact ticket returned by this client, never a reconstructed copy.
        :type ticket: CommandTicket

        :return: Correlated bounded outcome without callback exception details.
        :rtype: CommandResult
        """

        # spec — локальное описание именно этой команды.
        # ticket — тот же объект разрешения, который вернул этот клиент.

        marker = command_context.set(True)

        try:
            if self._stopping.is_set() or self.client.remaining(ticket) <= 0:
                return _unknown(ticket, CommandReason.TIMEOUT)
            awaitable = spec.callback(ticket.grant.request.context()) if spec.takes_context else spec.callback()
            value = await awaitable
            return callback_result(ticket.grant.request.ref, ticket.grant.claim_id, value)
        except BaseException:
            return _unknown(ticket, CommandReason.CALLBACK_ERROR)
        finally:
            command_context.reset(marker)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Повтор сохранения и доставки результата без повторного callback
    #--------------------------------------------------------------------------------------------------------------
    async def _settle(
        self,
        ticket: CommandTicket,
        result: CommandResult,
        finished: bool,
    ) -> None:

        """Retry only persistence and result delivery, never the application callback.

        :param ticket: Exact ticket returned by this client, never a reconstructed copy.
        :type ticket: CommandTicket

        :param result: Correlated terminal result whose exact contents must be retained.
        :type result: CommandResult

        :param finished: Whether the callback has actually returned or was never invoked.
        :type finished: bool
        """

        # ticket — тот же объект разрешения, который вернул этот клиент.
        # result — точный итог выполнения с идентификаторами исходной команды.
        # finished — фактическое окончание callback либо доказанный отказ от его вызова.

        self.client._health.pending(True)
        while not self._stopping.is_set():
            try:
                stored = await self.client.lookup(result.ref)
                if stored is not None and stored.record.result is not None:
                    if stored.record.result != result:
                        raise CommandError("outcome_conflict")
                    if finished and stored.execution_active:
                        await self.client.release(ticket)
                    else:
                        await self.client.flush_results()
                else:
                    await self.client.complete(ticket, result, execution_finished=finished)
                return
            except CommandError as error:
                self._error = error.code
                if getattr(error, "_health_observation", (None,))[0] is not self.client._health:
                    self.client._health.failure("result", error, object())
                if error.code not in ("busy", "unavailable", "capacity"):
                    raise
                await asyncio.sleep(self._retry_interval)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Ограниченная остановка с сохранением неизвестного исполнения
    #--------------------------------------------------------------------------------------------------------------
    async def _shutdown(self) -> None:

        """Persist an interrupted execution when possible and leave unresolved work pinned."""

        if self._job is not None and self._async_job and not self._job.done():
            self._job.cancel()

        # Если остановка оборвала ожидание callback, сохраняем неопределённость.
        # При недоступном журнале STARTED восстановится как UNKNOWN на следующем запуске.
        if self._ticket is not None:
            try:
                await asyncio.wait_for(self.client.complete(
                    self._ticket, _unknown(self._ticket, CommandReason.TIMEOUT),
                    execution_finished=self._job is None or self._job.done()), min(1, self._shutdown_timeout))
            except (CommandError, asyncio.TimeoutError):
                pass

        try:
            await self.client.close()
        finally:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка UNKNOWN без текста пользовательского исключения
#------------------------------------------------------------------------------------------------------------------
def _unknown(
    ticket: CommandTicket,
    reason: CommandReason,
) -> CommandResult:

    """Create a private-data-free outcome for the original execution attempt.

    :param ticket: Exact ticket returned by this client, never a reconstructed copy.
    :type ticket: CommandTicket

    :param reason: Fixed public failure reason, without exception details.
    :type reason: CommandReason

    :return: Correlated bounded outcome without callback exception details.
    :rtype: CommandResult
    """

    # ticket — тот же объект разрешения, который вернул этот клиент.
    # reason — фиксированная причина без текста исключения.

    return CommandResult(ref=ticket.grant.request.ref, claim_id=ticket.grant.claim_id,
                         outcome=CommandOutcome.UNKNOWN, reason=reason)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Извлечение поздней ошибки без записи её деталей в лог
#------------------------------------------------------------------------------------------------------------------
def _consume(future: asyncio.Future) -> None:

    """Retrieve task failures without exposing callback exceptions through logging.

    :param future: Completed callback or runner, possibly cancelled during shutdown.
    :type future: asyncio.Future
    """

    # future — завершившийся обработчик или исполнитель, в том числе после отмены.

    if not future.cancelled():
        future.exception()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль dispatcher не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
