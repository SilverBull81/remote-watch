# Исполнение callbacks, проверка аргументов и независимость команд от уведомлений.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-200546
#
# Тесты:
# -> test_sync_startup_cause(): Сохранение первичной ошибки синхронного старта.
# -> configured(): Подготовка согласованных прав, реестра и двух журналов.
# -> until(): Конечное ожидание наблюдаемого результата теста.
# -> submit(): Проверка прав и свежести перед атомарной записью с cursor.
# -> test_dispatcher_callback_forms(): Формы callbacks и проверка их потока либо event loop.
# -> test_dispatcher_argument_validation(): Проверка аргументов до grant и удержание зависшего валидатора.
# -> test_dispatcher_outcome_and_retry(): Безопасные неизвестные исходы и повтор только доставки результата.
# -> test_dispatcher_timeout_keeps_slot(): Удержание слота до фактического окончания после timeout.
# -> test_dispatcher_shutdown_pins_live_callback(): Конечная остановка с сохранением живого исполнения.
# -> test_watcher_commands_and_partial_events(): Проверка и изменение Event через словарь и partial.
# -> test_dispatcher_exact_registry_binding(): Запрет несовпадения реестра, scope и Identity клиента.
# -> test_watcher_rejects_callback_lifecycle(): Запрет рекурсивной остановки watcher из обработчика.
# -> test_watcher_sync_commands_leave_notifications_live(): Доставка уведомления при занятом командном потоке.
# -> test_watcher_sync_start_rejects_async_callback(): Явный выбор цикла приложения для async-обработчика.
# -> test_dispatcher_final_worker_deadline(): Запрет callback после истечения срока перед входом в поток.
# -> test_dispatcher_recovered_journal(): Разбор старых записей без повторного callback.
# -> test_dispatcher_result_persisted_before_release_failure(): Восстановление слота после отказа очистки.
# -> test_sync_startup_cause(): Безопасная причина старта без исходной цепочки исключений.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import sqlite3
import ssl
import threading
import traceback
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Any
from unittest.mock import AsyncMock

import pytest
from test_command_hub import APP_TOKEN, DirectTransport, Rig

from remote_watch import CommandRegistry, CommandSpec, RemoteWatcher, WatcherConfig
from remote_watch.commands.client import CommandClient
from remote_watch.commands.dispatcher import CommandDispatcher
from remote_watch.commands.protocol import (
    CommandReason,
    CommandRef,
    CommandRequest,
    CommandResult,
    describe_commands,
)
from remote_watch.commands.registry import CommandContext
from remote_watch.commands.sqlite_store import SQLiteCommandStore
from remote_watch.commands.storage import StoreError, StoreRole
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка согласованных прав, реестра и двух журналов
#------------------------------------------------------------------------------------------------------------------
def configured(
    path: Path,
    registry: CommandRegistry,
) -> tuple[Rig, CommandDispatcher]:

    """Bind an application registry to matching principal/source permissions and a real client journal.

    :param path: Temporary directory for isolated persistent journals.
    :type path: Path

    :param registry: Application-owned immutable command registry.
    :type registry: CommandRegistry

    :return: Isolated hub/client fixtures and their matching dispatcher.
    :rtype: tuple[Rig, CommandDispatcher]
    """

    # path — временный каталог отдельных постоянных журналов.
    # registry — реестр пользовательских обработчиков и правил проверки.

    rig = Rig(path)
    scopes = frozenset(spec.required_scope for spec in registry.specs)
    config = rig.hub.config
    rig.hub.config = replace(config,
        principals=(replace(config.principals[0], scopes=scopes),),
        sources=(replace(config.sources[0], access=(replace(config.sources[0].access[0], scopes=scopes),)),))
    rig.registration = replace(rig.registration, capabilities=describe_commands(registry))
    rig.client = CommandClient(rig.registration, rig.transport, rig.local, clock=lambda: rig.now)
    return rig, CommandDispatcher(registry, rig.client, poll_interval=0.01, retry_interval=0.01,
                                  shutdown_timeout=0.2)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Конечное ожидание наблюдаемого результата теста
