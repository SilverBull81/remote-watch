# Приём команд от провайдеров и независимая доставка сохранённых результатов.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-210047
#
# Классы:
# -> SourceStats: Счётчики источника без приватных данных.
#
# -> CommandSourceRunner: Связь одного провайдера с постоянным журналом и hub.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> stats(): Чтение счётчиков и состояния источника.
#    -> start(): Открытие ресурсов до начала приёма команд.
#    -> activate(): Начало приёма после проверки всех источников.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.
#    -> step(): Обработка сохранённого решения либо одной порции событий.
#    Служебные методы:
#    -> _prepare(): Проверка прав и выбор точной живой сессии.
#    -> _commit(): Завершение сохранённого решения без выбора новой сессии.
#    -> _receive(): Получение событий с конечными паузами после отказа.
#    -> _respond(): Доставка сохранённых ответов без повторного callback.
#    -> _finished(): Учёт неожиданного завершения фоновой задачи.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from hashlib import sha256
from secrets import token_hex

from remote_watch._validation import require_number
from remote_watch.commands._worker import StoreWorker
from remote_watch.commands.hub import CommandHub
from remote_watch.commands.hub_config import CommandSource
from remote_watch.commands.protocol import CommandRef, CommandRequest
from remote_watch.commands.source_protocol import (
    CommandProvider,
    SourceEvent,
    SourcePending,
    SourceTargets,
    bound_source_text,
    parse_source_command,
    source_result_text,
)
from remote_watch.commands.source_store import SourceJournal
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Счётчики источника без приватных данных
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class SourceStats:
    """Expose operational health without incoming text, targets or credentials."""

    accepted: int           # Команды, впервые принятые hub в текущем запуске.
    ignored: int            # События без принятой команды, включая повторы и отказы.
    replies: int            # Ответы, принятые провайдером; не подтверждение показа на телефоне.
    closed: bool            # Получатель больше не принимает события.
    last_error: str | None  # Последний фиксированный код отказа без его приватных деталей.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Связь одного провайдера с постоянным журналом и hub
