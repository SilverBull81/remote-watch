# Проверки маршрутизации, прав, сроков и отказов двух постоянных журналов.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-203048
#
# Классы:
# -> DirectTransport: Подставной транспорт с потерей уже записанного ответа.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие принадлежащих объекту ресурсов.
#    -> exchange(): Один обмен с проверкой ответа и без скрытого retry.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.
#
# -> Rig: Согласованные часы, права и два настоящих журнала SQLite.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> start(): Запуск объекта и подготовка состояния.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.
#    -> request(): Подставная команда для точной сессии приложения.
#    -> submit(): Проверка прав и свежести перед атомарной записью с cursor.
#
# Тесты:
# -> test_command_end_to_end(): Полный обмен с callback, квитанцией и подтверждением источника.
# -> test_command_acl_and_freshness(): Отказ чужим и просроченным сообщениям с сохранением cursor.
# -> test_command_credentials_separate(): Разделение секретов источника, приложения и relay.
# -> test_command_session_fencing(): Запрет второй живой цели и возрождения истёкшей сессии.
# -> test_command_lost_response_no_reexecution(): Потеря ответа после commit без повторного callback.
# -> test_command_capacity_and_scope(): Ограничение хранения и запрет повышения прав.
# -> test_command_heartbeat_does_not_extend_command(): Независимость срока команды от heartbeat.
# -> test_command_poll_bounds_and_shutdown(): Один poll на сессию и пробуждение при остановке.
# -> test_command_worker_cancellation(): Занятый рабочий поток журнала после отмены ожидающего caller.
# -> test_command_two_apps_and_chats(): Два приложения и два чата на одном hub.
# -> test_command_fail_closed_boundaries(): Отказ при потере достоверного времени, прав или корреляции.
# -> test_command_background_heartbeat(): Автоматическое продление регистрации клиентом.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from remote_watch.commands._worker import StoreWorker
from remote_watch.commands.client import CommandClient
from remote_watch.commands.hub import CommandHub
from remote_watch.commands.hub_config import CommandAccess, CommandHubConfig, CommandPrincipal, CommandSource
from remote_watch.commands.protocol import (
    CommandCapability,
    CommandMessage,
    CommandRef,
    CommandRegistration,
    CommandRequest,
    callback_result,
)
from remote_watch.commands.sqlite_store import SQLiteCommandStore
from remote_watch.commands.storage import StoreRole
from remote_watch.commands.time import TimeSample, TrustedClock
from remote_watch.commands.transport import CommandError, CommandOffer
from remote_watch.events import Identity