#------------------------------------------------------------------------------------------------------------------
async def until(
    predicate: Callable[[], bool],
    timeout: float = 10.0,
) -> None:

    """Wait for an observable effect under a fixed test deadline.

    :param predicate: Thread-safe predicate observing test progress, possibly reading SQLite.
    :type predicate: Callable[[], bool]

    :param timeout: Test observation budget, independent of command execution deadlines.
    :type timeout: float
    """

    # predicate — потокобезопасная проверка, в том числе чтение постоянного журнала.
    # timeout — предел ожидания теста, не разрешение продлевать срок самой команды.

    deadline = monotonic() + timeout

    # SQLite может ждать блокировку или диск. Наблюдение из event loop само
    # задерживало обработку команд в медленном CI. Выносим чтение в поток и ждём
    # его окончания; одновременно существует только одна такая проверка.
    while True:
        try:
            if await asyncio.to_thread(predicate):
                return
        except StoreError as error:
            # Повторяем только штатную занятость журнала. Ошибки данных и диска
            # остаются ошибками теста, а не скрываются за общим timeout.
            if error.args != ("store busy",):
                raise
        if monotonic() >= deadline:
            raise AssertionError("dispatcher did not reach expected state")
        await asyncio.sleep(0.02)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка прав и свежести перед атомарной записью с cursor
