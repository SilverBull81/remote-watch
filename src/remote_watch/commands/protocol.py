# Модели и строгий wire-контракт команд без сети и исполнения callbacks.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-102445
#
# Константы и типы:
# -> MAX_COMMAND_BYTES, MAX_COMMAND_TEXT_BYTES: Пределы wire и текста ответа, байт UTF-8.
# -> MAX_COMMAND_SECONDS, MAX_CAPABILITIES: Пределы срока и числа зарегистрированных команд.
# -> CommandMessage: Закрытый набор моделей сообщений.
# -> _KINDS, _NESTED: Закрытые таблицы видов сообщений и вложенных моделей.
#
# Состав модуля:
# -> CommandOutcome: Результат обработчика без предположений о состоянии приложения.
#
# -> CommandReason: Фиксированные безопасные причины отказа.
#
# -> _identifier(): Проверка ограниченного служебного имени.
# -> _nonce(): Проверка формата случайного идентификатора.
# -> _digest(): Проверка формата хеша данных.
# -> _seconds(): Ограничение присланного интервала времени.
# -> CommandCapability: Метаданные команды без ссылки на callback.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandRegistration: Регистрация запуска приложения и его команд.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandSession: Связь приложения с текущим запуском hub.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandRef: Полная цель и идентификатор одной команды.
#    Интерфейс:
#    -> matches(): Проверка точной связи запроса и ответа.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandRequest: Неизменяемое намерение пользователя.
#    Интерфейс:
#    -> context(): Подготовка контекста без исполнения и выдачи прав.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandClaim: Запрос единственной попытки исполнения.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandGrant: Разрешение на начало конкретной попытки.
#    Интерфейс:
#    -> matches(): Проверка точной связи запроса и ответа.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandResult: Завершение обработчика или неизвестный исход.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandReceipt: Подтверждение сохранения точного результата.
#    Интерфейс:
#    -> matches(): Проверка точной связи запроса и ответа.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> describe_commands(): Подготовка метаданных без вызова обработчиков.
# -> callback_result(): Проверка результата без пользовательского преобразования в строку.
# -> _plain(): Преобразование только разрешённых моделей в данные JSON.
# -> encode_command(): Кодирование ограниченного сообщения заданной версии.
# -> _schema_version(): Выбор схемы без изменения прежних сообщений и их хешей.
# -> _pairs(): Запрет повторяющихся ключей JSON.
# -> _constant(): Запрет нестандартных чисел JSON.
# -> _model(): Разбор точного состава полей по закрытому перечню моделей.
# -> decode_command(): Чтение сообщения без раскрытия ошибочного содержимого.
# -> message_digest(): Хеш канонических данных для проверки связи сообщений.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import TypeAlias

from remote_watch._validation import require_number, require_text
from remote_watch.commands.registry import CommandContext, CommandRegistry
from remote_watch.events import Identity

MAX_COMMAND_BYTES = 65536
MAX_COMMAND_TEXT_BYTES = 4096
MAX_COMMAND_SECONDS = 300
MAX_CAPABILITIES = 64


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Результат обработчика без предположений о состоянии приложения
#------------------------------------------------------------------------------------------------------------------
class CommandOutcome(str, Enum):
    """Describe callback completion, never inferred completion of application work."""

    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Фиксированные безопасные причины отказа
#------------------------------------------------------------------------------------------------------------------
class CommandReason(str, Enum):
    """Expose fixed reasons without exception text or provider payloads."""

    DENIED = "denied"
    INVALID_ARGUMENTS = "invalid_arguments"
    EXPIRED = "expired"
    CALLBACK_ERROR = "callback_error"
    INVALID_RESULT = "invalid_result"
    TIMEOUT = "timeout"
    RESTART = "restart"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка ограниченного служебного имени
#------------------------------------------------------------------------------------------------------------------
def _identifier(value: str) -> None:

    """Validate an opaque identifier without allowing arbitrary wire text.

    :param value: Value to validate or normalize.
    :type value: str
    """

    # value — непустой идентификатор с ограниченной длиной.

    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
        raise ValueError("invalid command identifier")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка формата случайного идентификатора
