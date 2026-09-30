# Переходы состояний команд и относительные сроки без постоянного хранилища.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260930-193650
#
# Состав модуля:
# -> CommandPhase: Состояния исполнения в постоянном журнале.
#
# -> CommandAction: Явные действия над состоянием команды.
#
# -> CommandDeadline: Локальный монотонный срок без сравнения часов VM.
#    Интерфейс:
#    -> from_response(): Вычисление срока с вычетом времени запроса.
#    -> remaining(): Остаток срока с проверкой эпохи и монотонного времени.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandRecord: Проверенная запись будущего журнала исполнения.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> CommandTransition: Предлагаемый переход до атомарной фиксации.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> _finish(): Создание согласованного терминального результата.
# -> recover_command(): Закрытие незавершённых команд после перезапуска.
# -> advance_command(): Проверка перехода без исполнения и изменения хранилища.


#******************************************************************************************************************
# ИМПОРТ И ОПРЕДЕЛЕНИЯ
#******************************************************************************************************************
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from ._validation import require_number
from .command_protocol import (
    MAX_COMMAND_SECONDS,
    CommandClaim,
    CommandGrant,
    CommandOutcome,
    CommandReason,
    CommandRequest,
    CommandResult,
    CommandSession,
    _nonce,
    message_digest,
)


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Состояния исполнения в постоянном журнале
#------------------------------------------------------------------------------------------------------------------
class CommandPhase(str, Enum):
    """Describe durable execution progress independently of message delivery."""

    READY = "ready"
    CLAIMED = "claimed"
    STARTED = "started"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Явные действия над состоянием команды
#------------------------------------------------------------------------------------------------------------------
class CommandAction(str, Enum):
    """Describe explicit state changes, never an implicit execution retry."""

    CLAIM = "claim"
    START = "start"
    FINISH = "finish"
    REJECT = "reject"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Локальный монотонный срок без сравнения часов VM
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandDeadline:
    """Keep a local monotonic deadline; never serialize it or rebuild it after restart."""

    hub_epoch: str          # Запуск hub, которому принадлежит срок.
    sent_at: float          # Монотонный момент начала исходящего запроса.
    received_at: float      # Монотонный момент получения ответа.
    expires_at: float       # Монотонный предел; UTC в этой модели нет.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Вычисление срока с вычетом времени запроса
    #--------------------------------------------------------------------------------------------------------------
    @classmethod
    def from_response(
        cls,
        remaining_ttl: float,
        sent_at: float,
        received_at: float,
        hub_epoch: str,
    ) -> CommandDeadline:

        """Conservatively subtract the complete round trip from a remote remaining budget.

        :param remaining_ttl: Remaining budget reported by the hub, in seconds.
        :type remaining_ttl: float

        :param sent_at: Local monotonic time before issuing the request.
        :type sent_at: float

        :param received_at: Local monotonic time after receiving the complete response.
        :type received_at: float

        :param hub_epoch: Identifier of the current hub incarnation.
        :type hub_epoch: str

        :return: Conservatively subtract the complete round trip from a remote remaining budget.
        :rtype: CommandDeadline
        """

        require_number(remaining_ttl, "remaining_ttl")
        if remaining_ttl > MAX_COMMAND_SECONDS:
            raise ValueError("command lifetime exceeds limit")
        # Hub сообщает остаток при формировании ответа. Вычитание всего времени
        # запроса консервативно: ожидание на сервере может быть учтено дважды,
        # но задержка ответа никогда не превращается в дополнительный срок.
        require_number(sent_at, "sent_at", allow_zero=True)
        return cls(hub_epoch=hub_epoch, sent_at=sent_at, received_at=received_at,
                   expires_at=sent_at + remaining_ttl)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остаток срока с проверкой эпохи и монотонного времени
    #--------------------------------------------------------------------------------------------------------------
    def remaining(
        self,
        now: float,
        hub_epoch: str,
    ) -> float:

        """Return zero for expiry, clock rollback or another hub incarnation.

        :param now: Current local monotonic time.
        :type now: float

        :param hub_epoch: Identifier of the current hub incarnation.
        :type hub_epoch: str

        :return: Return zero for expiry, clock rollback or another hub incarnation.
        :rtype: float
        """

        require_number(now, "now", allow_zero=True)
        if hub_epoch != self.hub_epoch or now < self.received_at:
            return 0.0
        return max(0.0, self.expires_at - now)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate finite monotonic observations without consulting wall clocks."""

        _nonce(self.hub_epoch)
        for value in (self.sent_at, self.received_at, self.expires_at):
            require_number(value, "monotonic time", allow_zero=True)
        if self.received_at < self.sent_at or not 0 < self.expires_at - self.sent_at <= MAX_COMMAND_SECONDS:
            raise ValueError("invalid command deadline")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Проверенная запись будущего журнала исполнения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandRecord:
    """Represent one journal entry; persistence and atomic compare-and-swap belong to storage."""

    request: CommandRequest                 # Неизменяемое намерение с точной целью.
    phase: CommandPhase = CommandPhase.READY    # Текущее состояние записи.
    claim_id: str | None = None             # Единственная попытка; None до резервирования.
    result: CommandResult | None = None     # Сохранённый итог; None до терминального состояния.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject impossible journal states before they can be persisted."""

        if type(self.request) is not CommandRequest or type(self.phase) is not CommandPhase:
            raise TypeError("invalid command record type")
        if self.claim_id is not None:
            _nonce(self.claim_id)
        if self.phase is CommandPhase.READY and self.claim_id is not None:
            raise ValueError("ready command cannot have a claim")
        if self.phase in (CommandPhase.CLAIMED, CommandPhase.STARTED) and self.claim_id is None:
            raise ValueError("active command requires a claim")

        # Терминальная запись всегда содержит согласованный результат. Это
        # исключает неоднозначное восстановление после перезапуска хранилища.
        terminal = self.phase.value in {item.value for item in CommandOutcome}
        if terminal != (self.result is not None):
            raise ValueError("command phase and result disagree")
        if self.result is not None:
            if type(self.result) is not CommandResult:
                raise TypeError("invalid command result type")
            if (self.result.ref != self.request.ref or self.result.claim_id != self.claim_id
                    or self.result.outcome.value != self.phase.value):
                raise ValueError("command result correlation mismatch")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Предлагаемый переход до атомарной фиксации
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandTransition:
    """Describe a proposed durable change, never execute its callback."""

    record: CommandRecord           # Новая запись для атомарной фиксации вместо предыдущей.
    start_callback: bool = False    # Разрешено только после успешной фиксации перехода STARTED.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Keep callback authorization consistent with the proposed journal state."""

        if type(self.record) is not CommandRecord or type(self.start_callback) is not bool:
            raise TypeError("invalid command transition type")
        if self.start_callback and self.record.phase is not CommandPhase.STARTED:
            raise ValueError("callback start requires a started record")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание согласованного терминального результата