#------------------------------------------------------------------------------------------------------------------
async def submit(
    rig: Rig,
    name: str = 'resume_load',
    number: int = 1,
    arguments: Mapping[str, str] | None = None,
) -> CommandRequest:

    """Submit a synthetic source event using the exact registered session.

    :param rig: Isolated hub, client, clocks and temporary journals.
    :type rig: Rig

    :param name: Exact registered application command name.
    :type name: str

    :param number: Synthetic command and provider event number.
    :type number: int

    :param arguments: Bounded immutable textual command arguments.
    :type arguments: Mapping[str, str] | None

    :return: Synthetic command addressed to the current application session.
    :rtype: CommandRequest
    """

    # rig — подставная среда с отдельными журналами и управляемыми часами.
    # name — точное имя команды приложения.
    # number — номер подставной команды и события провайдера.
    # arguments — ограниченный набор строковых аргументов команды.

    request = replace(rig.request(number), name=name, arguments={} if arguments is None else arguments)

    for _ in range(100):
        try:
            await rig.submit(request)
            return request
        except CommandError as error:
            if error.code != "busy":
                raise
            await asyncio.sleep(0.005)
    raise AssertionError("hub store remained busy")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Формы callbacks и проверка их потока либо event loop
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", [
    "sync", "async", "partial", "async_partial", "bound", "callable", "async_callable",
])
def test_dispatcher_callback_forms(
    tmp_path: Path,
    kind: str,
) -> None:

    """Run supported callback forms on their documented thread or loop.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param kind: Selected callback form or ownership scenario.
    :type kind: str
    """

    # tmp_path — отдельный временный каталог теста.
    # kind — проверяемая форма обработчика или режим владения ресурсами.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Record the execution owner and verify the final durable result."""

        calls = []
        loop = asyncio.get_running_loop()
        owner_thread = threading.get_ident()


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной обработчик для проверки рабочего потока
        #----------------------------------------------------------------------------------------------------------
        def sync(value: str = 'done') -> str:

            """Record synchronous worker ownership.

            :param value: Synthetic callback response.
            :type value: str

            :return: Short confirmation of the synthetic application state change.
            :rtype: str
            """

            # value — подставной ответ обработчика.

            calls.append((threading.get_ident(), None))
            return value
        #----------------------------------------------------------------------------------------------------------


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной обработчик для проверки цикла приложения
        #----------------------------------------------------------------------------------------------------------
        async def asynchronous() -> str:

            """Record application-loop ownership.

            :return: Short confirmation of the synthetic application state change.
            :rtype: str
            """

            calls.append((threading.get_ident(), asyncio.get_running_loop()))
            return "done"
        #----------------------------------------------------------------------------------------------------------


        #----------------------------------------------------------------------------------------------------------
        # КЛАСС : Подставной вызываемый объект с обычным методом
        #----------------------------------------------------------------------------------------------------------
        class Handler:
            """Expose an ordinary callable object."""

            #------------------------------------------------------------------------------------------------------
            # СПЕЦИАЛЬНЫЙ МЕТОД : Вызов подставного обработчика
            #------------------------------------------------------------------------------------------------------
            def __call__(self) -> str:

                """Delegate to the synchronous test handler.

                :return: Short confirmation of the synthetic application state change.
                :rtype: str
                """

                return sync()
            #------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------


        #----------------------------------------------------------------------------------------------------------
        # КЛАСС : Подставной вызываемый объект с асинхронным методом
        #----------------------------------------------------------------------------------------------------------
        class AsyncHandler:
            """Expose an asynchronous callable object."""

            #------------------------------------------------------------------------------------------------------
            # СПЕЦИАЛЬНЫЙ МЕТОД : Вызов подставного обработчика
            #------------------------------------------------------------------------------------------------------
            async def __call__(self) -> str:

                """Delegate to the asynchronous test handler.

                :return: Short confirmation of the synthetic application state change.
                :rtype: str
                """

                return await asynchronous()
            #------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------


        callback = {"sync": sync, "async": asynchronous, "partial": partial(sync, "done"),
                    "async_partial": partial(asynchronous), "bound": Handler().__call__,
                    "callable": Handler(), "async_callable": AsyncHandler()}[kind]
        registry = CommandRegistry.from_callbacks({"resume_load": callback})
        rig, dispatcher = configured(tmp_path, registry)
        await rig.hub.start()
        await dispatcher.start()

        try:
            request = await submit(rig)
            await until(lambda: rig.local.get(request.ref) is not None and rig.local.get(request.ref).acknowledged)
            assert len(calls) == 1
            if kind.startswith("async"):
                assert calls[0] == (owner_thread, loop)
            else:
                assert calls[0][0] != owner_thread and calls[0][1] is None
            assert rig.store.get(request.ref).record.result.text == "done"
            assert dispatcher.stats.completed == 1
        finally:
            await dispatcher.close()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка аргументов до grant и удержание зависшего валидатора
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["valid", "invalid", "return_value", "exception", "slow"])
def test_dispatcher_argument_validation(
    tmp_path: Path,
    case: str,
) -> None:

    """Validate arguments before grant and retain the worker occupied by a slow validator.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param case: Selected malformed request or response scenario.
    :type case: str
    """

    # tmp_path — отдельный временный каталог теста.
    # case — выбранный сценарий нарушения формата или границ запроса.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise pure validators without letting failures invoke the application callback."""

        calls = []
        entered = threading.Event()
        release = threading.Event()
        validator_threads = []


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Чистая проверка аргументов без запуска обработчика
        #----------------------------------------------------------------------------------------------------------
        def validate(arguments: Mapping[str, str]) -> None:

            """Validate one bounded integer argument on the callback worker.

            :param arguments: Bounded immutable textual command arguments.
            :type arguments: Mapping[str, str]
            """

            # arguments — ограниченный набор строковых аргументов команды.

            validator_threads.append(threading.get_ident())

            if case == "slow":
                entered.set()
                release.wait(2)

            if case == "exception":
                raise RuntimeError("private validator details")

            if case == "return_value":
                return False

            if set(arguments) != {"count"} or arguments["count"] != "2":
                raise ValueError("private invalid value")
        #----------------------------------------------------------------------------------------------------------


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Изменение только подставного состояния приложения
        #----------------------------------------------------------------------------------------------------------
        def callback(context: CommandContext) -> str:

            """Record the validated context without converting its arguments implicitly.

            :param context: Validated application context with exact target and arguments.
            :type context: CommandContext

            :return: Short confirmation of the synthetic application state change.
            :rtype: str
            """

            # context — проверенный контекст с точной целью и аргументами.

            calls.append(context)
            return context.arguments["count"]
        #----------------------------------------------------------------------------------------------------------


        registry = CommandRegistry(specs=(CommandSpec(name="resume_load", callback=callback,
            takes_context=True, validate_arguments=validate, timeout=0.05),))
        rig, dispatcher = configured(tmp_path, registry)
        await rig.hub.start()
        await dispatcher.start()

        try:
            request = await submit(rig, arguments={"count": "bad" if case == "invalid" else "2"})
            await until(lambda: rig.store.get(request.ref).record.result is not None)
            result = rig.store.get(request.ref).record.result
            assert validator_threads[0] != threading.get_ident()
            if case == "valid":
                assert len(calls) == 1 and calls[0].identity == rig.identity
                assert result.text == "2"
            else:
                assert calls == [] and result.outcome.value == "rejected"
                assert result.reason is CommandReason.INVALID_ARGUMENTS
                assert result.text is None
                if case == "slow":
                    assert entered.is_set() and dispatcher.stats.running
                    await submit(rig, number=2, arguments={"count": "2"})
                    await asyncio.sleep(0.05)
                    assert len(validator_threads) == 1
        finally:
            release.set()
            await dispatcher.close()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасные неизвестные исходы и повтор только доставки результата
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["exception", "bad_result", "cancelled", "lost_result"])
def test_dispatcher_outcome_and_retry(
    tmp_path: Path,
    case: str,
) -> None:

    """Keep exceptions private and retry result delivery without invoking the callback again.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param case: Selected malformed request or response scenario.
    :type case: str
    """

    # tmp_path — отдельный временный каталог теста.
    # case — выбранный сценарий нарушения формата или границ запроса.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Count side effects across callback failures and a lost durable receipt."""

        calls = []


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Изменение только подставного состояния приложения
        #----------------------------------------------------------------------------------------------------------
        def callback() -> object:

            """Change synthetic state before returning or raising.

            :return: Synthetic handler value used to exercise result normalization.
            :rtype: object
            """

            calls.append(1)

            if case == "exception":
                raise RuntimeError("secret from application")

            if case == "cancelled":
                raise asyncio.CancelledError
            return object() if case == "bad_result" else "done"
        #----------------------------------------------------------------------------------------------------------


        rig, dispatcher = configured(tmp_path, CommandRegistry.from_callbacks({"resume_load": callback}))

        if case == "lost_result":
            rig.transport.lose = "result"
        await rig.hub.start()
        await dispatcher.start()

        try:
            request = await submit(rig)
            await until(lambda: rig.local.get(request.ref) is not None and rig.local.get(request.ref).acknowledged)
            assert calls == [1]
            result = rig.store.get(request.ref).record.result
            assert result.outcome.value == ("completed" if case == "lost_result" else "unknown")
            assert result.text == ("done" if case == "lost_result" else None)
            assert not rig.local.get(request.ref).execution_active
            assert not rig.store.get(request.ref).execution_active
        finally:
            await dispatcher.close()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Удержание слота до фактического окончания после timeout
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["sync", "async", "lost_release"])
def test_dispatcher_timeout_keeps_slot(
    tmp_path: Path,
    kind: str,
) -> None:

    """Retain a timed-out execution until its real termination and deliver release durably.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param kind: Selected callback form or ownership scenario.
    :type kind: str
    """

    # tmp_path — отдельный временный каталог теста.
    # kind — проверяемая форма обработчика или режим владения ресурсами.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Hold a callback past timeout and prove that a second command cannot overtake it."""

        entered = threading.Event()
        released = threading.Event()
        async_release = asyncio.Event()
        calls = []


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Изменение только подставного состояния приложения
        #----------------------------------------------------------------------------------------------------------
        def callback() -> str:

            """Wait under test control without cooperating with cancellation.

            :return: Short confirmation of the synthetic application state change.
            :rtype: str
            """

            calls.append(1)
            entered.set()
            released.wait(2)
            return "late completion"
        #----------------------------------------------------------------------------------------------------------


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной обработчик для проверки цикла приложения
        #----------------------------------------------------------------------------------------------------------
        async def asynchronous() -> str:

            """Suppress the first cancellation to model asynchronous cleanup still in progress.

            :return: Short confirmation of the synthetic application state change.
            :rtype: str
            """

            calls.append(1)
            entered.set()

            try:
                await async_release.wait()
            except asyncio.CancelledError:
                await async_release.wait()
            return "late completion"
        #----------------------------------------------------------------------------------------------------------


        registry = CommandRegistry(specs=(CommandSpec(name="resume_load",
            callback=asynchronous if kind == "async" else callback, timeout=0.05),))
        rig, dispatcher = configured(tmp_path, registry)
        await rig.hub.start()
        await dispatcher.start()

        try:
            first = await submit(rig)
            await until(lambda: rig.store.get(first.ref).record.result is not None)
            assert rig.store.get(first.ref).record.result.reason is CommandReason.TIMEOUT
            assert entered.is_set() and dispatcher.stats.running
            assert rig.store.get(first.ref).execution_active and rig.local.get(first.ref).execution_active
            second = await submit(rig, number=2)
            await asyncio.sleep(0.05)
            assert calls == [1]
            if kind == "lost_release":
                rig.transport.lose = "release"
            released.set()
            async_release.set()
            await until(lambda: rig.store.get(second.ref).record.result is not None)
            assert len(calls) == 2
            assert rig.store.get(first.ref).record.result.outcome.value == "unknown"
            assert not rig.store.get(first.ref).execution_active
            assert not rig.local.get(first.ref).execution_active
        finally:
            released.set()
            async_release.set()
            await dispatcher.close()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Конечная остановка с сохранением живого исполнения