#------------------------------------------------------------------------------------------------------------------
class CommandSourceRunner:
    """Bridge one provider to the hub using a durable checkpoint and separate bounded reply loop."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        hub: CommandHub,
        policy: CommandSource,
        targets: SourceTargets,
        provider: CommandProvider,
        journal: SourceJournal,
        *,
        retry_interval: float = 2.0,
        short_commands: bool = False,
    ) -> None:

        """Bind explicit source ownership without opening journals or contacting the provider.

        :param hub: Independently configured command hub.
        :type hub: CommandHub

        :param policy: Explicit source authorization rules configured on the hub.
        :type policy: CommandSource

        :param targets: Explicit unique aliases for complete application identities.
        :type targets: SourceTargets

        :param provider: Protocol implementation for one independently owned provider stream.
        :type provider: CommandProvider

        :param journal: Durable bounded journal belonging only to this source.
        :type journal: SourceJournal

        :param retry_interval: Finite delay before the next failed-operation attempt, seconds.
        :type retry_interval: float

        :param short_commands: Enable implicit-target commands for a provider supporting Telegram syntax.
        :type short_commands: bool
        """

        # hub — отдельно настроенный командный hub.
        # policy — явные права источника, заданные на hub.
        # targets — явные уникальные адреса с полными Identity приложений.
        # provider — реализация протокола одного отдельно принадлежащего потока событий.
        # journal — постоянный ограниченный журнал только этого источника.
        # retry_interval — конечная пауза перед повтором операции после отказа, секунды.
        # short_commands — короткий синтаксис при неизменных ACL и привязке к сессии.

        if policy not in hub.config.sources:
            raise ValueError("source policy differs from hub")

        if any(rule.identity not in targets.aliases.values() for rule in policy.access):
            raise ValueError("source target alias missing")
        require_number(retry_interval, "source retry interval")

        if retry_interval > 60:
            raise ValueError("source retry interval")
        if type(short_commands) is not bool:
            raise ValueError("invalid short command mode")
        self._short_commands = short_commands
        self.hub = hub
        self.policy = policy
        self.targets = targets
        self.provider = provider
        self._worker = StoreWorker(journal, 3.0)
        self._retry = retry_interval
        self._tasks: tuple[asyncio.Task, ...] = ()
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._accepted = self._ignored = self._replies = 0
        self._error: str | None = None
        self._reply_position = 0
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение счётчиков и состояния источника
    #--------------------------------------------------------------------------------------------------------------
    @property
    def stats(self) -> SourceStats:

        """Read bounded process counters and whether the source has stopped.

        :return: Counters and source health without private message content.
        :rtype: SourceStats
        """

        for task in self._tasks:
            if task.done():
                self._finished(task)
        return SourceStats(accepted=self._accepted, ignored=self._ignored, replies=self._replies,
                           closed=self._stopping, last_error=self._error)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов до начала приёма команд
    #--------------------------------------------------------------------------------------------------------------
    async def start(
        self,
        *,
        activate: bool = True,
    ) -> None:

        """Open the exclusive journal before contacting or acknowledging the provider.

        :param activate: Whether to begin consuming after startup validation.
        :type activate: bool
        """

        # activate — начинать ли приём сразу после проверки источника.

        if self._loop is not None or self._stopping:
            raise CommandError("closed")
        self._loop = asyncio.get_running_loop()

        try:
            await self._worker.open()
            fingerprint = await self.provider.open()
            owner = sha256((self.policy.source_id + "\0" + fingerprint).encode("utf-8")).hexdigest()
            reader_key = sha256(fingerprint.encode("utf-8")).hexdigest()
            await self._worker.call(lambda: self._worker.store.bind(owner, reader_key))
            position, _, pending = await self._worker.call(self._worker.store.state)
            cursor = await self.hub.source_cursor(self.policy.token)
            if cursor != position and (pending is None or cursor != pending.position):
                raise CommandError("conflict")
            if activate:
                self.activate()
        except BaseException:
            await self.close()
            raise
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Начало приёма после проверки всех источников
    #--------------------------------------------------------------------------------------------------------------
    def activate(self) -> None:

        """Begin consuming only after all gateway sources have passed startup validation."""

        if self._loop is None or self._stopping or self._tasks:
            raise CommandError("closed")
        self._tasks = (asyncio.create_task(self._receive()), asyncio.create_task(self._respond()))

        for task in self._tasks:
            task.add_done_callback(self._finished)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Stop both loops before closing the provider and its durable source journal."""

        self._stopping = True

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5)

        try:
            await asyncio.wait_for(self.provider.close(), 5)
        finally:
            await self._worker.close(5)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Обработка сохранённого решения либо одной порции событий
    #--------------------------------------------------------------------------------------------------------------
    async def step(self) -> None:

        """Process one bounded provider batch, always completing a previously persisted decision first."""

        journal = self._worker.store
        cutoff = self.hub.source_cutoff(self.policy.token)
        await self._worker.call(lambda: journal.prune(cutoff))
        _, cursor, pending = await self._worker.call(journal.state)

        if pending is not None:
            # Решение могло сохраниться до сбоя, а ответ hub — потеряться.
            # Не читаем следующее событие и не выбираем сессию повторно.
            await self._commit(pending)
            return
        events = await self.provider.poll(cursor)

        if type(events) is not tuple or len(events) > 32:
            raise CommandError("invalid")

        for event in events:
            if type(event) is not SourceEvent:
                raise CommandError("invalid")
            if await self._worker.call(lambda: journal.contains(event.event_id)):
                # ID уже обработан: перемещаем только позицию провайдера.
                # Новый command_id здесь создал бы повтор побочного действия.
                await self._worker.call(lambda: journal.skip_duplicate(event.event_id))
                await self.provider.acknowledge(event.event_id)
                self._ignored += 1
                continue
            position, _, _ = await self._worker.call(journal.state)
            retired = await self._worker.call(lambda: journal.retired(event.message_date))
            pending = (SourcePending(position=position + 1, event=event, request=None)
                       if retired else self._prepare(event, position + 1))
            await self._worker.call(lambda: journal.reserve(pending))
            await self._commit(pending)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка прав и выбор точной живой сессии
    #--------------------------------------------------------------------------------------------------------------
    def _prepare(
        self,
        event: SourceEvent,
        position: int,
    ) -> SourcePending:

        """Authenticate routing fields and bind one live session before persisting the decision.

        :param event: Bounded provider event with authenticated routing fields.
        :type event: SourceEvent

        :param position: Monotonically increasing local source position.
        :type position: int

        :return: Durable decision bound to the original provider event and target session.
        :rtype: SourcePending
        """

        # event — ограниченное событие с проверенными полями маршрутизации.
        # position — собственная возрастающая позиция источника.

        rules = tuple(rule for rule in self.policy.access if rule.actor_id == event.actor_id
                      and rule.conversation_id == event.conversation_id)
        request = None
        notice = None
        short_help = False

        if rules and event.text is not None:
            try:
                alias, name, arguments = parse_source_command(event.text, short_commands=self._short_commands)
                short = self._short_commands and shlex.split(event.text, comments=False, posix=True)[0] != "/rw"
                allowed = [key for key, identity in self.targets.aliases.items()
                           if any(rule.identity == identity for rule in rules)]
                # Список определяется конфигурацией ACL, а не текущей доступностью
                # приложений. Отключение второго instance не меняет адрес команды.
                if short and len(allowed) == 1:
                    alias = allowed[0]
                if alias is None:
                    notice = "Адреса приложений: " + ", ".join(allowed) + ". Справка: /rw адрес"
                else:
                    identity = self.targets.aliases.get(alias)
                    if identity is None or not any(rule.identity == identity for rule in rules):
                        raise CommandError("denied")
                    names = self.hub.commands_for(
                        self.policy.token, identity, event.actor_id, event.conversation_id)
                    if name is None:
                        if short:
                            notice = "Allowed Bot Commands:\n" + "\n".join("/" + item for item in names)
                            short_help = True
                        else:
                            notice = alias + ": " + ", ".join(names) + ". Вызов: /rw адрес команда [имя=значение]"
                    elif name not in names:
                        raise CommandError("denied")
                    else:
                        session = self.hub.target(self.policy.token, identity)
                        ref = CommandRef(identity=identity, session_id=session.session_id,
                                         hub_epoch=session.hub_epoch, command_id=token_hex(16))
                        request = CommandRequest(ref=ref, source_id=self.policy.source_id,
                            source_event_id=event.event_id, actor_id=event.actor_id,
                            conversation_id=event.conversation_id, name=name, arguments=arguments)
            except (ValueError, TypeError):
                notice = "Неверная команда. Формат: /rw адрес команда [имя=значение]. Справка: /rw"
            except CommandError as error:
                notice = "Приложение недоступно." if error.code == "stale_session" else "Команда не разрешена."
        return SourcePending(position=position, event=event, request=request,
                             notice=notice if notice is None or short_help else bound_source_text(notice))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Завершение сохранённого решения без выбора новой сессии
    #--------------------------------------------------------------------------------------------------------------
    async def _commit(
        self,
        pending: SourcePending,
    ) -> None:

        """Finish the same journalled decision across response loss without choosing a new session.

        :param pending: Previously selected request and its original provider event.
        :type pending: SourcePending
        """

        # pending — выбранный запрос вместе с исходным событием провайдера.

        retired = await self._worker.call(lambda: self._worker.store.retired(pending.event.message_date))

        if pending.request is None or retired:
            admitted = await self.hub.skip_source(self.policy.token, position=pending.position,
                                                  expected_cursor=pending.position - 1)
        else:
            admitted = await self.hub.submit(self.policy.token, pending.request, position=pending.position,
                expected_cursor=pending.position - 1, message_date=pending.event.message_date)

        if admitted.cursor != pending.position:
            raise CommandError("conflict")
        await self._worker.call(lambda: self._worker.store.commit(pending))
        # Подтверждение провайдеру идёт последним. Если его ответ потеряется,
        # повторное чтение найдёт уже сохранённый ID в журнале источника.
        await self.provider.acknowledge(pending.event.event_id)

        if admitted.inserted:
            self._accepted += 1
        else:
            self._ignored += 1
        notice = pending.notice

        if pending.request is not None and admitted.command is None:
            notice = "Команда отклонена: права, срок или достоверное время недоступны."

        if notice is not None and pending.event.conversation_id is not None:
            # Справка/отказ не запускают приложение. Их ответ — одна попытка после
            # постоянного решения; потеря такого ответа не повторяет входящую команду.
            # Все разрешённые команды должны попасть в справку. Максимум 64 имени
            # по 64 ASCII-символа помещаются в две ограниченные страницы, без усечения строки.
            prefix = "Allowed Bot Commands:\n"
            if self._short_commands and notice.startswith(prefix):
                page = prefix.rstrip()
                for line in notice[len(prefix):].splitlines():
                    if len((page + "\n" + line).encode("utf-8")) > 3800:
                        await self.provider.reply(pending.event.conversation_id, page)
                        page = prefix.rstrip()
                    page += "\n" + line
                await self.provider.reply(pending.event.conversation_id, page)
            else:
                await self.provider.reply(pending.event.conversation_id, bound_source_text(notice))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Получение событий с конечными паузами после отказа
    #--------------------------------------------------------------------------------------------------------------
    async def _receive(self) -> None:

        """Poll with a finite pause and close admission on permanent source failures."""

        while not self._stopping:
            try:
                await self.step()
                await asyncio.sleep(0.1)
            except CommandError as error:
                self._error = error.code
                if error.code not in ("busy", "capacity", "unavailable"):
                    self._stopping = True
                    return
                await asyncio.sleep(self._retry)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Доставка сохранённых ответов без повторного callback
    #--------------------------------------------------------------------------------------------------------------
    async def _respond(self) -> None:

        """Retry retained results without rerunning callbacks or starving unrelated chats."""

        while not self._stopping:
            try:
                rows = await self.hub.results(self.policy.token)
                # За проход отправляем не более 32 результатов. Начало сдвигается,
                # чтобы недоступный чат не задерживал остальные приложения.
                count = len(rows)
                batch = tuple(rows[(self._reply_position + i) % count] for i in range(min(count, 32)))
                self._reply_position = (self._reply_position + len(batch)) % max(count, 1)
                for row in batch:
                    request = row.record.request
                    # При изменении ACL прежний ответ не должен уйти исключённому получателю.
                    permitted = any(rule.actor_id == request.actor_id
                                    and rule.conversation_id == request.conversation_id
                                    and rule.identity == request.ref.identity for rule in self.policy.access)
                    if not permitted:
                        continue
                    try:
                        await self.provider.reply(
                            request.conversation_id, source_result_text(request, row.record.result))
                        await self.hub.acknowledge(self.policy.token, row.record.result)
                        self._replies += 1
                    except CommandError as error:
                        self._error = error.code
                        if error.code in ("busy", "unavailable"):
                            break
                await asyncio.sleep(self._retry)
            except CommandError as error:
                self._error = error.code
                await asyncio.sleep(self._retry)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Учёт неожиданного завершения фоновой задачи
    #--------------------------------------------------------------------------------------------------------------
    def _finished(
        self,
        task: asyncio.Task,
    ) -> None:

        """Consume task failures and make unexpected source termination visible to the owner.

        :param task: Completed owned asynchronous task.
        :type task: asyncio.Task
        """

        # task — завершённая асинхронная задача, принадлежащая источнику.

        if not task.cancelled():
            task.exception()
        # Штатная остановка заранее устанавливает _stopping. Во всех остальных
        # случаях даже отмена или обычный ранний return означают потерю одного loop.
        if not self._stopping:
            self._error = "unavailable"
            self._stopping = True
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль source не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