#------------------------------------------------------------------------------------------------------------------
def _finish(
    record: CommandRecord,
    outcome: CommandOutcome,
    reason: CommandReason,
) -> CommandRecord:

    """Create a correlated terminal record without arbitrary diagnostic text.

    :param record: Current immutable journal snapshot.
    :type record: CommandRecord

    :param outcome: Terminal callback outcome.
    :type outcome: CommandOutcome

    :param reason: Fixed safe reason for the terminal state.
    :type reason: CommandReason

    :return: Create a correlated terminal record without arbitrary diagnostic text.
    :rtype: CommandRecord
    """

    result = CommandResult(ref=record.request.ref, claim_id=record.claim_id, outcome=outcome, reason=reason)
    return replace(record, phase=CommandPhase(outcome.value), result=result)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Закрытие незавершённых команд после перезапуска
#------------------------------------------------------------------------------------------------------------------
def recover_command(record: CommandRecord) -> CommandRecord:

    """Fence pre-restart work instead of granting it a fresh lifetime or another execution.

    :param record: Current immutable journal snapshot.
    :type record: CommandRecord

    :return: Fence pre-restart work instead of granting it a fresh lifetime or another execution.
    :rtype: CommandRecord
    """

    if type(record) is not CommandRecord:
        raise TypeError("record must be CommandRecord")
    if record.result is not None:
        return record
    if record.phase is CommandPhase.STARTED:
        return _finish(record, CommandOutcome.UNKNOWN, CommandReason.RESTART)
    return _finish(record, CommandOutcome.EXPIRED, CommandReason.RESTART)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка перехода без исполнения и изменения хранилища