#------------------------------------------------------------------------------------------------------------------
def test_dispatcher_shutdown_pins_live_callback(tmp_path: Path) -> None:

    """Bound shutdown without declaring a still-running synchronous handler terminated.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Stop while a callback owns its execution and inspect the retained hub record."""

        entered = threading.Event()
        released = threading.Event()


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Изменение только подставного состояния приложения
        #----------------------------------------------------------------------------------------------------------
        def callback() -> None:

            """Remain active until the test explicitly releases the worker."""

            entered.set()
            released.wait(2)
        #----------------------------------------------------------------------------------------------------------


        rig, dispatcher = configured(tmp_path, CommandRegistry.from_callbacks({"resume_load": callback}))
        await rig.hub.start()
        await dispatcher.start()

        try:
            request = await submit(rig)
            await until(entered.is_set)
            before = monotonic()
            assert not await dispatcher.close()
            assert monotonic() - before < 1
            assert rig.store.get(request.ref).record.result.outcome.value == "unknown"
            assert rig.store.get(request.ref).execution_active
            assert dispatcher.stats.closed and dispatcher.stats.running
        finally:
            released.set()
            await until(lambda: not dispatcher.stats.running)
            await dispatcher.close()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка и изменение Event через словарь и partial
#------------------------------------------------------------------------------------------------------------------
def test_watcher_commands_and_partial_events(tmp_path: Path) -> None:

    """Integrate arbitrary status and mutation names through the original mapping/partial API.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Change an application Event and distinguish its acknowledgement from business completion."""

        stopped = threading.Event()
        registry = CommandRegistry.from_callbacks({"suspend_load": partial(stopped.set),
            "resume_load": partial(stopped.clear), "check_load": lambda: str(stopped.is_set())})
        rig, _ = configured(tmp_path, registry)
        watcher = RemoteWatcher(WatcherConfig(identity=rig.identity, commands=registry), command_client=rig.client)
        await rig.hub.start()
        await watcher.astart()

        try:
            for number, name, expected in ((1, "suspend_load", True), (2, "check_load", True),
                                           (3, "resume_load", False)):
                request = await submit(rig, name=name, number=number)
                await until(lambda: rig.store.get(request.ref).record.result is not None)
                assert stopped.is_set() is expected
                result = rig.store.get(request.ref).record.result
                assert result.text == ("True" if name == "check_load" else None)
            assert watcher.command_stats.completed == 3
        finally:
            await watcher.astop()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет несовпадения реестра, scope и Identity клиента
