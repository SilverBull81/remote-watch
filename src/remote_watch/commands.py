# Локальная регистрация пользовательских команд без удалённого приёма и исполнения.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-140516
#
# Классы:
# -> CommandContext: Данные запроса для обработчика команды.
#    Специальные методы:
#    -> __post_init__(): Проверка и фиксация контекста.
#
# -> CommandSpec: Обработчик команды и условия его вызова.
#    Специальные методы:
#    -> __post_init__(): Проверка описания команды при регистрации.
#
# -> CommandRegistry: Реестр команд с поиском по имени.
#    Интерфейс:
#    -> from_callbacks(): Адаптация словаря callback-функций.
#    Специальные методы:
#    -> __post_init__(): Проверка уникальности и фиксация реестра.
#    -> __getitem__(): Поиск зарегистрированной команды.
#    -> __iter__(): Перечисление имён команд.
#    -> __len__(): Размер реестра.
#
# Типы:
#
# -> CommandCallback, ArgumentValidator: Контракты локальных функций.


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

    command_id: str         # Идентификатор запроса на выполнение команды.
    actor_id: str           # Идентификатор отправителя команды.
    conversation_id: str    # Идентификатор чата или беседы.
    identity: Identity      # Сведения о целевом приложении.
    session_id: str         # Идентификатор целевого запуска приложения.
    arguments: Mapping[str, str] = field(   # Именованные строковые аргументы команды.
        default_factory=dict,
        repr=False,
        )

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка и фиксация контекста
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy bounded textual arguments and validate explicit identities."""

        # Контекст хранит сведения о запросе, но сам по себе не подтверждает права отправителя.
        # Проверку доступа должен выполнить будущий диспетчер до вызова обработчика.
        if not isinstance(self.identity, Identity):
            raise TypeError("identity must be Identity")

        for name in ("command_id", "actor_id", "conversation_id", "session_id"):
            require_text(getattr(self, name), name)

        # Ограничиваем число аргументов до копирования и проверки их содержимого.
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")

        if len(self.arguments) > 32:
            raise ValueError("arguments has too many entries")

        # Отдельный словарь защищает контекст от последующих изменений исходных аргументов.
        copied = dict(self.arguments)

        for key, value in copied.items():
            require_text(key, "argument name", 64)
            require_text(value, "argument value", 1024, True)

        # Помимо предела для каждой строки проверяем суммарный размер имён и значений в байтах.
        size = sum(len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in copied.items())

        if size > 4096:
            raise ValueError("arguments exceeds its UTF-8 byte budget")

        # После проверки открываем только чтение; менять аргументы готового контекста нельзя.
        object.__setattr__(self, "arguments", MappingProxyType(copied))
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Описание команды и условий её выполнения
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

    name: str                                       # Имя команды в приложении.
    callback: CommandCallback = field(repr=False)   # Пользовательский обработчик команды.
    takes_context: bool = False                     # Передавать ли обработчику CommandContext.
    description: str = ""                           # Описание команды для справки.
    required_scope: str | None = None               # Право доступа, необходимое для команды.
    timeout: float = 10.0                           # Предельное время выполнения, секунды.
    read_only: bool = False                         # Заявлено ли отсутствие изменений состояния.
    idempotent: bool = False                        # Заявлена ли безопасность повторного вызова.
    validate_arguments: ArgumentValidator | None = field(   # Проверка аргументов до вызова обработчика.
        default=None,
        repr=False,
        )

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка описания команды при регистрации
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate name, policy and callable signatures without executing handlers."""

        # Имя команды используется при поиске и формировании права доступа, поэтому формат строгий.
        require_text(self.name, "command name", 64)

        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name) is None:
            raise ValueError("command name must match [a-z][a-z0-9_]{0,63}")

        # Это явные заявления приложения о поведении команды; библиотека не может доказать их истинность.
        for name in ("takes_context", "read_only", "idempotent"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")

        require_text(self.description, "description", 2048, True)
        require_number(self.timeout, "timeout")

        # Отсутствующее право заменяем именованным требованием, а не разрешением для всех отправителей.
        if self.required_scope is None:
            object.__setattr__(self, "required_scope", f"command:{self.name}")

        require_text(self.required_scope, "required_scope")
        # Проверяем возможность вызова с нужным числом аргументов, не исполняя пользовательский код.
        # Словарь функций и partial сохраняет привычный вызов без аргументов.
        require_callback(self.callback, int(self.takes_context), "callback")

        # Проверка входных строк имеет смысл только при передаче контекста обработчику.
        if self.validate_arguments is not None:
            if not self.takes_context:
                raise ValueError("validate_arguments requires takes_context=True")

            require_callback(self.validate_arguments, 1, "validate_arguments", allow_async=False)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Реестр команд приложения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandRegistry(Mapping[str, CommandSpec]):
    """Provide read-only name lookup for an application-owned command catalogue.

    specs is copied to a tuple. Duplicate names are rejected. Callback references
    remain local and may capture mutable application state; they are not serialized.
    This registry has no remote transport, authorization or dispatch method.
    """

    specs: tuple[CommandSpec, ...] = field(default=(), repr=False)  # Зарегистрированные команды приложения.
    _by_name: Mapping[str, CommandSpec] = field(                    # Поиск команды по имени без изменения реестра.
        init=False,
        repr=False,
        compare=False,
        )

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Адаптация словаря callback-функций
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

        # Для каждой функции действуют обычные строгие настройки CommandSpec.
        # Обработчики только сохраняются: регистрация не выполняет команду и не открывает удалённый доступ.
        return cls(specs=tuple(CommandSpec(name=name, callback=callback) for name, callback in callbacks.items()))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка уникальности и фиксация реестра
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy the finite command list and reject duplicate names."""

        if not isinstance(self.specs, (list, tuple)):
            raise TypeError("specs must be a list or tuple")

        # Сохраняем порядок регистрации и независимую копию набора команд.
        copied = tuple(self.specs)
        by_name = {}

        # Два одинаковых имени считаем ошибкой настройки, чтобы обработчик не заменялся незаметно.
        for spec in copied:
            if not isinstance(spec, CommandSpec):
                raise TypeError("specs must contain CommandSpec values")

            if spec.name in by_name:
                raise ValueError("duplicate command name")

            by_name[spec.name] = spec

        # Оба способа чтения реестра используют один проверенный набор; изменения через словарь запрещены.
        object.__setattr__(self, "specs", copied)
        object.__setattr__(self, "_by_name", MappingProxyType(by_name))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Поиск зарегистрированной команды
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
    # СПЕЦИАЛЬНЫЙ МЕТОД : Перечисление имён команд
    #--------------------------------------------------------------------------------------------------------------
    def __iter__(self) -> Iterator[str]:

        """Return command names in registration order.

        :return: Iterator over registered names.
        :rtype: Iterator[str]
        """

        return iter(self._by_name)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Размер реестра
    #--------------------------------------------------------------------------------------------------------------
    def __len__(self) -> int:

        """Return the number of registered commands.

        :return: Command count.
        :rtype: int
        """

        return len(self._by_name)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.commands не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
