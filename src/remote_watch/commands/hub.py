# Центральная маршрутизация команд, регистраций и сохранённых результатов.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-185745
#
# Классы:
# -> _Session: Регистрация процесса, сохраняемая и после истечения срока.
#
# -> CommandHub: Маршрутизация команд между доверенным источником и приложениями.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> start(): Запуск объекта и подготовка состояния.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.
#    -> authenticate(): Проверка отдельных учётных данных приложения.
#    -> register(): Регистрация точной цели без замены живой сессии.
#    -> heartbeat(): Продление живой регистрации без продления команд.
#    -> release(): Подтверждение фактического окончания ранее неизвестного исполнения.
#    -> source_cursor(): Чтение подтверждённой позиции доверенного источника.
#    -> skip_source(): Фиксация пропуска события до подтверждения провайдеру.
#    -> source_cutoff(): Консервативная граница очистки по доверенному времени.
#    -> commands_for(): Разрешённые команды живого приложения для автора и чата.
#    -> submit(): Проверка прав и свежести перед атомарной записью с cursor.
#    -> poll(): Ожидание одной команды с одним waiter на сессию.
#    -> claim(): Запись CLAIMED и STARTED до выдачи разрешения приложению.
#    -> finish(): Сохранение точного результата выданного исполнения.
#    -> results(): Чтение результатов только для своего источника.
#    -> sweep(): Завершение просроченных записей без предположения об остановке callback.
#    -> acknowledge(): Подтверждение ответа провайдера отдельно от квитанции приложению.
#    -> target(): Поиск точной живой цели для доверенного источника.
#    Служебные методы:
#    -> _check(): Проверка жизненного цикла и владельца event loop.
#    -> _now(): Проверка монотонного времени с запретом работы после отката.
#    -> _deadline(): Создание локального срока регистрации.
#    -> _session(): Остаток регистрации без её продления.
#    -> _entry(): Проверка владельца, Identity, сессии и запуска hub.
#    -> _ref_entry(): Проверка принадлежности команды приложению.
#    -> _source_policy(): Авторизация доверенного адаптера входящих сообщений.
#    -> _offer(): Выбор первой непросроченной команды этой сессии.
#    -> _update_time(): Обновление достоверного времени с конечным ожиданием.
#    -> _refresh_time(): Периодическое обслуживание времени и просроченных команд.
#
# Функции:
# -> _matches(): Сравнение хешей секретов без выдачи исходных значений.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from secrets import token_hex
from time import monotonic

from remote_watch._validation import require_int, require_number
from remote_watch.commands._worker import StoreWorker
from remote_watch.commands.hub_config import CommandHubConfig, CommandPrincipal, CommandSource
from remote_watch.commands.protocol import (
    CommandClaim,
    CommandGrant,
    CommandOutcome,
    CommandReceipt,
    CommandRef,
    CommandRegistration,
    CommandRequest,
    CommandResult,
    CommandSession,
    message_digest,
)
from remote_watch.commands.state import CommandAction, CommandDeadline, CommandPhase
from remote_watch.commands.storage import Admission, AuditCode, CommandStore, StoredCommand
from remote_watch.commands.time import TimeSource, TrustedClock
from remote_watch.commands.transport import CommandError, CommandOffer
from remote_watch.events import Identity


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Регистрация процесса, сохраняемая и после истечения срока
#------------------------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class _Session:
    """Keep one issued session, including expired sessions used to reject replay."""

    registration: CommandRegistration   # Неизменяемые сведения о запуске приложения.
    principal: str                      # Владелец командных учётных данных.
    deadline: CommandDeadline           # Текущий срок регистрации на часах hub.
    changed: asyncio.Event              # Единственное пробуждение long poll этой сессии.
    polling: bool = False               # Признак уже выполняющегося long poll.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Маршрутизация команд между доверенным источником и приложениями