#------------------------------------------------------------------------------------------------------------------
def _nonce(value: str) -> None:

    """Require the shape of a 128-bit randomly generated identifier.

    :param value: Value to validate or normalize.
    :type value: str
    """

    # value — случайный идентификатор из 32 шестнадцатеричных символов.

    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise ValueError("invalid command nonce")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка формата хеша данных
#------------------------------------------------------------------------------------------------------------------
def _digest(value: str) -> None:

    """Validate the hexadecimal representation of a SHA-256 digest.

    :param value: Value to validate or normalize.
    :type value: str
    """

    # value — контрольная сумма сообщения в шестнадцатеричном виде.

    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("invalid command digest")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ограничение присланного интервала времени
#------------------------------------------------------------------------------------------------------------------
def _seconds(value: float) -> None:

    """Bound all remotely supplied command intervals.

    :param value: Value to validate or normalize.
    :type value: float
    """

    # value — проверяемый срок действия, секунды.

    require_number(value, "command interval")
    if value > MAX_COMMAND_SECONDS:
        raise ValueError("command interval exceeds limit")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Метаданные команды без ссылки на callback
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandCapability:
    """Advertise application-owned metadata without a callback reference."""

    name: str                           # Точное имя команды в приложении.
    required_scope: str                 # Требуемое право; не выданное разрешение.
    timeout: float = 10.0               # Максимальный срок обработчика, секунды.
    read_only: bool = False             # Явное заявление приложения об отсутствии изменений.
    idempotent: bool = False            # Заявление автора; не разрешает автоматический повтор.
    accepts_arguments: bool = False     # Есть ли у локальной команды валидатор аргументов.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject invalid names, flags and unbounded execution budgets."""

        if not isinstance(self.name, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name) is None:
            raise ValueError("invalid command name")
        require_text(self.required_scope, "required_scope", 128)
        _seconds(self.timeout)
        for name in ("read_only", "idempotent", "accepts_arguments"):
            if type(getattr(self, name)) is not bool:
                raise TypeError("command capability flag must be bool")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Регистрация запуска приложения и его команд
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandRegistration:
    """Register one application session and a finite capability snapshot."""

    identity: Identity      # Полная принадлежность приложения.
    session_id: str         # Новый случайный ID при каждом запуске клиента.
    capabilities: tuple[CommandCapability, ...]     # Только метаданные, без функций и состояния приложения.
    command_display_mode: str | None = None        # full/compact/text; None — оформление источника gateway.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy capabilities and reject duplicate names before registration."""

        if type(self.identity) is not Identity:
            raise TypeError("identity must be Identity")
        _nonce(self.session_id)
        if self.command_display_mode is not None and self.command_display_mode not in ("full", "compact", "text"):
            raise ValueError("invalid command display mode")
        if not isinstance(self.capabilities, (tuple, list)) or not 1 <= len(self.capabilities) <= MAX_CAPABILITIES:
            raise ValueError("invalid capability count")
        if any(type(item) is not CommandCapability for item in self.capabilities):
            raise TypeError("invalid capability type")
        copied = tuple(self.capabilities)
        if len({item.name for item in copied}) != len(copied):
            raise ValueError("duplicate command capability")
        object.__setattr__(self, "capabilities", copied)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Связь приложения с текущим запуском hub
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandSession:
    """Bind one live application session to a specific hub incarnation."""

    identity: Identity      # Единственная разрешённая цель.
    session_id: str         # Идентификатор запуска приложения.
    hub_epoch: str          # Новый случайный ID при каждом запуске hub.
    remaining_ttl: float    # Остаток регистрации на момент ответа, секунды.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate session fencing and a bounded relative lease."""

        if type(self.identity) is not Identity:
            raise TypeError("identity must be Identity")
        _nonce(self.session_id)
        _nonce(self.hub_epoch)
        _seconds(self.remaining_ttl)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Полная цель и идентификатор одной команды
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandRef:
    """Identify one command within its exact target session and hub epoch."""

    identity: Identity      # Все пять полей целевого приложения.
    session_id: str         # Запуск приложения, которому адресована команда.
    hub_epoch: str          # Запуск hub, принявший команду.
    command_id: str         # Уникальный ID намерения; повтор сохраняет этот ID.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка точной связи запроса и ответа
    #--------------------------------------------------------------------------------------------------------------
    def matches(
        self,
        session: CommandSession,
    ) -> bool:

        """Check exact target binding without claiming authentication or liveness.

        :param session: Current authenticated session metadata; liveness is checked separately.
        :type session: CommandSession

        :return: True when all required correlation fields agree.
        :rtype: bool
        """

        # session — текущая регистрация запущенного приложения.

        return (type(session) is CommandSession and self.identity == session.identity
                and self.session_id == session.session_id and self.hub_epoch == session.hub_epoch)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate all identity and correlation fields."""

        if type(self.identity) is not Identity:
            raise TypeError("identity must be Identity")
        for value in (self.session_id, self.hub_epoch, self.command_id):
            _nonce(value)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Неизменяемое намерение пользователя
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandRequest:
    """Carry an immutable intent after future source authentication and authorization."""

    ref: CommandRef                         # Однозначная цель и идентификатор команды.
    source_event_id: str                    # ID исходного сообщения для устойчивого устранения повторов.
    source_id: str                          # Имя настроенного источника, а не URL провайдера.
    actor_id: str = field(repr=False)       # Проверенный ID пользователя, без display name.
    conversation_id: str = field(repr=False)    # Проверенный ID беседы или чата.
    name: str                               # Пользовательское имя зарегистрированного callback.
    arguments: Mapping[str, str] = field(default_factory=dict, repr=False)      # Проверяемые именованные строки.
    command_display_mode: str | None = None        # Режим исходной регистрации; сохраняется вместе с командой.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка контекста без исполнения и выдачи прав
    #--------------------------------------------------------------------------------------------------------------
    def context(self) -> CommandContext:

        """Build callback metadata without executing or authorizing the callback.

        :return: Application context copied from the admitted command.
        :rtype: CommandContext
        """

        return CommandContext(command_id=self.ref.command_id, actor_id=self.actor_id,
            conversation_id=self.conversation_id, identity=self.ref.identity,
            session_id=self.ref.session_id, arguments=self.arguments)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy argument data using the existing command context limits."""

        if type(self.ref) is not CommandRef:
            raise TypeError("ref must be CommandRef")
        if self.command_display_mode is not None and self.command_display_mode not in ("full", "compact", "text"):
            raise ValueError("invalid command display mode")
        _identifier(self.source_id)
        require_text(self.source_event_id, "source_event_id", 128)
        if not isinstance(self.name, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name) is None:
            raise ValueError("invalid command name")

        # Пределы аргументов едины с уже существующим контекстом callback.
        # Создание контекста ничего не исполняет и не подтверждает права actor.
        context = self.context()
        object.__setattr__(self, "arguments", context.arguments)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Запрос единственной попытки исполнения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandClaim:
    """Ask to start one exact request; retries retain the same claim identifier."""

    ref: CommandRef         # Точная команда в заданной сессии.
    claim_id: str           # Случайный ID единственной попытки исполнения.
    request_digest: str     # Хеш запроса, включая actor, цель и аргументы.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject malformed claims without accepting them as execution authority."""

        if type(self.ref) is not CommandRef:
            raise TypeError("ref must be CommandRef")
        _nonce(self.claim_id)
        _digest(self.request_digest)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Разрешение на начало конкретной попытки
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandGrant:
    """Represent a start authorization after durable hub admission, never by construction alone."""

    request: CommandRequest     # Точный запрос, связанный с выданным разрешением.
    claim_id: str               # ID попытки из CommandClaim; при повторе не меняется.
    remaining_ttl: float        # Остаток общего срока на момент ответа hub, секунды.
    execution_timeout: float    # Предел ожидания callback; не принудительная остановка потока.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка точной связи запроса и ответа
    #--------------------------------------------------------------------------------------------------------------
    def matches(
        self,
        claim: CommandClaim,
    ) -> bool:

        """Check request correlation including its immutable argument digest.

        :param claim: Expected claim for one exact request.
        :type claim: CommandClaim

        :return: True when all required correlation fields agree.
        :rtype: bool
        """

        # claim — запрос клиента на получение разрешения выполнить команду.

        return (type(claim) is CommandClaim and self.request.ref == claim.ref
                and self.claim_id == claim.claim_id and message_digest(self.request) == claim.request_digest)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Keep execution inside both the command lifetime and finite timeout."""

        if type(self.request) is not CommandRequest:
            raise TypeError("request must be CommandRequest")
        _nonce(self.claim_id)
        _seconds(self.remaining_ttl)
        _seconds(self.execution_timeout)
        if self.execution_timeout > self.remaining_ttl:
            raise ValueError("execution timeout exceeds command lifetime")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Завершение обработчика или неизвестный исход
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandResult:
    """Record callback outcome, not an inferred business-process state."""

    ref: CommandRef                         # Команда и целевая сессия.
    claim_id: str | None                    # ID попытки; None допустим только до начала исполнения.
    outcome: CommandOutcome                 # Завершение callback, отказ, истечение или неизвестный исход.
    text: str | None = field(default=None, repr=False)      # Явный ответ приложения, максимум 4096 байт UTF-8.
    reason: CommandReason | None = None     # Фиксированная причина; исключения и traceback не передаются.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate outcome semantics and a bounded application-owned response."""

        if type(self.ref) is not CommandRef or type(self.outcome) is not CommandOutcome:
            raise TypeError("invalid command result type")
        if self.claim_id is not None:
            _nonce(self.claim_id)
        if self.outcome in (CommandOutcome.COMPLETED, CommandOutcome.UNKNOWN) and self.claim_id is None:
            raise ValueError("executed outcome requires a claim")
        if self.text is not None:
            require_text(self.text, "command result text", MAX_COMMAND_TEXT_BYTES, True)
        if self.reason is not None and type(self.reason) is not CommandReason:
            raise TypeError("invalid command reason")

        allowed = {
            CommandOutcome.COMPLETED: (None,),
            CommandOutcome.REJECTED: (CommandReason.DENIED, CommandReason.INVALID_ARGUMENTS),
            CommandOutcome.EXPIRED: (CommandReason.EXPIRED, CommandReason.RESTART),
            CommandOutcome.UNKNOWN: (CommandReason.CALLBACK_ERROR, CommandReason.INVALID_RESULT,
                                    CommandReason.TIMEOUT, CommandReason.RESTART),
        }
        if self.reason not in allowed[self.outcome]:
            raise ValueError("reason does not match command outcome")
        if self.outcome is not CommandOutcome.COMPLETED and self.text is not None:
            raise ValueError("failed command result cannot expose arbitrary text")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Подтверждение сохранения точного результата
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandReceipt:
    """Acknowledge durable storage of an exact result without authorizing another call."""

    ref: CommandRef         # Команда, для которой сохранён результат.
    claim_id: str | None    # ID попытки или None для отказа до выдачи разрешения.
    result_digest: str      # Хеш всего сохранённого результата.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка точной связи запроса и ответа
    #--------------------------------------------------------------------------------------------------------------
    def matches(
        self,
        result: CommandResult,
    ) -> bool:

        """Check acknowledgment of the exact result, not just its command identifier.

        :param result: Correlated callback result.
        :type result: CommandResult

        :return: True when all required correlation fields agree.
        :rtype: bool
        """

        # result — результат выполнения с идентификаторами исходной команды.

        return (type(result) is CommandResult and self.ref == result.ref and self.claim_id == result.claim_id
                and self.result_digest == message_digest(result))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate receipt correlation fields."""

        if type(self.ref) is not CommandRef:
            raise TypeError("ref must be CommandRef")
        if self.claim_id is not None:
            _nonce(self.claim_id)
        _digest(self.result_digest)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


CommandMessage: TypeAlias = (CommandRegistration | CommandSession | CommandRequest | CommandClaim
                            | CommandGrant | CommandResult | CommandReceipt)
_KINDS = {
    "registration": CommandRegistration, "session": CommandSession, "request": CommandRequest,
    "claim": CommandClaim, "grant": CommandGrant, "result": CommandResult, "receipt": CommandReceipt,
}
_NESTED = {
    CommandRegistration: {"identity": Identity}, CommandSession: {"identity": Identity},
    CommandRef: {"identity": Identity}, CommandRequest: {"ref": CommandRef},
    CommandClaim: {"ref": CommandRef}, CommandGrant: {"request": CommandRequest},
    CommandResult: {"ref": CommandRef}, CommandReceipt: {"ref": CommandRef},
}


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка метаданных без вызова обработчиков
#------------------------------------------------------------------------------------------------------------------
def describe_commands(registry: CommandRegistry) -> tuple[CommandCapability, ...]:

    """Export finite command metadata without serializing or invoking callbacks.

    :param registry: Application-owned immutable command registry.
    :type registry: CommandRegistry

    :return: Immutable command metadata without callback references.
    :rtype: tuple[CommandCapability, ...]
    """

    # registry — реестр команд, зарегистрированных приложением.

    if type(registry) is not CommandRegistry or not 1 <= len(registry) <= MAX_CAPABILITIES:
        raise ValueError("invalid command registry size")
    return tuple(CommandCapability(name=spec.name, required_scope=spec.required_scope,
        timeout=spec.timeout, read_only=spec.read_only, idempotent=spec.idempotent,
        accepts_arguments=spec.validate_arguments is not None) for spec in (registry[name] for name in registry))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка результата без пользовательского преобразования в строку
#------------------------------------------------------------------------------------------------------------------
def callback_result(
    ref: CommandRef,
    claim_id: str,
    value: object,
) -> CommandResult:

    """Normalize an already returned value without calling user-defined string conversion.

    :param ref: Exact command and target-session reference.
    :type ref: CommandRef

    :param claim_id: Identifier of the only execution attempt.
    :type claim_id: str

    :param value: Value to validate or normalize.
    :type value: object

    :return: Correlated result with a validated outcome and bounded text.
    :rtype: CommandResult
    """

    # Пользовательский __str__ не вызывается: он может менять состояние или выдавать секреты.
    # Неверный/слишком большой ответ после callback не означает, что действие не произошло.
    if value is None or type(value) is str:
        try:
            return CommandResult(ref=ref, claim_id=claim_id, outcome=CommandOutcome.COMPLETED, text=value)
        except (ValueError, UnicodeError):
            pass
    return CommandResult(ref=ref, claim_id=claim_id, outcome=CommandOutcome.UNKNOWN,
                         reason=CommandReason.INVALID_RESULT)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Преобразование только разрешённых моделей в данные JSON
#------------------------------------------------------------------------------------------------------------------
def _plain(value: object) -> object:

    """Convert only explicitly allowed protocol types to JSON data.

    :param value: Value to validate or normalize.
    :type value: object

    :return: JSON-compatible values with nested protocol objects expanded.
    :rtype: object
    """

    # value — объект, преобразуемый в обычные JSON-совместимые значения.

    if type(value) in (*_KINDS.values(), CommandRef, CommandCapability, Identity):
        # Отсутствующий режим не добавляет null в старый wire: это сохраняет
        # канонические байты и digest уже записанных запросов и квитанций.
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)
                if item.name != "command_display_mode" or getattr(value, item.name) is not None}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Выбор схемы без изменения прежних сообщений и их хешей
#------------------------------------------------------------------------------------------------------------------
def _schema_version(message: CommandMessage) -> int:

    """Select the smallest exact schema needed by the immutable message.

    :param message: Validated command protocol model.
    :type message: CommandMessage

    :return: Schema 2 only for an explicit registration/request presentation mode.
    :rtype: int
    """

    # В grant вложен тот же request; result/receipt остаются в прежней схеме.
    target = message.request if type(message) is CommandGrant else message
    return 2 if (type(target) in (CommandRegistration, CommandRequest)
                 and target.command_display_mode is not None) else 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Кодирование ограниченного сообщения заданной версии
#------------------------------------------------------------------------------------------------------------------
def encode_command(message: CommandMessage) -> bytes:

    """Encode one exact versioned envelope with a fixed UTF-8 byte limit.

    :param message: Validated command protocol message.
    :type message: CommandMessage

    :return: Canonical UTF-8 JSON bytes within the wire size limit.
    :rtype: bytes
    """

    # message — проверяемое сообщение командного протокола.

    try:
        kind = next(name for name, model in _KINDS.items() if type(message) is model)
        body = json.dumps({"schema_version": _schema_version(message), "kind": kind, "payload": _plain(message)},
                          ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(body) > MAX_COMMAND_BYTES:
            raise ValueError("command wire size exceeded")
        return body
    except (StopIteration, ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("invalid command message") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрет повторяющихся ключей JSON
#------------------------------------------------------------------------------------------------------------------
def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:

    """Reject repeated JSON keys at every nesting level.

    :param pairs: JSON object key/value pairs.
    :type pairs: list[tuple[str, object]]

    :return: Decoded object with unique field names.
    :rtype: dict[str, object]
    """

    # pairs — пары ключей и значений одного JSON-объекта.

    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate command JSON key")
        result[key] = value
    return result
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрет нестандартных чисел JSON
#------------------------------------------------------------------------------------------------------------------
def _constant(value: str) -> None:

    """Reject nonstandard JSON numeric constants.

    :param value: Value to validate or normalize.
    :type value: str
    """

    # value — нестандартная числовая константа JSON, которую нужно отклонить.

    raise ValueError("invalid command JSON number")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Разбор точного состава полей по закрытому перечню моделей
#------------------------------------------------------------------------------------------------------------------
def _model(
    model: type,
    value: object,
    schema_version: int = 1,
) -> object:

    """Decode exact field sets using a closed model registry.

    :param model: Allowed model type from the closed registry.
    :type model: type

    :param value: Value to validate or normalize.
    :type value: object

    :param schema_version: Exact envelope version, also applied to nested models.
    :type schema_version: int

    :return: An instance of the requested validated message model.
    :rtype: object
    """

    # model — ожидаемый класс сообщения протокола.
    # value — словарь полей входящего сообщения.

    # Схема 1 не принимает новое поле даже с null. В схеме 2 режим обязателен
    # в registration/request; у вложенного grant он принадлежит только request.
    expected = {item.name for item in fields(model)}
    if schema_version == 1:
        expected.discard("command_display_mode")
    if type(value) is not dict or set(value) != expected:
        raise ValueError("invalid command payload fields")
    if schema_version == 2 and model in (CommandRegistration, CommandRequest):
        if type(value["command_display_mode"]) is not str:
            raise ValueError("invalid command display mode")
    payload = dict(value)
    for name, nested in _NESTED.get(model, {}).items():
        payload[name] = _model(nested, payload[name], schema_version)
    if model is CommandRegistration:
        items = payload["capabilities"]
        if type(items) is not list or not 1 <= len(items) <= MAX_CAPABILITIES:
            raise ValueError("invalid capability count")
        payload["capabilities"] = tuple(_model(CommandCapability, item) for item in items)
    if model is CommandResult:
        payload["outcome"] = CommandOutcome(payload["outcome"])
        if payload["reason"] is not None:
            payload["reason"] = CommandReason(payload["reason"])
    return model(**payload)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение сообщения без раскрытия ошибочного содержимого
#------------------------------------------------------------------------------------------------------------------
def decode_command(data: bytes) -> CommandMessage:

    """Decode a bounded closed schema without exposing malformed input in errors.

    :param data: Bounded UTF-8 JSON bytes.
    :type data: bytes

    :return: Validated command message of the declared wire kind.
    :rtype: CommandMessage
    """

    # data — полученные байты JSON; размер проверяется до разбора.

    try:
        if type(data) is not bytes or len(data) > MAX_COMMAND_BYTES:
            raise ValueError("invalid command wire size")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict or set(value) != {"schema_version", "kind", "payload"}:
            raise ValueError("invalid command envelope")
        if type(value["schema_version"]) is not int or value["schema_version"] not in (1, 2):
            raise ValueError("unsupported command schema")
        if type(value["kind"]) is not str or value["kind"] not in _KINDS:
            raise ValueError("unknown command kind")
        message = _model(_KINDS[value["kind"]], value["payload"], value["schema_version"])
        if _schema_version(message) != value["schema_version"]:
            raise ValueError("noncanonical command schema")
        return message
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise ValueError("invalid command message") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Хеш канонических данных для проверки связи сообщений
#------------------------------------------------------------------------------------------------------------------
def message_digest(message: CommandMessage) -> str:

    """Bind correlation to canonical data; this hash is not an authentication signature.

    :param message: Validated command protocol message.
    :type message: CommandMessage

    :return: SHA-256 hexadecimal digest of the canonical message bytes.
    :rtype: str
    """

    # message — проверяемое сообщение командного протокола.

    return hashlib.sha256(encode_command(message)).hexdigest()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.commands.protocol не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