#------------------------------------------------------------------------------------------------------------------
def test_dispatcher_exact_registry_binding(tmp_path: Path) -> None:

    """Reject a client registration that advertises different callback permissions.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.

    rig = Rig(tmp_path)
    registry = CommandRegistry.from_callbacks({"resume_load": lambda: None})
    with pytest.raises(ValueError, match="registration"):
        CommandDispatcher(registry, rig.client)
    foreign = replace(rig.identity, instance_id="foreign")
    with pytest.raises(ValueError, match="identity"):
        RemoteWatcher(WatcherConfig(identity=foreign, commands=registry), command_client=rig.client)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет рекурсивной остановки watcher из обработчика
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["sync", "async"])
def test_watcher_rejects_callback_lifecycle(
    tmp_path: Path,
    kind: str,
) -> None:

    """Reject recursive watcher shutdown from either callback execution context.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param kind: Selected callback form or ownership scenario.
    :type kind: str
    """

    # tmp_path — отдельный временный каталог теста.
    # kind — проверяемая форма обработчика или режим владения ресурсами.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Run a lifecycle misuse without blocking the callback or notification worker."""

        owner = []


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной обработчик для проверки рабочего потока
        #----------------------------------------------------------------------------------------------------------
        def sync() -> None:

            """Attempt unsupported watcher shutdown from the callback worker."""

            owner[0].stop()
        #----------------------------------------------------------------------------------------------------------


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной обработчик для проверки цикла приложения
        #----------------------------------------------------------------------------------------------------------
        async def asynchronous() -> None:

            """Attempt unsupported watcher shutdown from the application-loop callback."""

            await owner[0].astop()
        #----------------------------------------------------------------------------------------------------------


        registry = CommandRegistry.from_callbacks({"resume_load": sync if kind == "sync" else asynchronous})
        rig, _ = configured(tmp_path, registry)
        watcher = RemoteWatcher(WatcherConfig(identity=rig.identity, commands=registry), command_client=rig.client)
        owner.append(watcher)
        await rig.hub.start()
        await watcher.astart()

        try:
            request = await submit(rig)
            await until(lambda: rig.store.get(request.ref).record.result is not None)
            assert rig.store.get(request.ref).record.result.reason is CommandReason.CALLBACK_ERROR
            assert not watcher.command_stats.closed
        finally:
            await watcher.astop()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Доставка уведомления при занятом командном потоке
