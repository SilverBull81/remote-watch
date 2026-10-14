# Учётные данные и правила доступа к командам приложений.
#
# Version 1.0.4
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-164146
#
# Классы:
# -> CommandPrincipal: Права приложения с отдельным секретом команд
#    Специальные методы:
#    -> __post_init__(): Проверка полей и ограничений
#
# -> CommandAccess: Права отправителя в конкретном чате и приложении
#    Специальные методы:
#    -> __post_init__(): Проверка полей и ограничений
#
# -> CommandSource: Доверенный получатель сообщений провайдера
#    Специальные методы:
#    -> __post_init__(): Проверка полей и ограничений
#
# -> CommandHubConfig: Настройки авторизации и конечных пределов hub
#    Специальные методы:
#    -> __post_init__(): Проверка полей и ограничений
#
# Функции:
# -> _strings(): Копирование конечного набора разрешений
#
# -> _token(): Проверка формата отдельного секрета команд


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

from dataclasses import dataclass, field

from remote_watch._validation import require_int, require_number, require_text
from remote_watch.events import Identity

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Копирование конечного набора разрешений
#------------------------------------------------------------------------------------------------------------------
def _strings(
    values: frozenset[str],
    ) -> frozenset[str]:

    """
    Copy a finite set of explicit authorization scopes.

    :param values: Finite set of explicit authorization scopes.
    :type values: frozenset[str]

    :return: Immutable validated authorization scopes.
    :rtype: frozenset[str]
    """

    # values — конечный набор явно заданных прав.

    if not isinstance(values, (set, frozenset)) or not 1 <= len(values) <= 64:
        raise ValueError("invalid command scopes")

    for value in values:
        require_text(value, "scope", 128)

    return frozenset(values)
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Права приложения с отдельным секретом команд
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandPrincipal:
    """
    Authorize an application credential for exact identities and scopes.

    :param name: Stable configured command, principal, service or component name.
    :type name: str

    :param token: Private literal credential excluded from representations.
    :type token: str

    :param identities: Explicit application identities authorized for this principal.
    :type identities: tuple[Identity, ...]

    :param scopes: Explicit authorized or declared command scopes.
    :type scopes: frozenset[str]
    """

    name: str                           # Имя приложения в настройках hub.
    token: str = field(repr=False)      # Отдельный секрет команд; не токен шлюза уведомлений.
    identities: tuple[Identity, ...]    # Точные разрешённые экземпляры приложения.
    scopes: frozenset[str]              # Максимальные права регистрируемых команд.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(
        self,
        ) -> None:

        """
        Validate immutable application credentials and identity limits.
        """

        require_text(self.name, "principal name", 128)
        _token(self.token)

        if not isinstance(self.identities, (tuple, list)) or not 1 <= len(self.identities) <= 64:
            raise ValueError("invalid command identities")

        if any(type(identity) is not Identity for identity in self.identities):
            raise TypeError("invalid command identity")

        if len(set(self.identities)) != len(self.identities):
            raise ValueError("duplicate command identity")

        object.__setattr__(self, "identities", tuple(self.identities))
        object.__setattr__(self, "scopes", _strings(self.scopes))
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Права отправителя в конкретном чате и приложении
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandAccess:
    """
    Grant one provider actor access to an exact chat, application and scope set.

    :param actor_id: Authenticated command actor or explicitly configured private-topic writer.
    :type actor_id: str

    :param conversation_id: Provider conversation identifier checked by the command ACL.
    :type conversation_id: str

    :param identity: Explicit application identity used for routing and authorization.
    :type identity: Identity

    :param scopes: Explicit authorized or declared command scopes.
    :type scopes: frozenset[str]
    """

    actor_id: str = field(repr=False)           # Идентификатор отправителя у провайдера.
    conversation_id: str = field(repr=False)    # Идентификатор чата у провайдера.
    identity: Identity                          # Единственный адресуемый экземпляр приложения.
    scopes: frozenset[str]                      # Разрешённые этому отправителю права.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(
        self,
        ) -> None:

        """
        Reject wildcard identities and copy the scope set.
        """

        require_text(self.actor_id, "actor", 128)
        require_text(self.conversation_id, "conversation", 128)

        if type(self.identity) is not Identity:
            raise TypeError("invalid command identity")

        object.__setattr__(self, "scopes", _strings(self.scopes))
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Доверенный получатель сообщений провайдера
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandSource:
    """
    Authenticate a trusted provider reader independently of application clients.

    :param source_id: Stable ordered command-source identifier.
    :type source_id: str

    :param token: Private literal credential excluded from representations.
    :type token: str

    :param access: Explicit actor, conversation, Identity and scope permissions.
    :type access: tuple[CommandAccess, ...]
    """

    source_id: str                      # Постоянное имя упорядоченного источника.
    token: str = field(repr=False)      # Секрет доверенного адаптера входящих сообщений.
    access: tuple[CommandAccess, ...]   # Явные правила actor/chat/Identity/scope.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(
        self,
        ) -> None:

        """
        Validate one bounded trusted-source policy.
        """

        require_text(self.source_id, "source", 128)

        if not self.source_id.isascii() or not all(c.isalnum() or c in "_-" for c in self.source_id):
            raise ValueError("invalid command source")

        _token(self.token)

        if not isinstance(self.access, (tuple, list)) or not 1 <= len(self.access) <= 256:
            raise ValueError("invalid command access rules")

        if any(type(rule) is not CommandAccess for rule in self.access):
            raise TypeError("invalid command access rule")

        object.__setattr__(self, "access", tuple(self.access))
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки авторизации и конечных пределов hub
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class CommandHubConfig:
    """
    Bound command identities, sessions, pending records and network waits.

    :param principals: Explicitly configured authenticated application principals.
    :type principals: tuple[CommandPrincipal, ...]

    :param sources: Explicitly trusted provider sources and their access rules.
    :type sources: tuple[CommandSource, ...]

    :param session_ttl: Registration lifetime after a successful heartbeat, in seconds.
    :type session_ttl: float

    :param poll_timeout: Maximum long-poll duration, in seconds.
    :type poll_timeout: float

    :param storage_timeout: Shared storage admission and execution timeout, in seconds.
    :type storage_timeout: float

    :param shutdown_timeout: Maximum graceful shutdown wait, in seconds.
    :type shutdown_timeout: float

    :param refresh_interval: Trusted-time refresh interval, in seconds.
    :type refresh_interval: float

    :param max_sessions: Maximum live application sessions.
    :type max_sessions: int

    :param max_storage_waiters: Maximum queued hub storage operations.
    :type max_storage_waiters: int

    :param max_pending: Maximum pending commands admitted by the hub.
    :type max_pending: int
    """

    principals: tuple[CommandPrincipal, ...]    # Учётные данные приложений.
    sources: tuple[CommandSource, ...]          # Доверенные адаптеры провайдеров и их ACL.
    session_ttl: float = 60.0                   # Срок регистрации после heartbeat, секунды.
    poll_timeout: float = 5.0                   # Ожидание одного long poll, секунды.
    storage_timeout: float = 3.0                # Ожидание операции журнала, секунды.
    shutdown_timeout: float = 5.0               # Общий срок остановки, секунды.
    refresh_interval: float = 60.0              # Период обновления внешнего времени, секунды.
    max_sessions: int = 256                     # Включая истёкшие сессии текущего запуска hub.
    max_storage_waiters: int = 64               # Ожидающие операции hub; executor выполняет только одну.
    max_pending: int = 1000                     # Неподтверждённые или ещё исполняемые команды.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(
        self,
        ) -> None:

        """
        Validate limits and prohibit ambiguous credential or identity ownership.
        """

        for name, kind in (("principals", CommandPrincipal), ("sources", CommandSource)):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 64:
                raise ValueError("invalid command owners")
            if any(type(value) is not kind for value in values):
                raise TypeError("invalid command owner")
            object.__setattr__(self, name, tuple(values))

        tokens = [owner.token for owner in (*self.principals, *self.sources)]
        names = [p.name for p in self.principals]
        sources = [s.source_id for s in self.sources]
        identities = [identity for p in self.principals for identity in p.identities]

        if any(len(set(values)) != len(values) for values in (tokens, names, sources, identities)):
            raise ValueError("ambiguous command ownership")

        for source in self.sources:
            if any(rule.identity not in identities for rule in source.access):
                raise ValueError("unknown command target")

        for name, low, high in (
            ("session_ttl", 10, 300), ("poll_timeout", 0.01, 20), ("storage_timeout", 0.01, 30),
            ("shutdown_timeout", 0.1, 30), ("refresh_interval", 0.1, 120),
        ):
            value = getattr(self, name)
            require_number(value, name)
            if not low <= value <= high:
                raise ValueError("invalid command time limit")

        if self.poll_timeout >= self.session_ttl / 2:
            raise ValueError("poll exceeds session budget")

        for name, high in (("max_sessions", 4096), ("max_pending", 1000), ("max_storage_waiters", 256)):
            require_int(getattr(self, name), name)
            if getattr(self, name) > high:
                raise ValueError("invalid command capacity")
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка формата отдельного секрета команд
#------------------------------------------------------------------------------------------------------------------
def _token(
    token: str,
    ) -> None:

    """
    Require a printable, nonempty command-only bearer secret.

    :param token: Command-only credential, never logged or echoed.
    :type token: str
    """

    # token — отдельный секрет команд, не выводимый в сообщения и журнал.

    require_text(token, "command token", 256)

    if len(token) < 32 or not token.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("invalid command token")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль hub_config не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