#------------------------------------------------------------------------------------------------------------------
def advance_command(
    record: CommandRecord,
    action: CommandAction,
    session: CommandSession,
    claim: CommandClaim,
    command_deadline: CommandDeadline,
    session_deadline: CommandDeadline,
    now: float,
    grant: CommandGrant | None = None,
    result: CommandResult | None = None,
) -> CommandTransition:

    """Propose one fenced transition; a storage transaction must commit it before any side effect.

    :param record: Current immutable journal snapshot.
    :type record: CommandRecord

    :param action: Requested state transition.
    :type action: CommandAction

    :param session: Current authenticated session metadata; liveness is checked separately.
    :type session: CommandSession

    :param claim: Expected claim for one exact request.
    :type claim: CommandClaim

    :param command_deadline: Local deadline derived from the grant round trip.
    :type command_deadline: CommandDeadline

    :param session_deadline: Previously established local registration deadline.
    :type session_deadline: CommandDeadline

    :param now: Current local monotonic time.
    :type now: float

    :param grant: Correlated start authorization.
    :type grant: CommandGrant | None

    :param result: Correlated callback result.
    :type result: CommandResult | None

    :return: Propose one fenced transition; a storage transaction must commit it before any side effect.
    :rtype: CommandTransition
    """

    if type(record) is not CommandRecord or type(action) is not CommandAction:
        raise TypeError("invalid command transition input")
    if type(command_deadline) is not CommandDeadline or type(session_deadline) is not CommandDeadline:
        raise TypeError("invalid command deadline type")
    if not record.request.ref.matches(session):
        raise ValueError("command session mismatch")
    if (type(claim) is not CommandClaim or claim.ref != record.request.ref
            or claim.request_digest != message_digest(record.request)):
        raise ValueError("command claim mismatch")
    if record.claim_id is not None and record.claim_id != claim.claim_id:
        raise ValueError("command already belongs to another claim")
    if grant is not None and (type(grant) is not CommandGrant or not grant.matches(claim)):
        raise ValueError("command grant mismatch")
    if result is not None and (type(result) is not CommandResult or result.ref != record.request.ref
                               or result.claim_id != claim.claim_id):
        raise ValueError("command result mismatch")

    # Повтор уже завершённого запроса не меняет состояние и не запускает callback.
    # Конфликтующий результат не заменяет сохранённый: это ошибка протокола.
    if record.result is not None:
        if result is not None and result != record.result:
            raise ValueError("conflicting command result")
        return CommandTransition(record=record)

    # Доставка результата не запускает действие заново. Поздний достоверный ответ
    # можно сохранить, пока запись ещё STARTED; уже записанный UNKNOWN терминален.
    if action is CommandAction.FINISH:
        if record.phase is not CommandPhase.STARTED or result is None:
            raise ValueError("command has not started")
        if result.outcome not in (CommandOutcome.COMPLETED, CommandOutcome.UNKNOWN):
            raise ValueError("started command cannot become unexecuted")
        return CommandTransition(record=replace(record, phase=CommandPhase(result.outcome.value), result=result))

    remaining = min(command_deadline.remaining(now, session.hub_epoch),
                    session_deadline.remaining(now, session.hub_epoch))
    if remaining <= 0:
        if record.phase is CommandPhase.STARTED:
            expired = _finish(record, CommandOutcome.UNKNOWN, CommandReason.TIMEOUT)
        else:
            expired = _finish(record, CommandOutcome.EXPIRED, CommandReason.EXPIRED)
        return CommandTransition(record=expired)

    if action is CommandAction.CLAIM:
        if record.phase is CommandPhase.READY:
            return CommandTransition(record=replace(record, phase=CommandPhase.CLAIMED, claim_id=claim.claim_id))
        return CommandTransition(record=record)

    if action is CommandAction.REJECT:
        if record.phase is CommandPhase.STARTED:
            raise ValueError("started command cannot be rejected")
        if result is None or result.outcome is not CommandOutcome.REJECTED:
            raise ValueError("rejection requires a rejected result")
        return CommandTransition(record=replace(record, claim_id=claim.claim_id,
                                                phase=CommandPhase.REJECTED, result=result))

    if action is CommandAction.START:
        if grant is None:
            raise ValueError("command start requires a grant")
        if record.phase is CommandPhase.STARTED:
            return CommandTransition(record=record)
        if record.phase is not CommandPhase.CLAIMED:
            raise ValueError("command must be claimed before starting")

        # Проверяем, что caller не подменил локальный срок более длинным. Срок
        # получен из этого ответа и не может превышать присланный execution_timeout.
        if command_deadline.expires_at - command_deadline.sent_at > grant.execution_timeout:
            raise ValueError("command deadline exceeds grant")
        return CommandTransition(record=replace(record, phase=CommandPhase.STARTED), start_callback=True)

    raise ValueError("invalid command transition")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.command_state не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
