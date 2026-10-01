# Контракты ограниченного постоянного хранения команд и результатов.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Классы:
# -> StoreError: Ошибка хранилища без вывода его содержимого.
#
# -> StoreConflict: Конфликт версии или принадлежности команды.
#
# -> StoreFull: Отказ в приёме при заполненном журнале.
#
# -> StoreRole: Назначение журнала hub или приложения.
#
# -> AuditCode: Фиксированное действие для аудита.
#
# -> StoreLimits: Пределы ёмкости и ожидания хранилища.
#    Специальные методы:
#    -> __post_init__(): Проверка типов и согласованности полей.
#
# -> StoredCommand: Сохранённая команда с номером версии.
#
# -> Admission: Подтверждённая запись события источника.
#
# -> CommittedTransition: Переход после успешного сохранения.
#
# -> AuditEntry: Краткие сведения об операции журнала.
#
# -> CommandStore: Контракт ограниченного постоянного журнала.
#    Интерфейс:
#    -> open(): Открытие и восстановление нового поколения журнала.
#    -> close(): Освобождение соединения SQLite.
#    -> cursor(): Чтение сохранённой позиции источника.
#    -> admit(): Атомарная запись решения и позиции источника.
#    -> get(): Поиск команды с проверкой всех полей цели.
#    -> transition(): Фиксация перехода с проверкой версии.
#    -> acknowledge(): Подтверждение точного сохранённого результата.
#    -> release_execution(): Отметка о фактическом окончании ранее неизвестного исполнения.
#    -> pending(): Ограниченная страница неподтверждённых или исполняемых команд.
#    -> audit(): Ограниченная страница аудита.
#    -> prune(): Очистка подтверждённых записей с сохранением защиты от повторов.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from remote_watch._validation import require_int, require_number
from remote_watch.commands.protocol import (
    CommandClaim,
    CommandGrant,
    CommandRef,
    CommandRequest,
    CommandResult,
    CommandSession,
)
from remote_watch.commands.state import CommandAction, CommandDeadline, CommandRecord


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ошибка хранилища без вывода его содержимого
#------------------------------------------------------------------------------------------------------------------
class StoreError(RuntimeError):
    """Report a fixed storage failure without SQL, paths or command payloads."""
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Конфликт версии или принадлежности команды
#------------------------------------------------------------------------------------------------------------------
class StoreConflict(StoreError):
    """Reject stale cursor/revision or conflicting command identity."""
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Отказ в приёме при заполненном журнале
#------------------------------------------------------------------------------------------------------------------
class StoreFull(StoreError):
    """Refuse new work instead of removing active replay protection."""
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Назначение журнала hub или приложения
#------------------------------------------------------------------------------------------------------------------
class StoreRole(str, Enum):
    """Select the generation field owned by this journal."""

    HUB = "hub"
    CLIENT = "client"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Фиксированное действие для аудита