#------------------------------------------------------------------------------------------------------------------
def test_watcher_sync_commands_leave_notifications_live(tmp_path: Path) -> None:

    """Use separate command and notification loops while a synchronous callback is blocked.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.

    pytest.importorskip("aiohttp")
    from test_watcher import MemoryChannel

    from remote_watch import Destination, Route
    from remote_watch.adapters.command_http import HttpsCommandTransport
    from remote_watch.gateway.command_server import CommandHubServer

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Drive the synchronous watcher over HTTP from a separately owned hub loop."""

        entered = threading.Event()
        released = threading.Event()
        channel = MemoryChannel()


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Изменение только подставного состояния приложения
        #----------------------------------------------------------------------------------------------------------
        def callback() -> None:

            """Hold the command worker while the notification worker delivers a log entry."""

            entered.set()
            released.wait(2)
        #----------------------------------------------------------------------------------------------------------


        registry = CommandRegistry.from_callbacks({"resume_load": callback})
        rig, _ = configured(tmp_path, registry)
        server = CommandHubServer(rig.hub)
        await server.start(allow_loopback_http=True)
        transport = HttpsCommandTransport(f"http://127.0.0.1:{server.port}", APP_TOKEN, allow_loopback_http=True)
        client = CommandClient(rig.registration, transport, rig.local, clock=lambda: rig.now)
        config = WatcherConfig(identity=rig.identity, commands=registry,
            destinations=(Destination(destination_id="fake", channel_factory=lambda: channel),),
            routes=(Route(destination_ids=("fake",)),))
        watcher = RemoteWatcher(config, command_client=client)

        try:
            await asyncio.to_thread(watcher.start)
            request = await submit(rig)
            await until(entered.is_set)
            watcher.logger.error("notification while command is blocked")
            await until(lambda: len(channel.deliveries) == 1)
            assert watcher.command_stats.running
            released.set()
            await until(lambda: rig.store.get(request.ref).record.result is not None)
            await asyncio.to_thread(watcher.stop)
            assert channel.closed
        finally:
            released.set()
            await asyncio.to_thread(watcher.stop)
            await server.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Явный выбор цикла приложения для async-обработчика
