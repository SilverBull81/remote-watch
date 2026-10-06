# Контракт обмена командами без зависимости от сетевой библиотеки.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-163320
#
# Классы:
# -> CommandError: Ошибка с безопасным фиксированным кодом.
#    Конструктор:
#    -> __init__(): Создание объекта.
#
# -> CommandOffer: Команда из журнала с оставшимся сроком действия.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и ограничений.
#
# -> CommandTransport: Контракт ограниченного обмена с hub.
#    Интерфейс:
#    -> open(): Открытие принадлежащих объекту ресурсов.
#    -> exchange(): Один обмен с проверкой ответа и без скрытого retry.
#    -> close(): Остановка фоновой работы и закрытие ресурсов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from remote_watch._tls import tls_reason
from remote_watch._validation import require_int, require_number
from remote_watch.commands.protocol import CommandMessage, CommandRequest


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ошибка с безопасным фиксированным кодом
#------------------------------------------------------------------------------------------------------------------
class CommandError(RuntimeError):
    """Expose fixed command failure codes without credentials or exception payloads."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        code: str,
        *,
        http_status: int | None = None,
        error_kind: str | None = None,
        verify_code: int | None = None,
    ) -> None:

        """Retain only the supported public failure classification.

        :param code: Fixed public failure classification.
        :type code: str

        :param http_status: Optional HTTP response status, without server text or headers.
        :type http_status: int | None

        :param error_kind: Allowlisted local TLS category; not server-provided text.
        :type error_kind: str | None

        :param verify_code: Bounded OpenSSL certificate verification number.
        :type verify_code: int | None
        """

        # code — фиксированный код ошибки без приватных подробностей.
        # http_status — только проверенное число, без тела ответа gateway.

        known = {"denied", "unavailable", "busy", "stale_session", "conflict", "invalid",
                 "capacity", "expired", "already_started", "outcome_conflict", "closed"}
        self.code = code if code in known else "unavailable"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        # Для TLS оставляем прежний code, но сохраняем причину отдельно и в str(error).
        # Неизвестные строки, bool и произвольные объекты не проходят эту границу.
        self.error_kind = (error_kind if type(error_kind) is str
                           and error_kind in {"tls_certificate", "tls_handshake"} else None)
        self.verify_code = (verify_code if self.error_kind == "tls_certificate"
                            and type(verify_code) is int and 0 <= verify_code <= 999999 else None)
        self.tls_reason = (tls_reason(self.verify_code) if self.error_kind == "tls_certificate"
                           else "handshake_failed" if self.error_kind else None)
        message = self.code
        if self.error_kind:
            message += (f"; error_kind={self.error_kind}; verify_code={self.verify_code}"
                        f"; tls_reason={self.tls_reason}")
        super().__init__(message)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Команда из журнала с оставшимся сроком действия
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandOffer:
    """Deliver one journal position with a nonrenewable remaining command budget."""

    sequence: int               # Постоянная позиция команды в журнале hub.
    request: CommandRequest     # Команда с точной сессией приложения.
    remaining_ttl: float        # Остаток исходного срока, секунды.


    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate the bounded delivery offer before journal admission."""

        require_int(self.sequence, "command sequence")

        if self.sequence > 2**63 - 1 or type(self.request) is not CommandRequest:
            raise ValueError("invalid command offer")
        require_number(self.remaining_ttl, "command lifetime")

        if self.remaining_ttl > 300:
            raise ValueError("invalid command lifetime")
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Контракт ограниченного обмена с hub
#------------------------------------------------------------------------------------------------------------------
class CommandTransport(Protocol):
    """Keep client lifecycle and bounded exchanges independent of HTTP implementations."""


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Open local transport resources without registering an application."""

        ...
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Один обмен с проверкой ответа и без скрытого retry
    #--------------------------------------------------------------------------------------------------------------
    async def exchange(
        self,
        operation: str,
        message: CommandMessage,
    ) -> CommandMessage | CommandOffer | None:

        """Perform one authenticated exchange without a hidden retry queue.

        :param operation: Single operation executed within the documented limits.
        :type operation: str

        :param message: Typed request for the selected command operation.
        :type message: CommandMessage

        :return: Validated response, or None when polling found no work.
        :rtype: CommandMessage | CommandOffer | None
        """

        # operation — одна операция в пределах доступной ёмкости.
        # message — типизированное сообщение выбранной командной операции.

        ...
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Release bounded transport resources."""

        ...
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль transport не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
