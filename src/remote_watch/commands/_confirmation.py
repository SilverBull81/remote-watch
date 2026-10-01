# Одноразовое подтверждение изменяющего намерения без доверия к UTC машин.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Константы:
# -> MAX_CONFIRMATION_SECONDS: Максимальный срок одноразового подтверждения, секунды.
#
# Классы:
# -> CommandChallenge: Одноразовое подтверждение точного намерения.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> ConfirmationDecision: Решение о подтверждении до записи в журнал.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и согласованности объекта.
#
# -> confirm_command(): Проверка свежего подтверждения без сравнения UTC.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import hmac
from dataclasses import dataclass, field, replace

from remote_watch.commands.protocol import CommandRequest, _nonce, message_digest
from remote_watch.commands.state import CommandDeadline

MAX_CONFIRMATION_SECONDS = 30


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Одноразовое подтверждение точного намерения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandChallenge:
    """Bind a one-use confirmation to one intent, actor, conversation and hub incarnation."""

    request: CommandRequest             # Точное намерение, которое увидит пользователь при подтверждении.
    nonce: str = field(repr=False)      # Случайный одноразовый код; генерирует hub после получения намерения.
    deadline: CommandDeadline           # Локальный срок hub; UTC отправителя не используется.
    consumed: bool = False              # Признак уже использованного или окончательно закрытого подтверждения.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate a short hub-local confirmation window."""

        if type(self.request) is not CommandRequest or type(self.deadline) is not CommandDeadline:
            raise TypeError("invalid confirmation type")
        _nonce(self.nonce)
        if type(self.consumed) is not bool:
            raise TypeError("consumed must be bool")
        if (self.deadline.hub_epoch != self.request.ref.hub_epoch
                or self.deadline.expires_at - self.deadline.sent_at > MAX_CONFIRMATION_SECONDS):
            raise ValueError("invalid confirmation deadline")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Решение о подтверждении до записи в журнал
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationDecision:
    """Propose consumption of a challenge; storage must commit it before command admission."""

    challenge: CommandChallenge     # Состояние подтверждения для атомарной записи.
    accepted: bool                  # Разрешить приём только после успешной фиксации consumed.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и согласованности объекта
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Keep an accepted confirmation irreversibly consumed."""

        if type(self.challenge) is not CommandChallenge or type(self.accepted) is not bool:
            raise TypeError("invalid confirmation decision")
        if self.accepted and not self.challenge.consumed:
            raise ValueError("accepted confirmation must be consumed")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка свежего подтверждения без сравнения UTC
#------------------------------------------------------------------------------------------------------------------
def confirm_command(
    challenge: CommandChallenge,
    request: CommandRequest,
    nonce: str,
    source_id: str,
    actor_id: str,
    conversation_id: str,
    hub_epoch: str,
    now: float,
) -> ConfirmationDecision:

    """Check freshness without comparing provider, client or hub wall clocks.

    :param challenge: Current immutable confirmation snapshot.
    :type challenge: CommandChallenge

    :param request: Exact immutable command intent.
    :type request: CommandRequest

    :param nonce: One-use confirmation code.
    :type nonce: str

    :param source_id: Trusted source identifier.
    :type source_id: str

    :param actor_id: Authenticated user identifier from the source.
    :type actor_id: str

    :param conversation_id: Authenticated conversation identifier from the source.
    :type conversation_id: str

    :param hub_epoch: Identifier of the current hub incarnation.
    :type hub_epoch: str

    :param now: Current local monotonic time.
    :type now: float

    :return: Confirmation decision and whether the challenge was consumed.
    :rtype: ConfirmationDecision
    """

    # challenge — ранее выданное одноразовое предложение подтвердить команду.
    # request — исходная команда приложения.
    # nonce — одноразовое значение из подтверждения пользователя.
    # source_id — идентификатор источника входящих сообщений.
    # actor_id — идентификатор отправителя сообщения у провайдера.
    # conversation_id — идентификатор чата у провайдера.
    # hub_epoch — идентификатор текущего запуска hub.
    # now — текущее показание монотонных часов, секунды.

    if type(challenge) is not CommandChallenge or type(request) is not CommandRequest:
        raise TypeError("invalid command confirmation input")

    # Повтор и смена эпохи никогда не открывают новое окно. Закрытие просроченной
    # записи тоже должно попасть в постоянный журнал на следующем этапе реализации.
    if challenge.consumed or challenge.deadline.remaining(now, hub_epoch) <= 0:
        return ConfirmationDecision(challenge=replace(challenge, consumed=True), accepted=False)

    # Метаданные actor/chat должны поступать из аутентифицированного source,
    # а не из текста сообщения. Сам helper не реализует Telegram auth или ACL.
    matches = (type(nonce) is str and len(nonce) == 32 and nonce.isascii()
               and hmac.compare_digest(challenge.nonce, nonce)
               and source_id == challenge.request.source_id and actor_id == challenge.request.actor_id
               and conversation_id == challenge.request.conversation_id
               and message_digest(request) == message_digest(challenge.request))
    if not matches:
        return ConfirmationDecision(challenge=challenge, accepted=False)
    return ConfirmationDecision(challenge=replace(challenge, consumed=True), accepted=True)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.commands._confirmation не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