#------------------------------------------------------------------------------------------------------------------
def test_watcher_sync_start_rejects_async_callback(tmp_path: Path) -> None:

    """Require the application to choose its event loop explicitly for asynchronous callbacks.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Изменение только подставного состояния приложения
    #--------------------------------------------------------------------------------------------------------------
    async def callback() -> None:

        """Represent a callback requiring the application's event loop."""

        raise AssertionError("must not run during startup")
    #--------------------------------------------------------------------------------------------------------------


    registry = CommandRegistry.from_callbacks({"resume_load": callback})
    rig, _ = configured(tmp_path, registry)
    watcher = RemoteWatcher(WatcherConfig(identity=rig.identity, commands=registry), command_client=rig.client)
    with pytest.raises(ValueError, match="astart"):
        watcher.start()
    assert watcher.logger.handlers == []
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет callback после истечения срока перед входом в поток
#------------------------------------------------------------------------------------------------------------------
def test_dispatcher_final_worker_deadline(tmp_path: Path) -> None:

    """Refuse callback invocation when the budget expires between scheduling and worker entry.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Advance only the command clock before the final in-worker permission check."""

        calls = []
        rig, dispatcher = configured(
            tmp_path, CommandRegistry.from_callbacks({"resume_load": lambda: calls.append(1)}))
        original = dispatcher._invoke_sync


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Исчерпание срока перед входом в рабочий поток
        #----------------------------------------------------------------------------------------------------------
        def delayed(
            spec: CommandSpec,
            ticket: Any,
        ) -> Any:

            """Spend the lease before invoking the production worker guard.

            :param spec: Exact locally registered command specification.
            :type spec: CommandSpec

            :param ticket: Exact ticket returned by this client, never a reconstructed copy.
            :type ticket: Any

            :return: Outcome of the worker guard after the command lease has expired.
            :rtype: Any
            """

            # spec — локальное описание именно этой команды.
            # ticket — тот же объект разрешения, который вернул этот клиент.

            rig.now += 61
            return original(spec, ticket)
        #----------------------------------------------------------------------------------------------------------


        dispatcher._invoke_sync = delayed
        await rig.hub.start()
        await dispatcher.start()

        try:
            request = await submit(rig)
            await until(lambda: rig.store.get(request.ref).record.result is not None)
            assert calls == []
            assert rig.store.get(request.ref).record.result.reason is CommandReason.TIMEOUT
        finally:
            await dispatcher.close()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Разбор восстановленных записей без повторного callback
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("started", [False, True])
def test_dispatcher_recovered_journal(
    tmp_path: Path,
    started: bool,
) -> None:

    """Retire recovered pre-grant records while retaining uncertain executions after restart.

    :param tmp_path: Isolated directory containing both persistent journals.
    :type tmp_path: Path

    :param started: Whether the local client persisted STARTED before losing its process.
    :type started: bool
    """

    # tmp_path — отдельный каталог постоянных журналов теста.
    # started — локальный STARTED был сохранён до имитации перезапуска.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Смена поколения клиента при сохранении базы и неизвестного результата
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Reopen the real client journal with a new generation and inspect reconciliation."""

        calls = []
        registry = CommandRegistry.from_callbacks({"resume_load": lambda: calls.append(1)})
        rig, _ = configured(tmp_path, registry)
        await rig.start()

        try:
            request = await submit(rig)
            if started:
                assert await rig.client.acquire() is not None
            else:
                # Hub уже сохранил STARTED, но клиент не получил grant: он точно
                # не вызывал callback. Эти два журнала восстанавливаются по-разному.
                rig.transport.lose = "claim"
                with pytest.raises(CommandError, match="unavailable"):
                    await rig.client.acquire()
            await rig.client.close()

            # Старая регистрация истекла; новый процесс получает новую generation.
            rig.now += 61
            rig.registration = replace(rig.registration, session_id="d" * 32)
            local = SQLiteCommandStore(tmp_path / "client.sqlite", owner_id="app", generation="d" * 32,
                                       role=StoreRole.CLIENT, clock=lambda: rig.now)
            rig.client = CommandClient(rig.registration, DirectTransport(rig.hub), local, clock=lambda: rig.now)
            await rig.client.start()
            await rig.client.flush_results()

            stored = await rig.client.lookup(request.ref)
            pending = await rig.client.pending()
            assert calls == []
            assert stored.record.result.reason is CommandReason.RESTART
            assert stored.record.result.outcome.value == ("unknown" if started else "expired")
            assert stored.execution_active is started
            assert stored.acknowledged is not started
            assert bool(pending) is started

            # Даже новый dispatcher не должен извлекать старую запись как новую команду.
            assert await rig.client.acquire() is None
            assert calls == []
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Восстановление локального слота после сохранения результата и отказа очистки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lose_ack", [False, True])
def test_dispatcher_result_persisted_before_release_failure(
    tmp_path: Path,
    lose_ack: bool,
) -> None:

    """Recover an active in-memory ticket from durable completion without replaying its callback.

    :param tmp_path: Isolated journals used by the production client and dispatcher.
    :type tmp_path: Path

    :param lose_ack: Also lose the local acknowledgement response after its commit.
    :type lose_ack: bool
    """

    # tmp_path — отдельные журналы настоящего клиента и исполнителя.
    # lose_ack — дополнительно потерять подтверждение локального ACK после его записи.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Отказ между постоянным результатом и освобождением ticket в памяти
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Inject a finite cleanup failure and require the next command to make progress."""

        calls = []
        rig, dispatcher = configured(
            tmp_path, CommandRegistry.from_callbacks({"resume_load": lambda: calls.append(1)}))
        release = rig.local.release_execution
        acknowledge = rig.local.acknowledge
        release_failed = False
        ack_failed = False

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Однократный отказ после фиксации COMPLETED
        #----------------------------------------------------------------------------------------------------------
        def fail_release(
            ref: CommandRef,
            claim_id: str,
        ) -> None:

            """Fail once after the preceding FINISH has persisted the callback result.

            :param ref: Exact reference of the completed command.
            :type ref: CommandRef

            :param claim_id: Identifier of the original execution claim.
            :type claim_id: str
            """

            # ref — точная ссылка на завершённую команду.
            # claim_id — идентификатор исходного разрешения на исполнение.

            nonlocal release_failed
            if not release_failed:
                release_failed = True
                raise CommandError("unavailable")
            release(ref, claim_id)
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Потеря ответа уже выполненной операции ACK
        #----------------------------------------------------------------------------------------------------------
        def fail_ack(result: CommandResult) -> None:

            """Persist acknowledgement before optionally losing its first response.

            :param result: Exact completed result acknowledged by the hub.
            :type result: CommandResult
            """

            # result — точный результат, подтверждённый центральным hub.

            nonlocal ack_failed
            acknowledge(result)
            if lose_ack and not ack_failed:
                ack_failed = True
                raise CommandError("unavailable")
        #----------------------------------------------------------------------------------------------------------

        rig.local.release_execution = fail_release
        rig.local.acknowledge = fail_ack
        await rig.hub.start()
        await dispatcher.start()

        try:
            first = await submit(rig)
            await until(lambda: rig.store.get(first.ref).record.result is not None)
            second = await submit(rig, number=2)
            await until(lambda: rig.store.get(second.ref).record.result is not None)
            assert calls == [1, 1]
            assert release_failed and ack_failed is lose_ack
            assert rig.local.get(first.ref).acknowledged
            assert not dispatcher.stats.closed
        finally:
            await dispatcher.close()
            await rig.hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасная причина старта без исходной цепочки исключений
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["dependency", "import", "denied", "certificate", "tls", "timeout",
                                  "connection", "sqlite", "store", "os", "value", "unknown", "mutated", "custom"])
