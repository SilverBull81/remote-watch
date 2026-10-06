# События источников, явные адреса приложений и текстовые ответы на команды.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-102445
#
# Классы:
# -> SourceEvent: Проверенные данные одного события провайдера.
#    Специальные методы:
#    -> __post_init__(): Проверка и фиксация настроек.
#
# -> SourcePending: Решение с уже выбранной сессией приложения.
#    Специальные методы:
#    -> __post_init__(): Проверка и фиксация настроек.
#
# -> SourceTargets: Явные короткие адреса приложений.
#    Специальные методы:
#    -> __post_init__(): Проверка и фиксация настроек.
#
# -> CommandProvider: Контракт чтения событий и отправки ответов.
#    Интерфейс:
#    -> open(): Открытие ресурсов и проверка владения.
#    -> poll(): Получение ограниченной порции неподтверждённых событий.
#    -> acknowledge(): Подтверждение только записанного события.
#    -> reply(): Одна попытка отправить ответ проверенному получателю.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.
#
# Функции:
# -> parse_source_command(): Разбор адреса, имени команды и строковых аргументов.
# -> source_result_text(): Ответ с командой, сессией, адресом приложения и исходом.
# -> bound_source_text(): Ограничение ответа по размеру с видимой отметкой усечения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Protocol

