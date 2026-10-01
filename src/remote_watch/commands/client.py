# Клиент команд с регистрацией, heartbeat и журналом разрешений на выполнение.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-165638
#
# Классы:
# -> CommandTicket: Сохранённое разрешение перед передачей команды исполнителю.
#
# -> CommandClient: Регистрация приложения и получение разрешений на выполнение.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> session(): Чтение выданной регистрации приложения.
#    -> start(): Запуск объекта и подготовка состояния.
#    -> acquire(): Подготовка команды и запись разрешения до callback.
#    -> begin(): Однократная проверка права перед непосредственным вызовом callback.
#    -> complete(): Сохранение результата callback и попытка его доставки.
#    -> flush_results(): Повтор доставки сохранённых результатов без повторного исполнения.
#    -> remaining(): Остаток срока для последней проверки в рабочем потоке.
#    -> pending(): Чтение сохранённых записей для явного разбора неизвестных исходов.
#    -> lookup(): Чтение одной записи, включая уже подтверждённый результат.
#    -> reconcile(): Очистка заведомо неисполненных записей с сохранением UNKNOWN.
#    -> release(): Подтверждение фактического окончания ранее неизвестного исполнения.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.
#    Служебные методы:
#    -> _check(): Проверка жизненного цикла и владельца event loop.
#    -> _now(): Проверка монотонного времени с запретом работы после отката.
#    -> _live(): Проверка оставшегося срока регистрации клиента.
#    -> _install_session(): Проверка корреляции и сокращение срока на полный RTT.
#    -> _send_result(): Подтверждение локального результата только после квитанции hub.
#    -> _reject(): Фиксация отказа до получения разрешения на исполнение.
#    -> _release_remote(): Доставка отдельного подтверждения фактической остановки.
#    -> _reconcile_ticket(): Восстановление занятости в памяти по подтверждённой записи журнала.
#    -> _keep_alive(): Последовательный heartbeat без накопления повторных запросов.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from secrets import token_hex
from time import monotonic

from remote_watch._validation import require_number, require_text
from remote_watch.commands._worker import StoreWorker
from remote_watch.commands.protocol import (
    CommandClaim,
    CommandGrant,
    CommandOutcome,
    CommandReason,
    CommandReceipt,
    CommandRef,
    CommandRegistration,
    CommandRequest,
    CommandResult,
    CommandSession,
    message_digest,
)
from remote_watch.commands.state import CommandAction, CommandDeadline
from remote_watch.commands.storage import CommandStore, StoredCommand
from remote_watch.commands.transport import CommandError, CommandOffer, CommandTransport


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Сохранённое разрешение перед передачей команды исполнителю
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandTicket:
    """Hold a durably prepared command without invoking application callbacks."""

    grant: CommandGrant         # Разрешение hub с точной командой и claim_id.
    deadline: CommandDeadline   # Срок выполнения на локальных часах клиента.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Регистрация приложения и получение разрешений на выполнение