#------------------------------------------------------------------------------------------------------------------
class AuditCode(str, Enum):
    """Limit persisted diagnostics to known non-sensitive operations."""

    ACCEPTED = "accepted"
    IGNORED = "ignored"
    DENIED = "denied"
    STALE = "stale"
    TRANSITION = "transition"
    RECOVERED = "recovered"
    ACKNOWLEDGED = "acknowledged"
    EXECUTION_RELEASED = "execution_released"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Пределы ёмкости и ожидания хранилища
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class StoreLimits:
    """Bound records, streams, audit, disk pages and transaction lock waits."""

    records: int = 1024                 # Предел команд, включая завершённые до очистки.
    streams: int = 64                   # Предел постоянных позиций источников.
    audit: int = 4096                   # Число последних записей аудита; не защита от повторов.
    payload_bytes: int = 33554432       # Байты запросов и резервов для будущих результатов.
    database_bytes: int = 67108864      # Предел основного файла SQLite; журнал отката отдельный.
    retention: float = 86400.0          # Минимальный срок хранения после последнего изменения.
    lock_timeout: float = 1.0           # Предел ожидания каждой блокировки, секунды.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка типов и согласованности полей
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate finite limits and reserve room for bounded command results."""

        for value in (self.records, self.streams, self.audit, self.payload_bytes, self.database_bytes):
            require_int(value, "storage limit")
        require_number(self.retention, "retention")
        require_number(self.lock_timeout, "lock_timeout")
        if (self.records > 100000 or self.streams > 1024 or self.audit > 100000
                or self.payload_bytes < 65536 or self.payload_bytes > 1073741824
                or not 1048576 <= self.database_bytes <= 2147483648
                or not 300 <= self.retention <= 2592000 or self.lock_timeout > 30):
            raise ValueError("storage limits out of range")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Сохранённая команда с номером версии
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class StoredCommand:
    """Expose a journal snapshot with a revision and a stable delivery sequence."""

    record: CommandRecord       # Текущее сохранённое состояние команды.
    revision: int               # Номер версии для сравнения перед изменением.
    sequence: int               # Постоянный возрастающий номер для доставки клиенту.
    acknowledged: bool          # Результат подтверждён владельцем контура.
    execution_active: bool      # Выданное исполнение ещё не подтверждено как завершившееся.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Подтверждённая запись события источника
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Admission:
    """Return a committed source position and optional command record."""

    cursor: int                         # Уже записанная позиция; только её можно подтвердить источнику.
    command: StoredCommand | None       # None для отклонённого, пропущенного или очищенного события.
    inserted: bool                      # Новая команда записана именно этой транзакцией.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Переход после успешного сохранения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommittedTransition:
    """Authorize a side effect only for the transaction which committed STARTED."""

    command: StoredCommand      # Состояние после успешного commit.
    start_callback: bool        # Единственная транзакция, разрешающая следующий шаг.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Краткие сведения об операции журнала
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class AuditEntry:
    """Expose bounded audit metadata without actors, arguments or response text."""

    sequence: int               # Возрастающий номер записи аудита.
    code: AuditCode             # Фиксированное действие или отказ.
    command_id: str | None      # ID команды; None для события без принятой команды.
    phase: str | None           # Состояние после записи, если команда существует.
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Контракт ограниченного постоянного журнала
#------------------------------------------------------------------------------------------------------------------
class CommandStore(Protocol):
    """Define synchronous bounded storage; use outside logging/event-loop threads."""

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие и восстановление нового поколения журнала
    #--------------------------------------------------------------------------------------------------------------
    def open(self) -> None:

        """Open storage and recover a new owner generation before serving requests."""

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Освобождение соединения SQLite
    #--------------------------------------------------------------------------------------------------------------
    def close(self) -> None:

        """Release the owned database connection."""

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение сохранённой позиции источника
    #--------------------------------------------------------------------------------------------------------------
    def cursor(
        self,
        stream: str,
    ) -> int:

        """Read the last committed source position, or -1 before the first event.

        :param stream: Stable authenticated ordered-stream identifier.
        :type stream: str

        :return: Committed source position, or -1 for an unseen stream.
        :rtype: int
        """

        # stream — постоянное имя упорядоченного источника.

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Атомарная запись решения и позиции источника
    #--------------------------------------------------------------------------------------------------------------
    def admit(
        self,
        stream: str,
        position: int,
        expected_cursor: int,
        request: CommandRequest | None,
        deadline: CommandDeadline | None = None,
        rejection: AuditCode = AuditCode.IGNORED,
    ) -> Admission:

        """Atomically record a source decision and advance its ordered cursor.

        :param stream: Stable authenticated ordered-stream identifier.
        :type stream: str

        :param position: Monotonically increasing delivery position, stable on retry.
        :type position: int

        :param expected_cursor: Previously observed committed cursor for comparison.
        :type expected_cursor: int

        :param request: Validated command intent, or None for a rejected source event.
        :type request: CommandRequest | None

        :param deadline: Original remaining lifetime on this process monotonic clock.
        :type deadline: CommandDeadline | None

        :param rejection: Fixed audit code for a source decision without a command.
        :type rejection: AuditCode

        :return: Committed source cursor and the command, if one was admitted.
        :rtype: Admission
        """

        # stream — постоянное имя упорядоченного источника.
        # position — возрастающая позиция, неизменная при повторе.
        # expected_cursor — ожидаемая предыдущая позиция источника.
        # request — команда либо отсутствие принятой команды.
        # deadline — исходный оставшийся срок на локальных часах.
        # rejection — фиксированная причина пропуска или отказа.

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Поиск команды с проверкой всех полей цели
    #--------------------------------------------------------------------------------------------------------------
    def get(
        self,
        ref: CommandRef,
    ) -> StoredCommand | None:

        """Look up an exact command reference.

        :param ref: Exact command, identity, session and hub reference.
        :type ref: CommandRef

        :return: Stored command, or None when the identifier is absent.
        :rtype: StoredCommand | None
        """

        # ref — полная ссылка на команду и её цель.

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Фиксация перехода с проверкой версии
    #--------------------------------------------------------------------------------------------------------------
    def transition(
        self,
        ref: CommandRef,
        revision: int,
        action: CommandAction,
        session: CommandSession,
        claim: CommandClaim,
        session_deadline: CommandDeadline,
        grant: CommandGrant | None = None,
        result: CommandResult | None = None,
        grant_deadline: CommandDeadline | None = None,
    ) -> CommittedTransition:

        """Compare revision and commit a legal transition before authorizing a side effect.

        :param ref: Exact command, identity, session and hub reference.
        :type ref: CommandRef

        :param revision: Expected persisted command revision.
        :type revision: int

        :param action: Requested state-machine action.
        :type action: CommandAction

        :param session: Authenticated application session metadata.
        :type session: CommandSession

        :param claim: Claim bound to the exact command payload.
        :type claim: CommandClaim

        :param session_deadline: Independent local registration deadline.
        :type session_deadline: CommandDeadline

        :param grant: Correlated authorization for the only execution attempt.
        :type grant: CommandGrant | None

        :param result: Exact terminal callback result.
        :type result: CommandResult | None

        :param grant_deadline: Local grant deadline after subtracting the entire round trip.
        :type grant_deadline: CommandDeadline | None

        :return: Committed revision and permission for the next execution step.
        :rtype: CommittedTransition
        """

        # ref — полная ссылка на команду и её цель.
        # revision — ожидаемый номер версии сохранённой команды.
        # action — запрошенный переход состояния.
        # session — проверенные сведения о сессии приложения.
        # claim — попытка, связанная с полным запросом.
        # session_deadline — отдельный срок действия регистрации.
        # grant — разрешение на единственную попытку.
        # result — точный итог обработчика.
        # grant_deadline — срок разрешения после вычета времени запроса.

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение точного сохранённого результата
    #--------------------------------------------------------------------------------------------------------------
    def acknowledge(
        self,
        result: CommandResult,
    ) -> None:

        """Mark the exact durable result acknowledged; never execute the command.

        :param result: Exact terminal callback result.
        :type result: CommandResult
        """

        # result — точный итог обработчика.

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Отметка о фактическом окончании ранее неизвестного исполнения
    #--------------------------------------------------------------------------------------------------------------
    def release_execution(
        self,
        ref: CommandRef,
        claim_id: str,
    ) -> None:

        """Record externally verified callback termination without changing its UNKNOWN outcome.

        :param ref: Exact command, identity, session and hub reference.
        :type ref: CommandRef

        :param claim_id: Identifier of the original execution attempt.
        :type claim_id: str
        """

        # ref — полная ссылка на команду и её цель.
        # claim_id — ID исходной попытки исполнения.

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ограниченная страница неподтверждённых или исполняемых команд
    #--------------------------------------------------------------------------------------------------------------
    def pending(
        self,
        after: int = 0,
        limit: int = 100,
    ) -> tuple[StoredCommand, ...]:

        """Read a bounded page of records still awaiting acknowledgment.

        :param after: Exclusive sequence lower bound for pagination.
        :type after: int

        :param limit: Maximum records in one bounded page or cleanup batch.
        :type limit: int

        :return: Unacknowledged or still-running commands in delivery order.
        :rtype: tuple[StoredCommand, ...]
        """

        # after — последний уже прочитанный номер.
        # limit — предел одной страницы или порции очистки.

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ограниченная страница аудита
    #--------------------------------------------------------------------------------------------------------------
    def audit(
        self,
        after: int = 0,
        limit: int = 100,
    ) -> tuple[AuditEntry, ...]:

        """Read a bounded page of safe audit metadata.

        :param after: Exclusive sequence lower bound for pagination.
        :type after: int

        :param limit: Maximum records in one bounded page or cleanup batch.
        :type limit: int

        :return: Retained audit entries in insertion order.
        :rtype: tuple[AuditEntry, ...]
        """

        # after — последний уже прочитанный номер.
        # limit — предел одной страницы или порции очистки.

        ...
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Очистка подтверждённых записей с сохранением защиты от повторов
    #--------------------------------------------------------------------------------------------------------------
    def prune(
        self,
        limit: int = 100,
    ) -> int:

        """Remove eligible acknowledged terminal records without deleting source cursors.

        :param limit: Maximum records in one bounded page or cleanup batch.
        :type limit: int

        :return: Number of acknowledged terminal records removed after retention.
        :rtype: int
        """

        # limit — предел одной страницы или порции очистки.

        ...
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.commands.storage не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