from remote_watch._validation import require_int, require_text
from remote_watch.commands.protocol import CommandRequest, CommandResult
from remote_watch.events import Identity


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Проверенные данные одного события провайдера
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class SourceEvent:
    """Retain only provider-authenticated routing fields and bounded text."""

    event_id: str                                       # Непрозрачный ID события провайдера.
    message_date: int                                   # Время сообщения по данным провайдера, Unix seconds.
    actor_id: str | None = field(repr=False)            # Удостоверенный автор; None для пропуска.
    conversation_id: str | None = field(repr=False)     # Проверенный чат или закрытый топик.
    text: str | None = field(repr=False)                # Текст команды; None для неподдерживаемого события.


    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка и фиксация настроек
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject oversized provider data before retaining or journaling an event."""

        require_text(self.event_id, "source event", 128)
        require_int(self.message_date, "message date", minimum=0)

        for name in ("actor_id", "conversation_id"):
            value = getattr(self, name)
            if value is not None:
                require_text(value, name, 128)

        if self.text is not None:
            require_text(self.text, "command text", 65536, allow_empty=True)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Решение с уже выбранной сессией приложения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class SourcePending:
    """Persist a fully bound decision before communicating it to the hub."""

    position: int                   # Собственная возрастающая позиция источника.
    event: SourceEvent              # Исходное событие с ID и временем провайдера.
    request: CommandRequest | None  # Уже выбранная сессия; None для отказа или справки.
    notice: str | None = None       # Безопасный ответ без выполнения callback.


    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка и фиксация настроек
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate a persisted decision and its exact relationship to the source event."""

        require_int(self.position, "source position", minimum=0)

        if type(self.event) is not SourceEvent:
            raise TypeError("invalid pending event")

        if self.notice is not None:
            require_text(self.notice, "source notice", 8192)

        if self.request is not None:
            if type(self.request) is not CommandRequest or (
                self.request.source_event_id != self.event.event_id
                or self.request.actor_id != self.event.actor_id
                or self.request.conversation_id != self.event.conversation_id
            ):
                raise ValueError("pending source mismatch")
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Явные короткие адреса приложений
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class SourceTargets:
    """Bind stable human-readable aliases to complete immutable application identities."""

    aliases: Mapping[str, Identity]     # Адреса в команде, каждый указывает ровно на одну Identity.


    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка и фиксация настроек
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy bounded aliases without allowing wildcard or inferred identities."""

        if not isinstance(self.aliases, Mapping) or not 1 <= len(self.aliases) <= 64:
            raise ValueError("invalid source targets")

        for alias, identity in self.aliases.items():
            if type(alias) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", alias) is None:
                raise ValueError("invalid target alias")
            if type(identity) is not Identity:
                raise TypeError("invalid target identity")

        if len(set(self.aliases.values())) != len(self.aliases):
            raise ValueError("ambiguous target aliases")
        object.__setattr__(self, "aliases", MappingProxyType(dict(self.aliases)))
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Контракт чтения событий и отправки ответов
#------------------------------------------------------------------------------------------------------------------
class CommandProvider(Protocol):
    """Read authenticated provider events without knowing the application callbacks."""


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов и проверка владения
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> str:

        """Open owned resources and return a stable nonsecret provider fingerprint.

        :return: Stable nonsecret fingerprint of the opened provider stream.
        :rtype: str
        """

        ...
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Получение ограниченной порции неподтверждённых событий
    #--------------------------------------------------------------------------------------------------------------
    async def poll(
        self,
        cursor: str,
    ) -> tuple[SourceEvent, ...]:

        """Read at most 32 events after the durable opaque provider cursor.

        :param cursor: Opaque identifier of the last durably processed provider event.
        :type cursor: str

        :return: Bounded batch of authenticated provider events.
        :rtype: tuple[SourceEvent, ...]
        """

        # cursor — непрозрачный ID последнего сохранённого события.

        ...
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение только записанного события
    #--------------------------------------------------------------------------------------------------------------
    async def acknowledge(
        self,
        cursor: str,
    ) -> None:

        """Confirm only a provider event whose decision is already durably committed.

        :param cursor: Opaque identifier of the last durably processed provider event.
        :type cursor: str
        """

        # cursor — непрозрачный ID последнего сохранённого события.

        ...
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправить ответ проверенному получателю
    #--------------------------------------------------------------------------------------------------------------
    async def reply(
        self,
        conversation_id: str,
        text: str,
    ) -> None:

        """Make one bounded plain-text reply attempt without formatting private errors.

        :param conversation_id: Authenticated chat ID or configured private command topic.
        :type conversation_id: str

        :param text: Bounded plain text of a command or a provider reply.
        :type text: str
        """

        # conversation_id — проверенный ID чата либо закрытый топик команд.
        # text — ограниченный обычный текст команды либо ответа.

        ...
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close resources and cooperate with cancellation."""

        ...
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Разбор адреса, имени команды и строковых аргументов
#------------------------------------------------------------------------------------------------------------------
def parse_source_command(
    text: str,
    *,
    short_commands: bool = False,
) -> tuple[str | None, str | None, dict[str, str]]:

    """Parse /rw [alias [command [key=value ...]]] without evaluation or positional callback arguments.

    :param text: Bounded plain text of a command or a provider reply.
    :type text: str

    :param short_commands: Explicit provider capability permitting implicit-target slash commands and Help.
    :type short_commands: bool

    :return: Optional target and command names followed by textual named arguments.
    :rtype: tuple[str | None, str | None, dict[str, str]]
    """

    # text — ограниченный обычный текст команды либо ответа.
    # short_commands — явное разрешение синтаксиса Telegram; выбор адреса выполняет source runner.

    require_text(text, "command text", 8192)

    try:
        words = shlex.split(text, comments=False, posix=True)
    except ValueError:
        raise ValueError("invalid command syntax") from None

    if len(words) == 1 and words[0].casefold() in {"help", "/help"}:
        return None, None, {}
    if words and words[0].casefold() in {"help", "/help"}:
        raise ValueError("help does not accept arguments")
    short = bool(short_commands and words and words[0].startswith("/") and words[0] != "/rw")
    if short:
        # Не удаляем @bot: чужой адресат должен быть отклонён обычной проверкой имени.
        words = ["/rw", "implicit", words[0][1:], *words[1:]]
    if not words or words[0] != "/rw" or len(words) > 35:
        raise ValueError("invalid command syntax")
    alias = words[1] if len(words) > 1 else None
    name = words[2] if len(words) > 2 else None

    if alias is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", alias) is None:
        raise ValueError("invalid target alias")

    if name is not None and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is None:
        raise ValueError("invalid command name")
    arguments: dict[str, str] = {}

    for word in words[3:]:
        key, sep, value = word.partition("=")
        if not sep or key in arguments or not key:
            raise ValueError("invalid named argument")
        arguments[key] = value
    return None if short else alias, name, arguments
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ответ с командой, сессией, адресом приложения и исходом
#------------------------------------------------------------------------------------------------------------------
def source_result_text(
    request: CommandRequest,
    result: CommandResult,
    *,
    mode: str = "full",
) -> str:

    """Render bounded correlation and the retained outcome without claiming business completion.

    :param request: Incoming request validated before dispatch.
    :type request: CommandRequest

    :param result: Correlated terminal result whose exact contents must be retained.
    :type result: CommandResult

    :param mode: Source default, overridden by the original request's client-selected mode.
    :type mode: str

    :return: Bounded correlated command result suitable for either provider.
    :rtype: str
    """

    # request — входящий запрос, проверяемый перед обработкой.
    # result — точный итог выполнения с идентификаторами исходной команды.
    # mode — только отображение, без изменения result/digest и его квитанции.

    identity = request.ref.identity
    mode = request.command_display_mode or mode
    if mode not in ("full", "compact", "text"):
        raise ValueError("invalid command display mode")
    reason = "" if result.reason is None else "/" + result.reason.value
    header = (f"{identity.service}/{identity.environment}/{identity.region}/"
              f"{identity.host}/{identity.instance_id}\n"
              f"command={request.name} id={request.ref.command_id}\n"
              f"session={request.ref.session_id}\nresult={result.outcome.value}{reason}")
    fallback = "Handler completed." if result.outcome.value == "completed" else ""
    body = result.text if result.text else fallback
    if mode == "compact":
        source = json.dumps([identity.service, identity.environment, identity.region, identity.host,
                             identity.instance_id], ensure_ascii=False, separators=(",", ":"))
        header = f"{source}\n/{request.name}: {result.outcome.value}{reason}"
    elif mode == "text":
        # Ошибка/UNKNOWN не превращается в пустое либо внешне успешное сообщение.
        # При успехе текст приложения остаётся как есть, без машинных ID и шапки.
        failure = {"unknown": "Command outcome is unknown", "rejected": "Command rejected",
                   "expired": "Command expired"}.get(result.outcome.value)
        header = "" if failure is None else f"{failure} ({result.reason.value})."
    # Один ответ вместо нескольких частей: усечение видно, корреляция остаётся в начале.
    value = header + ("\n" if header and body else "") + body
    return bound_source_text(value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ограничение ответа по размеру с видимой отметкой усечения
#------------------------------------------------------------------------------------------------------------------
def bound_source_text(text: str) -> str:

    """Fit a complete plain-text reply into both initial providers' message limits.

    :param text: Bounded plain text of a command or a provider reply.
    :type text: str

    :return: Plain text within the provider limit, with a visible truncation marker if needed.
    :rtype: str
    """

    # text — ограниченный обычный текст команды либо ответа.

    raw = text.encode("utf-8")
    return text if len(raw) <= 3800 else raw[:3760].decode("utf-8", errors="ignore") + "\n[response truncated]"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль source_protocol не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