#------------------------------------------------------------------------------------------------------------------
class CommandClient:
    """Maintain a lease and durable handshake for a single cooperative application executor."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        registration: CommandRegistration,
        transport: CommandTransport,
        store: CommandStore,
        *,
        stream_id: str = 'command_hub',
        storage_timeout: float = 3.0,
        shutdown_timeout: float = 5.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:

        """Configure a command client without opening network or journal resources.

        :param registration: Explicit identity, process nonce and allowed capabilities.
        :type registration: CommandRegistration

        :param transport: Owned implementation of bounded command exchanges.
        :type transport: CommandTransport

        :param store: Owned bounded persistent journal with a matching generation.
        :type store: CommandStore

        :param stream_id: Stable identifier of this hub journal sequence.
        :type stream_id: str

        :param storage_timeout: Maximum wait for one synchronous journal operation, seconds.
        :type storage_timeout: float

        :param shutdown_timeout: Maximum asynchronous journal shutdown wait, seconds.
        :type shutdown_timeout: float

        :param clock: Shared local monotonic clock, also used by the journal.
        :type clock: Callable[[], float]
        """

        # registration — явная Identity, новый ID процесса и объявленные команды.
        # transport — принадлежащий клиенту транспорт ограниченного обмена командами.
        # store — принадлежащий объекту журнал с согласованным поколением.
        # stream_id — постоянное имя последовательности этого журнала hub.
        # storage_timeout — конечное ожидание одной синхронной операции журнала, секунды.
        # shutdown_timeout — конечное ожидание закрытия журнала, секунды.
        # clock — монотонные часы, общие с соответствующим журналом.

        if type(registration) is not CommandRegistration or not callable(clock):
            raise TypeError("invalid command client")
        require_text(stream_id, "command stream", 128)

        for value in (storage_timeout, shutdown_timeout):
            require_number(value, "client timeout")
            if value > 30:
                raise ValueError("invalid client timeout")
        self.registration = registration
        self._transport = transport
        self._worker = StoreWorker(store, storage_timeout)
        self._stream = stream_id
        self._clock = clock
        self._shutdown_timeout = shutdown_timeout
        self._session: CommandSession | None = None
        self._lease: CommandDeadline | None = None
        self._heartbeat: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._acquiring = False
        self._active: CommandTicket | None = None
        self._begun = False
        self._last = 0.0
        self._failed_clock = False
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение выданной регистрации приложения
    #--------------------------------------------------------------------------------------------------------------
    @property
    def session(self) -> CommandSession:

        """Return the authenticated session after startup.

        :return: Exact registration with a conservative remaining lifetime.
        :rtype: CommandSession
        """

        self._check()

        if self._session is None:
            raise CommandError("closed")
        return self._session
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск объекта и подготовка состояния
    #--------------------------------------------------------------------------------------------------------------
    async def start(self) -> None:

        """Register the exact application identity and start one heartbeat task."""

        if self._loop is not None or self._closed:
            raise CommandError("closed")
        self._loop = asyncio.get_running_loop()

        try:
            await self._worker.open()
            await self._transport.open()
            sent = self._now()
            response = await self._transport.exchange("register", self.registration)
            self._install_session(response, sent)
            self._heartbeat = asyncio.create_task(self._keep_alive())
        except BaseException:
            await self.close()
            raise
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка команды и запись разрешения до callback
    #--------------------------------------------------------------------------------------------------------------
    async def acquire(
        self,
        validate: Callable[[CommandRequest, float], Awaitable[CommandReason | None]] | None = None,
    ) -> CommandTicket | None:

        """Persist the start permission before returning one command to the executor.

        :param validate: Optional argument check before requesting the execution grant.
        :type validate: Callable[[CommandRequest, float], Awaitable[CommandReason | None]] | None

        :return: Durably prepared execution ticket, or None when no start is allowed.
        :rtype: CommandTicket | None
        """

        # validate — локальная проверка аргументов до выдачи разрешения hub.

        self._live()

        if self._acquiring or self._active is not None:
            raise CommandError("busy")
        self._acquiring = True

        try:
            # Сначала передаём уже сохранённые итоги. Сбой после callback не должен
            # превращаться в новый вызов того же обработчика.
            await self.flush_results()
            sent = self._now()
            offer = await self._transport.exchange("poll", self.session)
            if offer is None:
                return None
            if type(offer) is not CommandOffer or not offer.request.ref.matches(self.session):
                raise CommandError("invalid")
            capability = next(
                (cap for cap in self.registration.capabilities if cap.name == offer.request.name), None)
            if capability is None or (offer.request.arguments and not capability.accepts_arguments):
                raise CommandError("denied")
            # Вычитаем весь обмен, в том числе ожидание на сервере. После доставки
            # сообщения исходный срок не начинается заново на часах приложения.
            deadline = CommandDeadline.from_response(
                offer.remaining_ttl, sent, self._now(), self.session.hub_epoch)
            lease = self._live()


            #------------------------------------------------------------------------------------------------------
            # ФУНКЦИЯ : Запись команды и выбор единственного claim_id
            #------------------------------------------------------------------------------------------------------
            def prepare() -> CommandClaim | None:

                """Deduplicate the offer and durably select one claim identifier.

                :return: Durably selected execution claim, or None for terminal work.
                :rtype: CommandClaim | None
                """

                store = self._worker.store
                admitted = store.admit(
                    self._stream, offer.sequence, store.cursor(self._stream), offer.request, deadline)
                stored = admitted.command

                if stored is None or stored.record.result is not None:
                    return None
                claim = CommandClaim(ref=offer.request.ref, claim_id=stored.record.claim_id or token_hex(16),
                                     request_digest=message_digest(offer.request))
                changed = store.transition(
                    claim.ref, stored.revision, CommandAction.CLAIM, self._session, claim, lease)
                return claim if changed.command.record.result is None else None
            #------------------------------------------------------------------------------------------------------


            claim = await self._worker.call(prepare)
            if claim is None:
                return None
            if validate is not None:
                remaining = min(deadline.remaining(self._now(), self.session.hub_epoch),
                                lease.remaining(self._now(), self.session.hub_epoch))
                reason = await validate(offer.request, remaining)
                if reason is not None:
                    await self._reject(claim, reason)
                    return None
            sent = self._now()
            grant = await self._transport.exchange("claim", claim)
            if type(grant) is CommandResult:
                # Окончательный отказ hub не даёт разрешения выполнять callback.
                if grant.ref != claim.ref:
                    raise CommandError("invalid")
                return None
            if (type(grant) is not CommandGrant or not grant.matches(claim)
                    or grant.execution_timeout > capability.timeout):
                raise CommandError("invalid")
            # Разрешение связано с digest запроса. Чужой или запоздалый grant
            # не может расширить ни исходный срок, ни timeout объявленной команды.
            bound = CommandDeadline.from_response(
                grant.execution_timeout, sent, self._now(), self.session.hub_epoch)
            lease = self._live()


            #------------------------------------------------------------------------------------------------------
            # ФУНКЦИЯ : Фиксация STARTED перед передачей разрешения исполнителю
            #------------------------------------------------------------------------------------------------------
            def start() -> CommandTicket | None:

                """Commit STARTED before handing execution ownership to the caller.

                :return: Durably prepared execution ticket, or None when no start is allowed.
                :rtype: CommandTicket | None
                """

                store = self._worker.store
                stored = store.get(claim.ref)

                if stored is None:
                    raise CommandError("invalid")
                changed = store.transition(claim.ref, stored.revision, CommandAction.START, self._session,
                                           claim, lease, grant=grant, grant_deadline=bound)

                if not changed.start_callback:
                    return None
                return CommandTicket(grant=grant, deadline=changed.command.deadline)
            #------------------------------------------------------------------------------------------------------


            ticket = await self._worker.call(start)
            self._active = ticket
            self._begun = False
            return ticket
        finally:
            self._acquiring = False
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Однократная проверка права перед непосредственным вызовом callback
    #--------------------------------------------------------------------------------------------------------------
    def begin(
        self,
        ticket: CommandTicket,
    ) -> bool:

        """Consume the execution permission once, immediately before invoking a callback.

        :param ticket: Exact ticket returned by this client, never a reconstructed copy.
        :type ticket: CommandTicket

        :return: True only when the requested credential or execution prerequisite is valid.
        :rtype: bool
        """

        # ticket — тот же объект разрешения, который вернул этот клиент.

        self._check()

        if ticket is not self._active or self._begun:
            raise CommandError("conflict")
        self._begun = True
        return self.remaining(ticket) > 0
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Сохранение результата callback и попытка его доставки
    #--------------------------------------------------------------------------------------------------------------
    async def complete(
        self,
        ticket: CommandTicket,
        result: CommandResult,
        execution_finished: bool = True,
    ) -> None:

        """Commit a completed callback result before attempting its delivery to the hub.

        :param ticket: Exact ticket returned by this client, never a reconstructed copy.
        :type ticket: CommandTicket

        :param result: Correlated terminal result whose exact contents must be retained.
        :type result: CommandResult

        :param execution_finished: True only after the callback has actually stopped or was never invoked.
        :type execution_finished: bool
        """

        # ticket — тот же объект разрешения, который вернул этот клиент.
        # result — точный итог выполнения с идентификаторами исходной команды.
        # execution_finished — фактическое окончание, а не истечение timeout.

        self._check()

        if ticket is not self._active or not self._begun:
            raise CommandError("conflict")
        grant = ticket.grant

        if (type(result) is not CommandResult or result.ref != grant.request.ref
                or result.claim_id != grant.claim_id):
            raise CommandError("invalid")


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Сохранение точного результата выданного исполнения
        #----------------------------------------------------------------------------------------------------------
        def finish() -> None:

            """Retain the exact result even when the following HTTP exchange fails."""

            store = self._worker.store
            stored = store.get(result.ref)

            if stored is None:
                raise CommandError("invalid")
            claim = CommandClaim(ref=result.ref, claim_id=grant.claim_id,
                                 request_digest=message_digest(grant.request))
            store.transition(result.ref, stored.revision, CommandAction.FINISH, self._session,
                             claim, self._lease, result=result)
        #----------------------------------------------------------------------------------------------------------


        await self._worker.call(finish)
        # С этого момента повторять можно только доставку результата. Локальная
        # фиксация выполнена даже при потере следующего HTTP-ответа.
        if execution_finished:
            await self._worker.call(lambda: self._worker.store.release_execution(result.ref, result.claim_id))
            self._active = None
        await self._send_result(result)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Повтор доставки сохранённых результатов без повторного исполнения
    #--------------------------------------------------------------------------------------------------------------
    async def flush_results(self) -> None:

        """Retry persisted results without replaying callbacks or old process sessions."""

        self._check()
        await self.reconcile()
        rows = await self._worker.call(lambda: self._worker.store.pending(limit=1000))

        for stored in rows:
            result = stored.record.result
            if result is not None and not stored.acknowledged and result.ref.matches(self.session):
                # Истёкшие до START команды сообщает сам hub; клиент не подменяет его итог.
                if stored.record.phase.value not in ("completed", "unknown", "rejected"):
                    await self._worker.call(lambda: self._worker.store.acknowledge(result))
                    continue
                await self._send_result(result)
        await self._worker.call(self._worker.store.prune)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остаток срока для последней проверки в рабочем потоке
    #--------------------------------------------------------------------------------------------------------------
    def remaining(
        self,
        ticket: CommandTicket,
    ) -> float:

        """Read the conservative remaining execution budget, including from the callback worker.

        :param ticket: Exact ticket returned by this client, never a reconstructed copy.
        :type ticket: CommandTicket

        :return: Remaining execution lifetime in seconds, or zero when invocation is forbidden.
        :rtype: float
        """

        # ticket — тот же объект разрешения, который вернул этот клиент.

        if self._closed or self._session is None or self._lease is None or self._failed_clock:
            return 0.0
        now = self._clock()
        require_number(now, "execution clock", allow_zero=True)
        if now < self._last:
            return 0.0
        return min(ticket.deadline.remaining(now, self._session.hub_epoch),
                   self._lease.remaining(now, self._session.hub_epoch))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение сохранённых записей для явного разбора неизвестных исходов
    #--------------------------------------------------------------------------------------------------------------
    async def pending(self) -> tuple[StoredCommand, ...]:

        """Expose retained records for explicit local reconciliation, without replaying them.

        :return: Up to 1000 unacknowledged or still active local journal records.
        :rtype: tuple[StoredCommand, ...]
        """

        self._check()
        return await self._worker.call(lambda: self._worker.store.pending(limit=1000))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение одной записи, включая уже подтверждённый результат
    #--------------------------------------------------------------------------------------------------------------
    async def lookup(
        self,
        ref: CommandRef,
    ) -> StoredCommand | None:

        """Read one retained record, including an already acknowledged callback result.

        :param ref: Exact identity, process session, hub epoch and command identifier.
        :type ref: CommandRef

        :return: Retained journal record, or None when absent.
        :rtype: StoredCommand | None
        """

        # ref — точные Identity, сессия процесса, запуск hub и ID команды.

        self._check()
        return await self._worker.call(lambda: self._worker.store.get(ref))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Очистка заведомо неисполненных записей с сохранением UNKNOWN
    #--------------------------------------------------------------------------------------------------------------
    async def reconcile(self) -> None:

        """Retire provably unexecuted expired records while retaining unknown executions for review."""

        self._check()
        await self._reconcile_ticket()
        session = self.session
        lease = self._lease


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подтверждение истёкших записей без возможного callback
        #----------------------------------------------------------------------------------------------------------
        def retire() -> None:

            """Acknowledge only records which cannot represent an executed callback."""

            store = self._worker.store
            for row in store.pending(limit=1000):
                record = row.record
                ref = record.request.ref
                if (record.result is None and record.phase.value in ("ready", "claimed")
                        and row.deadline is not None and ref.matches(session)):
                    now = self._clock()
                    if min(row.deadline.remaining(now, ref.hub_epoch), lease.remaining(now, ref.hub_epoch)) <= 0:
                        claim = CommandClaim(ref=ref, claim_id=record.claim_id or token_hex(16),
                                             request_digest=message_digest(record.request))
                        changed = store.transition(ref, row.revision, CommandAction.CLAIM, session, claim, lease)
                        record = changed.command.record
                if record.result is not None and record.result.outcome is CommandOutcome.EXPIRED:
                    store.acknowledge(record.result)
            store.prune()
        #----------------------------------------------------------------------------------------------------------


        await self._worker.call(retire)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение фактического окончания ранее неизвестного исполнения
    #--------------------------------------------------------------------------------------------------------------
    async def release(
        self,
        ticket: CommandTicket,
    ) -> None:

        """Release a timed-out execution only after the executor observed its actual termination.

        :param ticket: Exact ticket returned by this client, never a reconstructed copy.
        :type ticket: CommandTicket
        """

        # ticket — тот же объект разрешения, который вернул этот клиент.

        self._check()
        if ticket is not self._active:
            raise CommandError("conflict")
        stored = await self._worker.call(lambda: self._worker.store.get(ticket.grant.request.ref))
        if stored is None or stored.record.result is None:
            raise CommandError("conflict")
        result = stored.record.result
        await self._worker.call(lambda: self._worker.store.release_execution(result.ref, result.claim_id))
        self._active = None
        await self._send_result(result)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Stop heartbeat and close owned resources without claiming callback termination."""

        if self._closed:
            return
        self._closed = True

        if self._heartbeat is not None:
            self._heartbeat.cancel()

        try:
            await self._transport.close()
        finally:
            await self._worker.close(self._shutdown_timeout)

        if self._heartbeat is not None:
            await asyncio.wait({self._heartbeat}, timeout=0)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка жизненного цикла и владельца event loop
    #--------------------------------------------------------------------------------------------------------------
    def _check(self) -> None:

        """Reject use outside the owning event loop or after shutdown."""

        if self._closed or self._loop is None or asyncio.get_running_loop() is not self._loop:
            raise CommandError("closed")
        self._now()
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка монотонного времени с запретом работы после отката
    #--------------------------------------------------------------------------------------------------------------
    def _now(self) -> float:

        """Invalidate this client permanently if its monotonic clock rolls back.

        :return: Current validated monotonic time in seconds.
        :rtype: float
        """

        now = self._clock()
        require_number(now, "client monotonic time", allow_zero=True)

        if now < self._last or self._failed_clock:
            self._failed_clock = True
            raise CommandError("unavailable")
        self._last = now
        return now
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка оставшегося срока регистрации клиента
    #--------------------------------------------------------------------------------------------------------------
    def _live(self) -> CommandDeadline:

        """Require an unexpired local lease before acquiring or starting new work.

        :return: Conservative local deadline in the shared monotonic domain.
        :rtype: CommandDeadline
        """

        self._check()

        if self._lease is None or self._lease.remaining(self._now(), self.session.hub_epoch) <= 0:
            raise CommandError("stale_session")
        return self._lease
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка корреляции и сокращение срока на полный RTT
    #--------------------------------------------------------------------------------------------------------------
    def _install_session(
        self,
        response: CommandSession,
        sent: float,
    ) -> None:

        """Verify identity and epoch before replacing the conservative local lease.

        :param response: Authenticated registration returned by the hub.
        :type response: CommandSession

        :param sent: Local monotonic time immediately before the request.
        :type sent: float
        """

        # response — проверяемая регистрация, полученная от hub.
        # sent — локальное монотонное время непосредственно перед запросом.

        if (type(response) is not CommandSession or response.identity != self.registration.identity
                or response.session_id != self.registration.session_id):
            raise CommandError("invalid")

        if self._session is not None and response.hub_epoch != self._session.hub_epoch:
            raise CommandError("stale_session")
        lease = CommandDeadline.from_response(response.remaining_ttl, sent, self._now(), response.hub_epoch)

        if lease.remaining(self._now(), response.hub_epoch) <= 0:
            raise CommandError("stale_session")
        self._session = response
        self._lease = lease
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Подтверждение локального результата только после квитанции hub
    #--------------------------------------------------------------------------------------------------------------
    async def _send_result(
        self,
        result: CommandResult,
    ) -> None:

        """Acknowledge local storage only after an exact durable hub receipt.

        :param result: Correlated terminal result whose exact contents must be retained.
        :type result: CommandResult
        """

        # result — точный итог выполнения с идентификаторами исходной команды.

        response = await self._transport.exchange("result", result)

        if type(response) is not CommandReceipt or not response.matches(result):
            raise CommandError("invalid")
        stored = await self._worker.call(lambda: self._worker.store.get(result.ref))
        if result.outcome is CommandOutcome.UNKNOWN and not stored.execution_active:
            await self._release_remote(result)
        await self._worker.call(lambda: self._worker.store.acknowledge(result))
        await self._reconcile_ticket()
        await self._worker.call(self._worker.store.prune)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Фиксация отказа до получения разрешения на исполнение
    #--------------------------------------------------------------------------------------------------------------
    async def _reject(
        self,
        claim: CommandClaim,
        reason: CommandReason,
    ) -> None:

        """Persist a failed pre-execution validation before reporting it to the hub.

        :param claim: Correlated request for a single execution permission.
        :type claim: CommandClaim

        :param reason: Fixed public failure reason, without exception details.
        :type reason: CommandReason
        """

        # claim — запрос разрешения на единственное исполнение команды.
        # reason — фиксированная причина без текста исключения.

        if reason not in (CommandReason.DENIED, CommandReason.INVALID_ARGUMENTS):
            raise CommandError("invalid")
        result = CommandResult(ref=claim.ref, claim_id=claim.claim_id,
                               outcome=CommandOutcome.REJECTED, reason=reason)


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Запись отказа без разрешения запускать callback
        #----------------------------------------------------------------------------------------------------------
        def reject() -> CommandResult:

            """Record rejection without granting callback permission.

            :return: Correlated bounded outcome without callback exception details.
            :rtype: CommandResult
            """

            store = self._worker.store
            stored = store.get(claim.ref)
            if stored is None:
                raise CommandError("invalid")
            changed = store.transition(claim.ref, stored.revision, CommandAction.REJECT,
                                       self._session, claim, self._lease, result=result)
            return changed.command.record.result
        #----------------------------------------------------------------------------------------------------------


        saved = await self._worker.call(reject)
        if saved.outcome is CommandOutcome.REJECTED:
            await self._send_result(saved)
        else:
            # До grant callback не мог начаться. Истечение на hub завершится независимо;
            # локальный ACK здесь означает лишь освобождение заведомо неисполненной записи.
            await self._worker.call(lambda: self._worker.store.acknowledge(saved))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Доставка отдельного подтверждения фактической остановки
    #--------------------------------------------------------------------------------------------------------------
    async def _release_remote(
        self,
        result: CommandResult,
    ) -> None:

        """Confirm actual termination separately from the earlier UNKNOWN result.

        :param result: Correlated terminal result whose exact contents must be retained.
        :type result: CommandResult
        """

        # result — точный итог выполнения с идентификаторами исходной команды.

        stored = await self._worker.call(lambda: self._worker.store.get(result.ref))
        if stored is None:
            raise CommandError("invalid")
        claim = CommandClaim(ref=result.ref, claim_id=result.claim_id,
                             request_digest=message_digest(stored.record.request))
        response = await self._transport.exchange("release", claim)
        if type(response) is not CommandReceipt or not response.matches(result):
            raise CommandError("outcome_conflict")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Восстановление занятости в памяти по подтверждённой записи журнала
    #--------------------------------------------------------------------------------------------------------------
    async def _reconcile_ticket(self) -> None:

        """Release stale in-memory ownership only after durable completion and acknowledgement."""

        if self._active is None:
            return
        ticket = self._active
        stored = await self._worker.call(lambda: self._worker.store.get(ticket.grant.request.ref))

        # Запись результата/ACK могла завершиться, а ожидание её ответа — оборваться.
        # Сверяем память до prune: UNKNOWN живого callback не удовлетворяет проверке.
        if (stored is not None and stored.record.result is not None
                and stored.acknowledged and not stored.execution_active):
            self._active = None
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Последовательный heartbeat без накопления повторных запросов
    #--------------------------------------------------------------------------------------------------------------
    async def _keep_alive(self) -> None:

        """Send sequential heartbeats without an accumulating retry queue."""

        while not self._closed:
            await asyncio.sleep(max(0.01, self._session.remaining_ttl / 3))
            try:
                self._live()
                sent = self._now()
                response = await self._transport.exchange("heartbeat", self.session)
                self._install_session(response, sent)
            except CommandError:
                # Сбой сети не продлевает локальную регистрацию. После её истечения
                # клиент остаётся закрыт для новых действий до нового запуска.
                if self._lease.remaining(self._clock(), self._session.hub_epoch) <= 0:
                    return
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль client не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
