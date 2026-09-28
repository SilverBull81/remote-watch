# Локальная регистрация пользовательских команд без удалённого приёма и исполнения.
# Классы:
# -> CommandContext: Неизменяемый контекст будущего проверенного запроса.
#    -> __post_init__(): Проверка и копирование аргументов.
# -> CommandSpec: Callback и явная политика команды.
#    -> __post_init__(): Проверка имени, сигнатуры и политики.
# -> CommandRegistry: Неизменяемый именованный набор спецификаций.
#    -> __post_init__(): Защитная копия и проверка дубликатов.
#    -> from_callbacks(): Регистрация словаря функций без их исполнения.
#    -> __getitem__(), __iter__(), __len__(): Read-only mapping API.
# Типы:
# -> CommandCallback, ArgumentValidator: Контракты локальных функций.
#
# Version 1.0.0
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
# Дата и время последнего изменения: 260928-110519

#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeAlias

from ._validation import require_callback, require_number, require_text
from .events import Identity

#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************
CommandCallback: TypeAlias = Callable[..., object]
ArgumentValidator: TypeAlias = Callable[[Mapping[str, str]], None]

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Контекст вызова команды в приложении
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandContext:
    """Carry bounded request metadata after future dispatcher authorization.

    command_id, actor_id and conversation_id identify a request and its sender.
    identity/session_id bind the exact application session. arguments is a copied
    read-only mapping of named textual inputs; the command's validator checks their
    semantics. This data class itself does not authenticate or authorize a caller.
    """

    command_id: str
    actor_id: str
    conversation_id: str
    identity: Identity
    session_id: str
    arguments: Mapping[str, str] = field(default_factory=dict, repr=False)

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка и фиксация контекста
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy bounded textual arguments and validate explicit identities."""

        if not isinstance(self.identity, Identity):
            raise TypeError("identity must be Identity")
        for name in ("command_id", "actor_id", "conversation_id", "session_id"):
            require_text(getattr(self, name), name)
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        if len(self.arguments) > 32:
            raise ValueError("arguments has too many entries")
        copied = dict(self.arguments)
        for key, value in copied.items():
            require_text(key, "argument name", 64)
            require_text(value, "argument value", 1024, True)
        size = sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in copied.items())
        if size > 4096:
            raise ValueError("arguments exceeds its UTF-8 byte budget")
        object.__setattr__(self, "arguments", MappingProxyType(copied))
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Явная спецификация команды
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandSpec:
    """Register a local handler and metadata without executing or exposing it.

    name is a lowercase identifier. callback is sync or async, including partials.
    takes_context chooses callback() versus callback(context). description is local
    help text. required_scope defaults to command:<name>, never to public access.
    timeout is a future execution deadline in seconds, not forced thread termination.
    read_only and idempotent are explicit claims, both conservatively false.
    validate_arguments is a synchronous validator for context-mode textual arguments;
    it returns None or raises ValueError. Without it, remote arguments must be empty.
    Registration never invokes callback/validator and never grants command access.
    """

    name: str
    callback: CommandCallback = field(repr=False)
    takes_context: bool = False
    description: str = ""
    required_scope: str | None = None
    timeout: float = 10.0
    read_only: bool = False
    idempotent: bool = False
    validate_arguments: ArgumentValidator | None = field(default=None, repr=False)

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Валидация регистрационной спецификации
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate name, policy and callable signatures without executing handlers."""

        require_text(self.name, "command name", 64)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name) is None:
            raise ValueError("command name must match [a-z][a-z0-9_]{0,63}")
        for name in ("takes_context", "read_only", "idempotent"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")
        require_text(self.description, "description", 2048, True)
        require_number(self.timeout, "timeout")
        if self.required_scope is None:
            object.__setattr__(self, "required_scope", f"command:{self.name}")
        require_text(self.required_scope, "required_scope")
        require_callback(self.callback, int(self.takes_context), "callback")
        if self.validate_arguments is not None:
            if not self.takes_context:
                raise ValueError("validate_arguments requires takes_context=True")
            require_callback(self.validate_arguments, 1, "validate_arguments", allow_async=False)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Неизменяемый реестр команд без dispatcher
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandRegistry(Mapping[str, CommandSpec]):
    """Provide read-only name lookup for an application-owned command catalogue.

    specs is copied to a tuple. Duplicate names are rejected. Callback references
    remain local and may capture mutable application state; they are not serialized.
    This registry has no remote transport, authorization or dispatch method.
    """

    specs: tuple[CommandSpec, ...] = field(default=(), repr=False)
    _by_name: Mapping[str, CommandSpec] = field(init=False, repr=False, compare=False)

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка уникальности и фиксация реестра
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy the finite command list and reject duplicate names."""

        if not isinstance(self.specs, (list, tuple)):
            raise TypeError("specs must be a list or tuple")
        copied = tuple(self.specs)
        by_name = {}
        for spec in copied:
            if not isinstance(spec, CommandSpec):
                raise TypeError("specs must contain CommandSpec values")
            if spec.name in by_name:
                raise ValueError("duplicate command name")
            by_name[spec.name] = spec
        object.__setattr__(self, "specs", copied)
        object.__setattr__(self, "_by_name", MappingProxyType(by_name))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Адаптация словаря callback-функций
    #--------------------------------------------------------------------------------------------------------------
    @classmethod
    def from_callbacks(
        cls,
        callbacks: Mapping[str, CommandCallback],
        ) -> CommandRegistry:

        """Register zero-argument functions or partials with conservative defaults.

        :param callbacks: Mapping from command names to local callable handlers.
        :type callbacks: Mapping[str, CommandCallback]

        :return: Immutable catalogue detached from the input mapping.
        :rtype: CommandRegistry
        """

        # callbacks - пользовательский словарь имён и обработчиков.

        if not isinstance(callbacks, Mapping):
            raise TypeError("callbacks must be a mapping")
        return cls(specs=tuple(CommandSpec(name=name, callback=callback) for name, callback in callbacks.items()))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Поиск зарегистрированной команды
    #--------------------------------------------------------------------------------------------------------------
    def __getitem__(
        self,
        name: str,
        ) -> CommandSpec:

        """Look up a command specification without invoking it.

        :param name: Exact registered command name.
        :type name: str

        :return: Registered specification, or raise KeyError for an unknown name.
        :rtype: CommandSpec
        """

        # name - точное имя команды.

        return self._by_name[name]
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Перечисление имён команд
    #--------------------------------------------------------------------------------------------------------------
    def __iter__(self) -> Iterator[str]:

        """Return command names in registration order.

        :return: Iterator over registered names.
        :rtype: Iterator[str]
        """

        return iter(self._by_name)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Размер реестра
    #--------------------------------------------------------------------------------------------------------------
    def __len__(self) -> int:

        """Return the number of registered commands.

        :return: Command count.
        :rtype: int
        """

        return len(self._by_name)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------