def test_sync_startup_cause(
    tmp_path: Path,
    mode: str,
) -> None:

    """Retain safe startup classification without leaking the original exception chain.

    :param tmp_path: Isolated command journal directory.
    :type tmp_path: Path

    :param mode: Selected worker failure category or corrupted public error attributes.
    :type mode: str
    """

    # tmp_path — только временный журнал, без рабочего gateway.
    # mode — выбранный отказ до начала обработки команд.

    registry = CommandRegistry.from_callbacks({"status": lambda: "ready"})
    rig, _ = configured(tmp_path, registry)
    # Подставной маркер имитирует секрет сразу в тексте, notes и вложенной причине.
    # Проверяем стандартный traceback целиком, а не только str верхней ошибки.
    categories = {"dependency": ModuleNotFoundError, "import": ImportError,
                  "certificate": ssl.SSLCertVerificationError, "tls": ssl.SSLError,
                  "timeout": TimeoutError, "connection": ConnectionError,
                  "sqlite": sqlite3.Error, "store": StoreError, "os": OSError,
                  "value": ValueError, "unknown": RuntimeError}
    failure = (categories[mode]("PRIVATE") if mode in categories
               else CommandError("denied", http_status=403))
    if mode == "custom":
        failure = type("PRIVATE_CLASS", (RuntimeError,), {})("PRIVATE")
    failure.__cause__ = RuntimeError("PRIVATE_CHAIN")
    if hasattr(failure, "add_note"):
        failure.add_note("PRIVATE_NOTE")
    if mode == "mutated":
        failure.code = ["PRIVATE"]
        failure.http_status = "PRIVATE"
        failure.args = ("PRIVATE",)
    rig.client.start = AsyncMock(side_effect=failure)
    watcher = RemoteWatcher(WatcherConfig(identity=rig.identity, commands=registry), command_client=rig.client)

    with pytest.raises(CommandError, match="unavailable") as caught:
        watcher.start()
    cause = caught.value.__cause__
    assert cause is not failure and type(cause) is (RuntimeError if mode == "custom" else type(failure))
    assert cause.__cause__ is None and cause.__context__ is None and cause.__traceback__ is None
    formatted = "".join(traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__))
    assert "PRIVATE" not in formatted
    if mode in {"denied", "mutated"}:
        assert cause.code == ("denied" if mode == "denied" else "unavailable")
        assert cause.http_status == (403 if mode == "denied" else None)
    assert watcher.logger.handlers == []
    watcher.stop()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_dispatcher не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