APP_TOKEN = "application_command_secret_" + "a" * 32
SOURCE_TOKEN = "source_command_secret_" + "b" * 32
EPOCH = "a" * 32
SESSION = "b" * 32


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Подставной транспорт с потерей уже записанного ответа
#------------------------------------------------------------------------------------------------------------------
class DirectTransport:
    """Inject response loss while preserving real hub and journal behavior."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        hub: CommandHub,
    ) -> None:

        """Bind the synthetic transport without opening the hub.

        :param hub: Independently configured command hub.
        :type hub: CommandHub
        """

        # hub — отдельно настроенный командный hub.

        self.hub = hub
        self.lose: str | None = None
        self.delay = 0.0
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Keep the fake transport free from network resources."""
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Один обмен с проверкой ответа и без скрытого retry
    #--------------------------------------------------------------------------------------------------------------
    async def exchange(
        self,
        operation: str,
        message: CommandMessage,
    ) -> CommandMessage | CommandOffer | None:

        """Forward an authenticated call and optionally lose its committed response.

        :param operation: Single operation executed within the documented limits.
        :type operation: str

        :param message: Typed request for the selected command operation.
        :type message: CommandMessage

        :return: Validated response, or None when polling found no work.
        :rtype: CommandMessage | CommandOffer | None
        """

        # operation — одна операция в пределах доступной ёмкости.
        # message — типизированное сообщение выбранной командной операции.

        method = self.hub.finish if operation == "result" else getattr(self.hub, operation)
        response = await method(APP_TOKEN, message)

        if operation == self.lose:
            self.lose = None
            raise CommandError("unavailable")
        return response
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Leave hub ownership with the test fixture."""
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Согласованные часы, права и два настоящих журнала SQLite
#------------------------------------------------------------------------------------------------------------------
class Rig:
    """Own deterministic clocks and two independent real SQLite journals."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        path: Path,
        **overrides: Any,
    ) -> None:

        """Prepare a single application, source ACL and matching command client.

        :param path: Temporary directory for isolated persistent journals.
        :type path: Path

        :param overrides: Explicit test overrides of hub bounds.
        :type overrides: Any
        """

        # path — временный каталог отдельных постоянных журналов.
        # overrides — переопределяемые тестом пределы hub.

        self.now = 100.0
        self.identity = Identity(service="loader", environment="test", region="ru", host="vm", instance_id="one")
        self.registration = CommandRegistration(identity=self.identity, session_id=SESSION,
            capabilities=(CommandCapability(name="resume_load", required_scope="control"),))
        config = CommandHubConfig(
            principals=(CommandPrincipal(name="app", token=APP_TOKEN, identities=(self.identity,),
                                         scopes=frozenset({"control"})),),
            sources=(CommandSource(source_id="fake", token=SOURCE_TOKEN, access=(
                CommandAccess(actor_id="owner", conversation_id="chat", identity=self.identity,
                              scopes=frozenset({"control"})),)),), poll_timeout=0.01, **overrides)
        self.trusted = TrustedClock(clock=lambda: self.now)
        self.trusted.install(TimeSample(lower_utc=1000, upper_utc=1000.1, observed_at=self.now))
        self.store = SQLiteCommandStore(path / "hub.sqlite", owner_id="hub", generation=EPOCH,
                                       role=StoreRole.HUB, clock=lambda: self.now)
        self.hub = CommandHub(config, self.store, epoch=EPOCH, trusted_clock=self.trusted, clock=lambda: self.now)
        self.transport = DirectTransport(self.hub)
        self.local = SQLiteCommandStore(path / "client.sqlite", owner_id="app", generation=SESSION,
                                       role=StoreRole.CLIENT, clock=lambda: self.now)
        self.client = CommandClient(self.registration, self.transport, self.local, clock=lambda: self.now)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск объекта и подготовка состояния
    #--------------------------------------------------------------------------------------------------------------
    async def start(self) -> None:

        """Open hub and client in the current test event loop."""

        await self.hub.start()
        await self.client.start()
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close both owners even when the client fails to close."""

        try:
            await self.client.close()
        finally:
            await self.hub.close()
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подставная команда для точной сессии приложения
    #--------------------------------------------------------------------------------------------------------------
    def request(
        self,
        number: int = 1,
    ) -> CommandRequest:

        """Construct a synthetic provider intent bound to the exact live session.

        :param number: Synthetic command and provider event number.
        :type number: int

        :return: Synthetic command addressed to the current application session.
        :rtype: CommandRequest
        """

        # number — номер подставной команды и события провайдера.

        session = self.hub.target(SOURCE_TOKEN, self.identity)
        return CommandRequest(ref=CommandRef(identity=self.identity, session_id=session.session_id,
            hub_epoch=session.hub_epoch, command_id=f"{number:032x}"),
            source_id="fake", source_event_id=str(number),
            actor_id="owner", conversation_id="chat", name="resume_load")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка прав и свежести перед атомарной записью с cursor
    #--------------------------------------------------------------------------------------------------------------
    async def submit(
        self,
        request: CommandRequest,
    ) -> None:

        """Commit one in-order fake source event using the trusted UTC timestamp.

        :param request: Incoming request validated before dispatch.
        :type request: CommandRequest
        """

        # request — входящий запрос, проверяемый перед обработкой.

        cursor = await self.hub.source_cursor(SOURCE_TOKEN)
        await self.hub.submit(
            SOURCE_TOKEN, request, position=cursor + 1, expected_cursor=cursor, message_date=1000)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полный обмен с callback, квитанцией и подтверждением источника
#------------------------------------------------------------------------------------------------------------------
def test_command_end_to_end(tmp_path: Path) -> None:

    """Execute one fake callback through both durable journals and acknowledge the reply.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise registration, admission, claim, execution, receipt and source ACK."""

        rig = Rig(tmp_path)
        await rig.start()

        try:
            request = rig.request()
            await rig.submit(request)
            ticket = await rig.client.acquire()
            assert ticket is not None and rig.client.begin(ticket)
            calls = []
            calls.append(ticket.grant.request.name)
            with pytest.raises(CommandError, match="conflict"):
                rig.client.begin(ticket)
            result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "resumed")
            await rig.client.complete(ticket, result)
            rows = await rig.hub.results(SOURCE_TOKEN)
            assert len(rows) == 1 and rows[0].record.result == result
            await rig.hub.acknowledge(SOURCE_TOKEN, result)
            assert await rig.hub.results(SOURCE_TOKEN) == ()
            assert await rig.client.acquire() is None
            assert calls == ["resume_load"]
            assert rig.local.get(request.ref).acknowledged
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ чужим и просроченным сообщениям с сохранением cursor
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["actor", "chat", "instance", "session", "epoch", "name", "arguments", "stale"])
def test_command_acl_and_freshness(
    tmp_path: Path,
    field: str,
) -> None:

    """Commit rejected source positions without making them executable.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param field: Admission field changed by the rejection scenario.
    :type field: str
    """

    # tmp_path — отдельный временный каталог теста.
    # field — поле команды, подменяемое в сценарии отказа.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Change one admission boundary while retaining all remaining valid fields."""

        rig = Rig(tmp_path)
        await rig.start()

        try:
            request = rig.request()
            if field in ("actor", "chat", "name", "arguments"):
                key = {"actor": "actor_id", "chat": "conversation_id",
                       "name": "name", "arguments": "arguments"}[field]
                request = replace(request, **{key: {"x": "1"} if field == "arguments" else "foreign"})
            elif field == "instance":
                request = replace(
                    request, ref=replace(request.ref, identity=replace(rig.identity, instance_id="two")))
            elif field in ("session", "epoch"):
                request = replace(request, ref=replace(request.ref, **{
                    "session_id" if field == "session" else "hub_epoch": "c" * 32}))
            elif field == "stale":
                rig.now += 301
                rig.trusted.install(TimeSample(lower_utc=1301, upper_utc=1301.1, observed_at=rig.now))
            await rig.submit(request)
            assert await rig.hub.source_cursor(SOURCE_TOKEN) == 0
            assert rig.store.pending() == ()
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Разделение секретов источника, приложения и relay
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("token", [SOURCE_TOKEN, "relay_secret_" + "x" * 32, "", "bad"])
def test_command_credentials_separate(
    tmp_path: Path,
    token: str,
) -> None:

    """Reject provider and relay credentials at application registration.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param token: Command-only credential, never logged or echoed.
    :type token: str
    """

    # tmp_path — отдельный временный каталог теста.
    # token — отдельный секрет команд, не выводимый в сообщения и журнал.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Check both directions of the source/application credential boundary."""

        rig = Rig(tmp_path)
        await rig.start()

        try:
            with pytest.raises(CommandError, match="denied"):
                await rig.hub.register(token, rig.registration)
            with pytest.raises(CommandError, match="denied"):
                await rig.hub.source_cursor(APP_TOKEN)
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет второй живой цели и возрождения истёкшей сессии
#------------------------------------------------------------------------------------------------------------------
def test_command_session_fencing(tmp_path: Path) -> None:

    """Prevent duplicate live targets and refuse to revive expired process sessions.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Check registration idempotence, heartbeat and old-session fencing."""

        rig = Rig(tmp_path, session_ttl=10)
        await rig.start()

        try:
            initial = rig.client.session
            rig.now += 2
            assert (await rig.hub.register(APP_TOKEN, rig.registration)).remaining_ttl == 8
            other = replace(rig.registration, session_id="c" * 32)
            with pytest.raises(CommandError, match="conflict"):
                await rig.hub.register(APP_TOKEN, other)
            renewed = await rig.hub.heartbeat(APP_TOKEN, initial)
            assert renewed.remaining_ttl == 10
            rig.now += 11
            with pytest.raises(CommandError, match="stale_session"):
                await rig.hub.heartbeat(APP_TOKEN, initial)
            with pytest.raises(CommandError, match="stale_session"):
                await rig.hub.register(APP_TOKEN, rig.registration)
            assert (await rig.hub.register(APP_TOKEN, other)).session_id == other.session_id
            with pytest.raises(CommandError, match="stale_session"):
                await rig.hub.poll(APP_TOKEN, initial)
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Потеря ответа после commit без повторного callback
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("operation", ["claim", "result"])
def test_command_lost_response_no_reexecution(
    tmp_path: Path,
    operation: str,
) -> None:

    """Recover lost result receipts and refuse a second grant after a lost start response.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param operation: Single operation executed within the documented limits.
    :type operation: str
    """

    # tmp_path — отдельный временный каталог теста.
    # operation — одна операция в пределах доступной ёмкости.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Inject one failure strictly after hub commit."""

        rig = Rig(tmp_path)
        await rig.start()

        try:
            request = rig.request()
            await rig.submit(request)
            rig.transport.lose = operation
            if operation == "claim":
                with pytest.raises(CommandError, match="unavailable"):
                    await rig.client.acquire()
                with pytest.raises(CommandError, match="already_started"):
                    await rig.client.acquire()
                rig.now += 11
                rows = await rig.hub.results(SOURCE_TOKEN)
                assert rows[0].record.result.outcome.value == "unknown"
                assert rows[0].execution_active
            else:
                ticket = await rig.client.acquire()
                assert rig.client.begin(ticket)
                with pytest.raises(CommandError, match="unavailable"):
                    await rig.client.complete(ticket, callback_result(ticket.grant.request.ref,
                                                                      ticket.grant.claim_id, "done"))
                assert not rig.local.get(request.ref).acknowledged
                await rig.client.flush_results()
                assert rig.local.get(request.ref).acknowledged
                assert await rig.client.acquire() is None
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничение хранения и запрет повышения прав
#------------------------------------------------------------------------------------------------------------------
def test_command_capacity_and_scope(tmp_path: Path) -> None:

    """Bound pending records and reject capabilities outside application permissions.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Verify backpressure before source ACK and registration escalation."""

        rig = Rig(tmp_path, max_pending=1, max_sessions=1)
        await rig.start()

        try:
            await rig.submit(rig.request())
            with pytest.raises(CommandError, match="capacity"):
                await rig.submit(rig.request(2))
            assert await rig.hub.source_cursor(SOURCE_TOKEN) == 0
            forbidden = replace(rig.registration, session_id="c" * 32,
                capabilities=(CommandCapability(name="shell", required_scope="admin"),))
            with pytest.raises(CommandError, match="denied"):
                await rig.hub.register(APP_TOKEN, forbidden)
            rig.now += 61
            with pytest.raises(CommandError, match="capacity"):
                await rig.hub.register(APP_TOKEN, replace(rig.registration, session_id="c" * 32))
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимость срока команды от heartbeat
#------------------------------------------------------------------------------------------------------------------
def test_command_heartbeat_does_not_extend_command(tmp_path: Path) -> None:

    """Expire admitted work despite a renewed application lease.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Advance only deterministic time and inspect the terminal result."""

        rig = Rig(tmp_path)
        await rig.start()

        try:
            await rig.submit(rig.request())
            for _ in range(7):
                rig.now += 50
                await rig.hub.heartbeat(APP_TOKEN, rig.client.session)
            rows = await rig.hub.results(SOURCE_TOKEN)
            assert len(rows) == 1 and rows[0].record.result.outcome.value == "expired"
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Один poll на сессию и пробуждение при остановке
#------------------------------------------------------------------------------------------------------------------
def test_command_poll_bounds_and_shutdown(tmp_path: Path) -> None:

    """Wake a pending poll during shutdown and reject a second poll for the same session.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Use a real long wait which must be interrupted by hub closure."""

        rig = Rig(tmp_path)
        rig.hub.config = replace(rig.hub.config, poll_timeout=5)
        await rig.start()
        pending = asyncio.create_task(rig.hub.poll(APP_TOKEN, rig.client.session))

        try:
            await asyncio.sleep(0.02)
            with pytest.raises(CommandError, match="busy"):
                await rig.hub.poll(APP_TOKEN, rig.client.session)
            await rig.close()
            with pytest.raises(CommandError, match="closed"):
                await asyncio.wait_for(pending, 0.5)
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Занятый рабочий поток журнала после отмены ожидающего caller
#------------------------------------------------------------------------------------------------------------------
def test_command_worker_cancellation(tmp_path: Path) -> None:

    """Retain the single storage slot until a cancelled caller's actual commit finishes.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Block one store job and verify bounded rejection without blocking the loop."""

        rig = Rig(tmp_path)
        # Проверяем явную отмену caller, а не скорость создания базы или timeout.
        # Подготовке нужен обычный конечный срок, достаточный для диска Windows CI.
        worker = StoreWorker(rig.store, 10.0)
        await worker.open()
        entered = threading.Event()
        released = threading.Event()


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставная запись, переживающая отмену ожидающего caller
        #----------------------------------------------------------------------------------------------------------
        def blocked() -> None:

            """Simulate a storage operation whose caller may disappear."""

            entered.set()
            released.wait(10)
        #----------------------------------------------------------------------------------------------------------


        pending = asyncio.create_task(worker.call(blocked))

        try:
            assert await asyncio.to_thread(entered.wait, 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            for _ in range(3):
                with pytest.raises(CommandError, match="busy"):
                    await worker.call(lambda: None)
        finally:
            released.set()
            await worker.close(1)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Два приложения и два чата на одном hub
#------------------------------------------------------------------------------------------------------------------
def test_command_two_apps_and_chats(tmp_path: Path) -> None:

    """Route two application identities on one hub without sharing credentials or chat permissions.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path — отдельный временный каталог теста.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Admit commands from two chats and deny cross-application polling."""

        rig = Rig(tmp_path)
        other = replace(rig.identity, service="quotes", instance_id="two")
        other_token = "second_application_secret_" + "c" * 32
        principal = CommandPrincipal(name="second", token=other_token, identities=(other,),
                                     scopes=frozenset({"control"}))
        source = rig.hub.config.sources[0]
        rule = CommandAccess(actor_id="owner", conversation_id="second_chat", identity=other,
                             scopes=frozenset({"control"}))
        rig.hub.config = replace(rig.hub.config, principals=rig.hub.config.principals + (principal,),
                                 sources=(replace(source, access=source.access + (rule,)),))
        await rig.start()

        try:
            second = await rig.hub.register(
                other_token, replace(rig.registration, identity=other, session_id="c" * 32))
            first_request = rig.request()
            second_request = replace(first_request, ref=CommandRef(identity=other, session_id=second.session_id,
                hub_epoch=second.hub_epoch, command_id="d" * 32),
                conversation_id="second_chat", source_event_id="2")
            await rig.submit(first_request)
            await rig.submit(second_request)
            assert (await rig.hub.poll(APP_TOKEN, rig.client.session)).request == first_request
            assert (await rig.hub.poll(other_token, second)).request == second_request
            with pytest.raises(CommandError, match="stale_session"):
                await rig.hub.poll(APP_TOKEN, second)
            with pytest.raises(CommandError, match="denied"):
                await rig.hub.register(other_token, rig.registration)
            wrong_chat = replace(second_request, ref=replace(second_request.ref, command_id="e" * 32),
                                 source_event_id="3", conversation_id="chat")
            await rig.submit(wrong_chat)
            assert rig.store.get(wrong_chat.ref) is None
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ при потере достоверного времени, прав или корреляции
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("boundary", ["source_scope", "no_time", "old_date", "rtt", "rollback", "restart"])
def test_command_fail_closed_boundaries(
    tmp_path: Path,
    boundary: str,
) -> None:

    """Check source scope, trusted UTC, transport delay, monotonic rollback and restart fencing.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param boundary: Selected missing execution prerequisite.
    :type boundary: str
    """

    # tmp_path — отдельный временный каталог теста.
    # boundary — проверяемая утрата необходимого условия выполнения.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение изолированного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Remove one prerequisite without allowing another command start."""

        rig = Rig(tmp_path)

        if boundary == "source_scope":
            source = rig.hub.config.sources[0]
            rig.hub.config = replace(rig.hub.config, sources=(replace(source, access=(
                replace(source.access[0], scopes=frozenset({"read"})),)),))

        if boundary == "no_time":
            rig.hub._trusted = TrustedClock(clock=lambda: rig.now)
        await rig.start()

        try:
            request = rig.request()
            if boundary == "old_date":
                await rig.hub.submit(SOURCE_TOKEN, request, position=0, expected_cursor=-1, message_date=800)
            else:
                await rig.submit(request)
            if boundary in ("source_scope", "no_time", "old_date"):
                assert rig.store.pending() == ()
            elif boundary == "rtt":
                original = rig.transport.exchange


                #--------------------------------------------------------------------------------------------------
                # ФУНКЦИЯ : Задержка ответа после сохранения разрешения hub
                #--------------------------------------------------------------------------------------------------
                async def delayed(
                    operation: str,
                    message: CommandMessage,
                ) -> CommandMessage | CommandOffer | None:

                    """Spend the entire execution budget after the committed grant leaves the hub.

                    :param operation: Single operation executed within the documented limits.
                    :type operation: str

                    :param message: Typed request for the selected command operation.
                    :type message: CommandMessage

                    :return: Validated response, or None when polling found no work.
                    :rtype: CommandMessage | CommandOffer | None
                    """

                    # operation — одна операция в пределах доступной ёмкости.
                    # message — типизированное сообщение выбранной командной операции.

                    response = await original(operation, message)

                    if operation == "claim":
                        rig.now += 11
                    return response
                #--------------------------------------------------------------------------------------------------


                rig.transport.exchange = delayed
                assert await rig.client.acquire() is None
            elif boundary == "rollback":
                rig.now -= 1
                with pytest.raises(CommandError, match="unavailable"):
                    await rig.client.acquire()
                rig.now += 2
                with pytest.raises(CommandError, match="unavailable"):
                    await rig.client.acquire()
            else:
                ticket = await rig.client.acquire()
                assert ticket is not None
                await rig.client.close()
                replacement = SQLiteCommandStore(tmp_path / "client.sqlite", owner_id="app", generation="d" * 32,
                                                  role=StoreRole.CLIENT, clock=lambda: rig.now)
                replacement.open()
                try:
                    stored = replacement.get(request.ref)
                    assert stored.record.result.outcome.value == "unknown" and stored.deadline is None
                finally:
                    replacement.close()
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Автоматическое продление регистрации клиентом
#------------------------------------------------------------------------------------------------------------------
def test_command_background_heartbeat(tmp_path: Path) -> None:

    """Observe the actual background heartbeat rather than invoking the hub method directly.

    :param tmp_path: Isolated directory for both journals.
    :type tmp_path: Path
    """

    # tmp_path — отдельные настоящие журналы hub и клиента.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Ожидание одного heartbeat с конечным сроком теста
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Allow one real timer wakeup and verify that shutdown stops its task."""

        rig = Rig(tmp_path, session_ttl=10)
        received = asyncio.Event()
        original = rig.transport.exchange

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Наблюдение ответа на автоматический heartbeat
        #----------------------------------------------------------------------------------------------------------
        async def observed(
            operation: str,
            message: CommandMessage,
        ) -> CommandMessage | CommandOffer | None:

            """Record heartbeat only after the hub has accepted its request.

            :param operation: Selected command transport operation.
            :type operation: str

            :param message: Typed request sent by the client.
            :type message: CommandMessage

            :return: Unmodified response from the real in-process hub.
            :rtype: CommandMessage | CommandOffer | None
            """

            # operation — имя операции, выбранное самим клиентом.
            # message — запрос клиента, не заменяемый тестом.

            response = await original(operation, message)
            if operation == "heartbeat":
                received.set()
            return response
        #----------------------------------------------------------------------------------------------------------

        rig.transport.exchange = observed
        await rig.start()
        rig.now += 2
        try:
            await asyncio.wait_for(received.wait(), 5)
            assert rig.client._lease.expires_at == rig.now + 10
        finally:
            await rig.close()
        assert rig.client._heartbeat.done()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_hub не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