#------------------------------------------------------------------------------------------------------------------
class CommandHub:
    """Route authenticated command intents with bounded sessions and durable start decisions."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: CommandHubConfig,
        store: CommandStore,
        *,
        epoch: str,
        trusted_clock: TrustedClock,
        time_source: TimeSource | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:

        """Bind an explicit journal generation and clock without opening resources.

        :param config: Explicit credentials, access rules and finite limits.
        :type config: CommandHubConfig

        :param store: Owned bounded persistent journal with a matching generation.
        :type store: CommandStore

        :param epoch: Fresh hub incarnation identifier matching the journal generation.
        :type epoch: str

        :param trusted_clock: UTC bounds anchored to the same local monotonic clock.
        :type trusted_clock: TrustedClock

        :param time_source: Explicit trusted UTC source; absent in deterministic clock tests.
        :type time_source: TimeSource | None

        :param clock: Shared local monotonic clock, also used by the journal.
        :type clock: Callable[[], float]
        """

        # config — явные учётные данные, права и конечные пределы.
        # store — принадлежащий объекту журнал с согласованным поколением.
        # epoch — новый ID запуска hub, совпадающий с поколением журнала.
        # trusted_clock — границы UTC, привязанные к тем же монотонным часам.
        # time_source — явный источник UTC; в тестах можно установить показание заранее.
        # clock — монотонные часы, общие с соответствующим журналом.

        if type(config) is not CommandHubConfig or type(trusted_clock) is not TrustedClock:
            raise TypeError("invalid command hub configuration")

        if type(epoch) is not str or len(epoch) != 32 or any(c not in "0123456789abcdef" for c in epoch):
            raise ValueError("invalid hub generation")

        if not callable(clock):
            raise TypeError("invalid monotonic clock")
        self.config = config
        self.epoch = epoch
        self._clock = clock
        self._trusted = trusted_clock
        self._source = time_source
        self._worker = StoreWorker(store, config.storage_timeout)
        self._issued: dict[str, _Session] = {}
        self._targets: dict[Identity, _Session] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._last = 0.0
        self._refresh: asyncio.Task | None = None
        self._failed_clock = False
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск объекта и подготовка состояния
    #--------------------------------------------------------------------------------------------------------------
    async def start(self) -> None:

        """Open the journal and start one explicitly configured time-refresh task."""

        if self._loop is not None or self._closed:
            raise CommandError("closed")
        self._loop = asyncio.get_running_loop()

        try:
            await self._worker.open()
            if self._source is not None:
                await self._update_time()
            self._refresh = asyncio.create_task(self._refresh_time())
        except BaseException:
            await self.close()
            raise
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Wake all polls and close storage within one shared shutdown budget."""

        if self._closed:
            return
        self._closed = True

        for entry in self._issued.values():
            entry.changed.set()

        if self._refresh is not None:
            self._refresh.cancel()
        await self._worker.close(self.config.shutdown_timeout)

        if self._refresh is not None:
            await asyncio.wait({self._refresh}, timeout=0)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка отдельных учётных данных приложения
    #--------------------------------------------------------------------------------------------------------------
    def authenticate(
        self,
        token: str,
    ) -> CommandPrincipal:

        """Authorize only application command credentials, never relay or source credentials.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :return: Configured application policy authenticated by the supplied secret.
        :rtype: CommandPrincipal
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.

        self._check()

        for principal in self.config.principals:
            if _matches(token, principal.token):
                return principal
        raise CommandError("denied")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Регистрация точной цели без замены живой сессии
    #--------------------------------------------------------------------------------------------------------------
    async def register(
        self,
        token: str,
        registration: CommandRegistration,
    ) -> CommandSession:

        """Issue a lease without replacing a live identity or reviving an expired session.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param registration: Explicit identity, process nonce and allowed capabilities.
        :type registration: CommandRegistration

        :return: Exact registration with a conservative remaining lifetime.
        :rtype: CommandSession
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # registration — явная Identity, новый ID процесса и объявленные команды.

        principal = self.authenticate(token)

        if type(registration) is not CommandRegistration or registration.identity not in principal.identities:
            raise CommandError("denied")

        if any(cap.required_scope not in principal.scopes for cap in registration.capabilities):
            raise CommandError("denied")

        # Повтор регистрации идемпотентен, но не является heartbeat. Храним также
        # истёкшие ID: прежний процесс не должен ожить под тем же session_id.
        now = self._now()
        known = self._issued.get(registration.session_id)

        if known is not None:
            if known.registration != registration or known.principal != principal.name:
                raise CommandError("conflict")
            return self._session(known)
        existing = self._targets.get(registration.identity)

        if existing is not None and existing.deadline.remaining(now, self.epoch) > 0:
            raise CommandError("conflict")

        if len(self._issued) >= self.config.max_sessions:
            raise CommandError("capacity")
        entry = _Session(registration, principal.name, self._deadline(self.config.session_ttl), asyncio.Event())
        self._issued[registration.session_id] = entry
        self._targets[registration.identity] = entry
        return self._session(entry)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Продление живой регистрации без продления команд
    #--------------------------------------------------------------------------------------------------------------
    async def heartbeat(
        self,
        token: str,
        session: CommandSession,
    ) -> CommandSession:

        """Extend only a currently live registration, without extending command deadlines.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param session: Previously issued exact application registration.
        :type session: CommandSession

        :return: Exact registration with a conservative remaining lifetime.
        :rtype: CommandSession
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # session — ранее выданная регистрация конкретного приложения.

        entry = self._entry(token, session)
        entry.deadline = self._deadline(self.config.session_ttl)
        return self._session(entry)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение фактического окончания ранее неизвестного исполнения
    #--------------------------------------------------------------------------------------------------------------
    async def release(
        self,
        token: str,
        claim: CommandClaim,
    ) -> CommandReceipt:

        """Accept termination proof only from the owning application and original claim.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param claim: Correlated request for a single execution permission.
        :type claim: CommandClaim

        :return: Receipt bound to the exact durably stored result digest.
        :rtype: CommandReceipt
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # claim — запрос разрешения на единственное исполнение команды.

        self._ref_entry(token, claim.ref, live=False)


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Проверка и запись решения в рабочем потоке журнала
        #----------------------------------------------------------------------------------------------------------
        def commit() -> CommandReceipt:

            """Clear execution ownership without changing its retained terminal outcome.

            :return: Receipt bound to the exact durably stored result digest.
            :rtype: CommandReceipt
            """

            store = self._worker.store
            stored = store.get(claim.ref)
            if (stored is None or stored.record.result is None or stored.record.claim_id != claim.claim_id
                    or message_digest(stored.record.request) != claim.request_digest):
                raise CommandError("conflict")
            store.release_execution(claim.ref, claim.claim_id)
            return CommandReceipt(ref=claim.ref, claim_id=claim.claim_id,
                                  result_digest=message_digest(stored.record.result))
        #----------------------------------------------------------------------------------------------------------


        return await self._worker.call(commit)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение подтверждённой позиции доверенного источника
    #--------------------------------------------------------------------------------------------------------------
    async def source_cursor(
        self,
        token: str,
    ) -> int:

        """Read the durable position for an authenticated provider reader.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :return: Committed source position; minus one for a new stream.
        :rtype: int
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.

        source = self._source_policy(token)
        return await self._worker.call(lambda: self._worker.store.cursor(source.source_id))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Фиксация пропуска события до подтверждения провайдеру
    #--------------------------------------------------------------------------------------------------------------
    async def skip_source(
        self,
        token: str,
        *,
        position: int,
        expected_cursor: int,
    ) -> Admission:

        """Persist a non-command provider event before advancing its external checkpoint.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param position: Monotonically increasing local source position.
        :type position: int

        :param expected_cursor: Previously committed source position.
        :type expected_cursor: int

        :return: Committed source cursor and optional admitted command.
        :rtype: Admission
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # position — собственная возрастающая позиция источника.
        # expected_cursor — предыдущая подтверждённая позиция источника.

        source = self._source_policy(token)
        return await self._worker.call(lambda: self._worker.store.admit(
            source.source_id, position, expected_cursor, None))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Консервативная граница очистки по доверенному времени
    #--------------------------------------------------------------------------------------------------------------
    def source_cutoff(
        self,
        token: str,
    ) -> int | None:

        """Return a conservative replay-pruning boundary only while trusted time is available.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :return: Conservative UTC pruning boundary, or None without trusted time.
        :rtype: int | None
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.

        self._source_policy(token)
        try:
            sample = self._trusted.bounds()
        except Exception:
            return None
        return max(0, int(sample.lower_utc - self._trusted.policy.max_age - 2))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Разрешённые команды живого приложения для автора и чата
    #--------------------------------------------------------------------------------------------------------------
    def commands_for(
        self,
        token: str,
        identity: Identity,
        actor_id: str,
        conversation_id: str,
    ) -> tuple[str, ...]:

        """List only the live target's commands permitted to this exact provider actor and chat.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param identity: Exact application identity, without wildcard fields.
        :type identity: Identity

        :param actor_id: Provider-authenticated sender or configured private-topic principal.
        :type actor_id: str

        :param conversation_id: Authenticated chat ID or configured private command topic.
        :type conversation_id: str

        :return: Command names currently permitted to the exact actor and conversation.
        :rtype: tuple[str, ...]
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # identity — точные сведения о приложении без шаблонов и догадок.
        # actor_id — удостоверенный отправитель либо владелец закрытого топика.
        # conversation_id — проверенный ID чата либо закрытый топик команд.

        source = self._source_policy(token)
        entry = self._targets.get(identity)
        if entry is None:
            raise CommandError("stale_session")
        self._session(entry)
        scopes = frozenset(scope for rule in source.access
                           if rule.identity == identity and rule.actor_id == actor_id
                           and rule.conversation_id == conversation_id for scope in rule.scopes)
        if not scopes:
            raise CommandError("denied")
        return tuple(cap.name for cap in entry.registration.capabilities if cap.required_scope in scopes)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка прав и свежести перед атомарной записью с cursor
    #--------------------------------------------------------------------------------------------------------------
    async def submit(
        self,
        token: str,
        request: CommandRequest,
        *,
        position: int,
        expected_cursor: int,
        message_date: int,
    ) -> Admission:

        """Commit an authenticated source decision before the source acknowledges its event.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param request: Incoming request validated before dispatch.
        :type request: CommandRequest

        :param position: Monotonically increasing provider stream position.
        :type position: int

        :param expected_cursor: Previously committed source position.
        :type expected_cursor: int

        :param message_date: Integer provider timestamp, never the VM wall clock.
        :type message_date: int

        :return: Committed source cursor and optional admitted command.
        :rtype: Admission
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # request — входящий запрос, проверяемый перед обработкой.
        # position — возрастающая позиция события в потоке провайдера.
        # expected_cursor — предыдущая подтверждённая позиция источника.
        # message_date — целая отметка провайдера, а не системные часы VM.

        source = self._source_policy(token)

        if type(request) is not CommandRequest or request.source_id != source.source_id:
            raise CommandError("denied")
        require_int(position, "source position", minimum=0)
        require_int(message_date, "provider date", minimum=0)
        entry = self._targets.get(request.ref.identity)
        capability = None

        if entry is not None and entry.deadline.remaining(self._now(), self.epoch) > 0:
            if request.ref.matches(self._session(entry)):
                capability = next(
                    (cap for cap in entry.registration.capabilities if cap.name == request.name), None)
        permitted = capability is not None and any(
            rule.actor_id == request.actor_id and rule.conversation_id == request.conversation_id
            and rule.identity == request.ref.identity and capability.required_scope in rule.scopes
            for rule in source.access
        )

        if capability is not None and request.arguments and not capability.accepts_arguments:
            permitted = False

        # actor/chat берутся у доверенного источника. Секрет приложения не даёт
        # права подавать эти сведения через публичный HTTP endpoint.
        decision = self._trusted.check(message_date, self.epoch) if permitted else None
        deadline = None if decision is None else decision.deadline
        accepted = request if permitted and deadline is not None else None
        reason = AuditCode.DENIED if not permitted else AuditCode.STALE

        # В единственном рабочем потоке журнала проверяем общую ёмкость и фиксируем cursor с решением.
        # Повтор позиции не порождает новый ID даже после очистки данных команды.

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Проверка и запись решения в единственном рабочем потоке журнала
        #----------------------------------------------------------------------------------------------------------
        def commit() -> Admission:

            """Apply one source decision in the serialized storage lane.

            :return: Committed source cursor and optional admitted command.
            :rtype: Admission
            """

            store = self._worker.store

            if accepted is not None and position > store.cursor(source.source_id):
                if len(store.pending(limit=1000)) >= self.config.max_pending:
                    raise CommandError("capacity")
            return store.admit(source.source_id, position, expected_cursor, accepted, deadline, reason)
        #----------------------------------------------------------------------------------------------------------


        admitted = await self._worker.call(commit)

        if entry is not None:
            entry.changed.set()
        return admitted
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ожидание одной команды с одним waiter на сессию
    #--------------------------------------------------------------------------------------------------------------
    async def poll(
        self,
        token: str,
        session: CommandSession,
    ) -> CommandOffer | None:

        """Wait for one command using at most one poll waiter per live session.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param session: Previously issued exact application registration.
        :type session: CommandSession

        :return: Earliest eligible command and budget, or None if no work is available.
        :rtype: CommandOffer | None
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # session — ранее выданная регистрация конкретного приложения.

        entry = self._entry(token, session)

        if entry.polling:
            raise CommandError("busy")
        entry.polling = True

        try:
            entry.changed.clear()
            offer = await self._offer(entry)
            if offer is not None:
                return offer
            wait = min(self.config.poll_timeout, entry.deadline.remaining(self._now(), self.epoch))
            try:
                await asyncio.wait_for(entry.changed.wait(), wait)
            except asyncio.TimeoutError:
                pass
            self._entry(token, session)
            return await self._offer(entry)
        finally:
            entry.polling = False
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запись CLAIMED и STARTED до выдачи разрешения приложению
    #--------------------------------------------------------------------------------------------------------------
    async def claim(
        self,
        token: str,
        claim: CommandClaim,
    ) -> CommandGrant | CommandResult:

        """Persist CLAIMED and STARTED before returning a single execution authorization.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param claim: Correlated request for a single execution permission.
        :type claim: CommandClaim

        :return: Single execution grant or an already committed terminal result.
        :rtype: CommandGrant | CommandResult
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # claim — запрос разрешения на единственное исполнение команды.

        entry = self._ref_entry(token, claim.ref)
        session = self._session(entry)
        lease = entry.deadline


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Проверка и запись решения в единственном рабочем потоке журнала
        #----------------------------------------------------------------------------------------------------------
        def commit() -> CommandGrant | CommandResult:

            """Make the start decision durably without invoking application code.

            :return: Single execution grant or an already committed terminal result.
            :rtype: CommandGrant | CommandResult
            """

            store = self._worker.store
            stored = store.get(claim.ref)

            if stored is None:
                raise CommandError("invalid")

            if claim.request_digest != message_digest(stored.record.request):
                raise CommandError("conflict")

            if stored.record.claim_id is not None and stored.record.claim_id != claim.claim_id:
                raise CommandError("conflict")

            if stored.record.result is not None:
                return stored.record.result

            if stored.record.phase is CommandPhase.STARTED:
                raise CommandError("already_started")

            # Сначала журнал разрешает единственное исполнение. Если HTTP-ответ
            # затем потеряется, повторный claim уже не выдаст второй grant.
            result = store.transition(claim.ref, stored.revision, CommandAction.CLAIM, session, claim, lease)
            stored = result.command

            if stored.record.result is not None:
                return stored.record.result
            capability = next(
                cap for cap in entry.registration.capabilities if cap.name == stored.record.request.name)
            now = self._clock()
            remaining = min(stored.deadline.remaining(now, self.epoch), lease.remaining(now, self.epoch))

            if remaining <= 0:
                raise CommandError("expired")
            duration = min(capability.timeout, remaining)
            grant = CommandGrant(request=stored.record.request, claim_id=claim.claim_id,
                                 remaining_ttl=remaining, execution_timeout=duration)
            bound = CommandDeadline(hub_epoch=self.epoch, sent_at=now, received_at=now, expires_at=now + duration)
            committed = store.transition(claim.ref, stored.revision, CommandAction.START, session, claim,
                                         lease, grant=grant, grant_deadline=bound)

            if not committed.start_callback:
                raise CommandError("expired")
            return grant
        #----------------------------------------------------------------------------------------------------------


        return await self._worker.call(commit)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Сохранение точного результата выданного исполнения
    #--------------------------------------------------------------------------------------------------------------
    async def finish(
        self,
        token: str,
        result: CommandResult,
    ) -> CommandReceipt:

        """Persist a result from its owning session, including a late but unambiguous result.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param result: Correlated terminal result whose exact contents must be retained.
        :type result: CommandResult

        :return: Receipt bound to the exact durably stored result digest.
        :rtype: CommandReceipt
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # result — точный итог выполнения с идентификаторами исходной команды.

        entry = self._ref_entry(token, result.ref, live=False)
        registration = entry.registration
        session = CommandSession(identity=registration.identity, session_id=registration.session_id,
                                 hub_epoch=self.epoch, remaining_ttl=self.config.session_ttl)


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Проверка и запись решения в единственном рабочем потоке журнала
        #----------------------------------------------------------------------------------------------------------
        def commit() -> CommandReceipt:

            """Return a receipt only after storing the exact correlated result.

            :return: Receipt bound to the exact durably stored result digest.
            :rtype: CommandReceipt
            """

            store = self._worker.store
            stored = store.get(result.ref)

            if stored is None or result.claim_id is None:
                raise CommandError("invalid")

            if stored.record.result is not None:
                if stored.record.result != result:
                    raise CommandError("outcome_conflict")
            else:
                claim = CommandClaim(ref=result.ref, claim_id=result.claim_id,
                                     request_digest=message_digest(stored.record.request))
                action = (CommandAction.REJECT if result.outcome is CommandOutcome.REJECTED
                          else CommandAction.FINISH)
                changed = store.transition(result.ref, stored.revision, action,
                                 session, claim, entry.deadline, result=result)
                if changed.command.record.result != result:
                    raise CommandError("outcome_conflict")
            return CommandReceipt(ref=result.ref, claim_id=result.claim_id, result_digest=message_digest(result))
        #----------------------------------------------------------------------------------------------------------


        return await self._worker.call(commit)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение результатов только для своего источника
    #--------------------------------------------------------------------------------------------------------------
    async def results(
        self,
        token: str,
    ) -> tuple[StoredCommand, ...]:

        """Expose only this trusted source's pending terminal results for provider replies.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :return: Pending terminal records belonging only to the authenticated source.
        :rtype: tuple[StoredCommand, ...]
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.

        source = self._source_policy(token)
        await self.sweep()
        rows = await self._worker.call(lambda: self._worker.store.pending(limit=1000))
        return tuple(row for row in rows if row.record.request.source_id == source.source_id
                     and row.record.result is not None and not row.acknowledged)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Завершение просроченных записей без предположения об остановке callback
    #--------------------------------------------------------------------------------------------------------------
    async def sweep(self) -> None:

        """Expire abandoned commands without pretending that a running callback stopped."""

        self._check()
        # Снимок регистраций исключает чтение изменяемого словаря из рабочего потока.
        leases = {sid: entry.deadline for sid, entry in self._issued.items()}


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Запись итогов команд с истёкшим сроком
        #----------------------------------------------------------------------------------------------------------
        def expire() -> None:

            """Persist terminal outcomes for expired commands and leases."""

            store = self._worker.store

            for stored in store.pending(limit=1000):
                record = stored.record
                ref = record.request.ref
                lease = leases.get(ref.session_id)
                if record.result is not None or stored.deadline is None or lease is None:
                    continue
                now = self._clock()
                if min(stored.deadline.remaining(now, self.epoch), lease.remaining(now, self.epoch)) > 0:
                    continue
                session = CommandSession(identity=ref.identity, session_id=ref.session_id,
                                         hub_epoch=self.epoch, remaining_ttl=1)
                claim = CommandClaim(ref=ref, claim_id=record.claim_id or token_hex(16),
                                     request_digest=message_digest(record.request))
                store.transition(ref, stored.revision, CommandAction.CLAIM, session, claim, lease)
            store.prune()
        #----------------------------------------------------------------------------------------------------------


        await self._worker.call(expire)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение ответа провайдера отдельно от квитанции приложению
    #--------------------------------------------------------------------------------------------------------------
    async def acknowledge(
        self,
        token: str,
        result: CommandResult,
    ) -> None:

        """Acknowledge a provider reply separately from the application's result receipt.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param result: Correlated terminal result whose exact contents must be retained.
        :type result: CommandResult
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # result — точный итог выполнения с идентификаторами исходной команды.

        source = self._source_policy(token)


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Проверка и запись решения в единственном рабочем потоке журнала
        #----------------------------------------------------------------------------------------------------------
        def commit() -> None:

            """Check source ownership before acknowledging the exact result."""

            stored = self._worker.store.get(result.ref)

            if stored is None or stored.record.request.source_id != source.source_id:
                raise CommandError("denied")
            self._worker.store.acknowledge(result)
            self._worker.store.prune()
        #----------------------------------------------------------------------------------------------------------


        await self._worker.call(commit)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Поиск точной живой цели для доверенного источника
    #--------------------------------------------------------------------------------------------------------------
    def target(
        self,
        token: str,
        identity: Identity,
    ) -> CommandSession:

        """Resolve an exact live target for a trusted source before constructing a request.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param identity: Exact application identity, without wildcard fields.
        :type identity: Identity

        :return: Exact registration with a conservative remaining lifetime.
        :rtype: CommandSession
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # identity — точные сведения о приложении без шаблонов и догадок.

        source = self._source_policy(token)

        if not any(rule.identity == identity for rule in source.access):
            raise CommandError("denied")
        entry = self._targets.get(identity)

        if entry is None:
            raise CommandError("stale_session")
        return self._session(entry)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка жизненного цикла и владельца event loop
    #--------------------------------------------------------------------------------------------------------------
    def _check(self) -> None:

        """Reject cross-loop use and calls outside the hub lifecycle."""

        if self._closed or self._loop is None or asyncio.get_running_loop() is not self._loop:
            raise CommandError("closed")
        self._now()
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка монотонного времени с запретом работы после отката
    #--------------------------------------------------------------------------------------------------------------
    def _now(self) -> float:

        """Fail closed if the shared monotonic clock rolls back.

        :return: Current validated monotonic time in seconds.
        :rtype: float
        """

        now = self._clock()
        require_number(now, "monotonic time", allow_zero=True)

        if now < self._last or self._failed_clock:
            self._failed_clock = True
            raise CommandError("unavailable")
        self._last = now
        return now
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Создание локального срока регистрации
    #--------------------------------------------------------------------------------------------------------------
    def _deadline(
        self,
        duration: float,
    ) -> CommandDeadline:

        """Construct a local lease on the same clock used by the journal.

        :param duration: Relative local lifetime in seconds.
        :type duration: float

        :return: Conservative local deadline in the shared monotonic domain.
        :rtype: CommandDeadline
        """

        # duration — относительный срок на локальных часах, секунды.

        now = self._now()
        return CommandDeadline(hub_epoch=self.epoch, sent_at=now, received_at=now, expires_at=now + duration)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Остаток регистрации без её продления
    #--------------------------------------------------------------------------------------------------------------
    def _session(
        self,
        entry: _Session,
    ) -> CommandSession:

        """Return remaining registration lifetime without renewing it.

        :param entry: Registration record owned by the current hub.
        :type entry: _Session

        :return: Exact registration with a conservative remaining lifetime.
        :rtype: CommandSession
        """

        # entry — запись регистрации текущего запуска hub.

        remaining = entry.deadline.remaining(self._now(), self.epoch)

        if remaining <= 0:
            raise CommandError("stale_session")
        registration = entry.registration
        return CommandSession(identity=registration.identity, session_id=registration.session_id,
                              hub_epoch=self.epoch, remaining_ttl=remaining)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка владельца, Identity, сессии и запуска hub
    #--------------------------------------------------------------------------------------------------------------
    def _entry(
        self,
        token: str,
        session: CommandSession,
    ) -> _Session:

        """Bind an authenticated client to its exact live identity, session and hub epoch.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param session: Previously issued exact application registration.
        :type session: CommandSession

        :return: Hub-owned registration after checking all required ownership fields.
        :rtype: _Session
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # session — ранее выданная регистрация конкретного приложения.

        principal = self.authenticate(token)

        if type(session) is not CommandSession:
            raise CommandError("invalid")
        entry = self._issued.get(session.session_id)

        if (entry is None or entry.principal != principal.name or session.hub_epoch != self.epoch
                or session.identity != entry.registration.identity):
            raise CommandError("stale_session")
        self._session(entry)
        return entry
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка принадлежности команды приложению
    #--------------------------------------------------------------------------------------------------------------
    def _ref_entry(
        self,
        token: str,
        ref: CommandRef,
        live: bool = True,
    ) -> _Session:

        """Check result or claim ownership without granting privileges from a request body.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :param ref: Exact identity, process session, hub epoch and command identifier.
        :type ref: CommandRef

        :param live: Whether an unexpired registration is required.
        :type live: bool

        :return: Hub-owned registration after checking all required ownership fields.
        :rtype: _Session
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.
        # ref — точные Identity, сессия процесса, запуск hub и ID команды.
        # live — нужна ли ещё действующая регистрация приложения.

        principal = self.authenticate(token)

        if type(ref) is not CommandRef:
            raise CommandError("invalid")
        entry = self._issued.get(ref.session_id)

        if (entry is None or entry.principal != principal.name or ref.hub_epoch != self.epoch
                or ref.identity != entry.registration.identity):
            raise CommandError("stale_session")

        if live:
            self._session(entry)
        return entry
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Авторизация доверенного адаптера входящих сообщений
    #--------------------------------------------------------------------------------------------------------------
    def _source_policy(
        self,
        token: str,
    ) -> CommandSource:

        """Authorize trusted source calls separately from all application credentials.

        :param token: Command-only credential, never logged or echoed.
        :type token: str

        :return: Trusted source policy including its actor and conversation ACL.
        :rtype: CommandSource
        """

        # token — отдельный секрет команд, не выводимый в сообщения и журнал.

        self._check()

        for source in self.config.sources:
            if _matches(token, source.token):
                return source
        raise CommandError("denied")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Выбор первой непросроченной команды этой сессии
    #--------------------------------------------------------------------------------------------------------------
    async def _offer(
        self,
        entry: _Session,
    ) -> CommandOffer | None:

        """Scan a bounded journal page and expire stale work before delivering a candidate.

        :param entry: Registration record owned by the current hub.
        :type entry: _Session

        :return: Earliest eligible command and budget, or None if no work is available.
        :rtype: CommandOffer | None
        """

        # entry — запись регистрации текущего запуска hub.

        session = self._session(entry)


        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Выбор команды из ограниченной страницы журнала
        #----------------------------------------------------------------------------------------------------------
        def select() -> CommandOffer | None:

            """Select the earliest pending command for this exact session.

            :return: Earliest eligible command and budget, or None if no work is available.
            :rtype: CommandOffer | None
            """

            store = self._worker.store

            for stored in store.pending(limit=1000):
                record = stored.record
                if not record.request.ref.matches(session) or record.result is not None:
                    continue
                remaining = stored.deadline.remaining(self._clock(), self.epoch)
                if remaining <= 0:
                    claim = CommandClaim(ref=record.request.ref, claim_id=record.claim_id or token_hex(16),
                                         request_digest=message_digest(record.request))
                    store.transition(record.request.ref, stored.revision, CommandAction.CLAIM,
                                     session, claim, entry.deadline)
                    continue
                return CommandOffer(sequence=stored.sequence, request=record.request, remaining_ttl=remaining)
            return None
        #----------------------------------------------------------------------------------------------------------


        return await self._worker.call(select)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Обновление достоверного времени с конечным ожиданием
    #--------------------------------------------------------------------------------------------------------------
    async def _update_time(self) -> None:

        """Bound time-source refresh and keep admissions closed after a failed update."""

        try:
            await asyncio.wait_for(self._trusted.refresh(self._source), 5.0)
        except Exception:
            pass
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Периодическое обслуживание времени и просроченных команд
    #--------------------------------------------------------------------------------------------------------------
    async def _refresh_time(self) -> None:

        """Maintain one time estimate without accumulating timer tasks or retry queues."""

        refreshed = self._clock()

        while not self._closed:
            await asyncio.sleep(min(1.0, self.config.refresh_interval))
            if self._source is not None and self._clock() - refreshed >= self.config.refresh_interval:
                await self._update_time()
                refreshed = self._clock()
            try:
                await self.sweep()
            except CommandError:
                # Занятая запись не создаёт очередь обслуживания; следующий проход позже.
                pass
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Сравнение хешей секретов без выдачи исходных значений
#------------------------------------------------------------------------------------------------------------------
def _matches(
    candidate: str,
    expected: str,
) -> bool:

    """Compare fixed-size credential digests without exposing the original secret.

    :param candidate: Untrusted credential supplied by the caller.
    :type candidate: str

    :param expected: Configured credential used for digest comparison.
    :type expected: str

    :return: True only when the requested credential or execution prerequisite is valid.
    :rtype: bool
    """

    # candidate — предъявленный секрет, который требуется проверить.
    # expected — секрет из конфигурации для сравнения хешей.

    if type(candidate) is not str or len(candidate) > 256:
        return False
    return hmac.compare_digest(
        hashlib.sha256(candidate.encode()).digest(), hashlib.sha256(expected.encode()).digest())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль hub не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
