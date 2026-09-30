# Типизированные настройки исходящего gateway и разрешений приложений.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260930-164512
#
# Классы:
# -> GatewayPrincipal: Разрешённая принадлежность и назначения одного приложения.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек и защитное копирование.
#
# -> GatewayConfig: Общие ограничения и закрытые разрешения gateway.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек и защитное копирование.
#


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from dataclasses import dataclass, field

from ._credentials import validate_credentials
from ._validation import require_int, require_number, text_tuple
from .config import DeliveryMode, Destination
from .events import Identity
from .relay import validate_alias


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Разрешённая принадлежность и назначения одного приложения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayPrincipal:
    """Bind a service credential to one exact identity and a finite alias allowlist."""

    name: str                           # Локальное имя приложения для учёта доступа.
    token_env: str | None = field(default=None, repr=False)  # Имя переменной; альтернатива token.
    token: str | None = field(default=None, repr=False)      # Отдельный сервисный токен приложения.
    identity: Identity                  # Единственная разрешённая принадлежность отправителя.
    aliases: tuple[str, ...]            # Разрешённые имена назначений на gateway.
    capacity: int = 4                   # Максимум одновременно принятых запросов приложения.
    min_interval: float = 0.2           # Минимальный интервал приёма запросов; ноль отключает паузу.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек и защитное копирование
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate finite permissions without reading credentials."""

        validate_alias(self.name)

        # Значение из файла проверяется сразу; переменная окружения читается при start.
        validate_credentials(self.token, self.token_env, "gateway")
        if not isinstance(self.identity, Identity):
            raise TypeError("identity must be Identity")

        # Права копируются, чтобы изменение исходного списка не меняло работающую ACL.
        aliases = text_tuple(self.aliases, "aliases")
        if not aliases:
            raise ValueError("at least one alias is required")
        for alias in aliases:
            validate_alias(alias)
        object.__setattr__(self, "aliases", aliases)

        require_int(self.capacity, "capacity")
        if self.capacity > 256:
            raise ValueError("principal capacity exceeds 256")
        require_number(self.min_interval, "min_interval", allow_zero=True)
        if self.min_interval > 3600:
            raise ValueError("min_interval exceeds one hour")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Общие ограничения и закрытые разрешения gateway
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayConfig:
    """Configure a finite relay with one active attempt per direct destination."""

    destinations: tuple[Destination, ...]   # Прямые назначения; destination_id служит alias.
    principals: tuple[GatewayPrincipal, ...]    # Приложения с отдельными токенами и точными правами.
    capacity: int = 32                  # Общий предел чтения тел и выполняемых запросов.
    body_timeout: float = 2.0           # Максимальное время чтения одного тела, секунды.
    attempt_timeout: float = 8.0        # Верхний предел одной provider attempt, секунды.
    startup_timeout: float = 5.0        # Общий срок подготовки всех каналов, секунды.
    shutdown_timeout: float = 5.0       # Общий срок завершения запросов и очистки, секунды.
    future_tolerance: float = 0.0       # Допуск будущего created_at; expiry не продлевается.
    clock_skew_tolerance: float = 0.0   # Допуск расхождения UTC между машинами в обе стороны, секунды.
    destination_interval: float = 1.0   # Минимальная пауза между попытками одного alias, секунды.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек и защитное копирование
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy finite collections and reject relay cascades or ambiguous grants."""

        # Ограничиваем коллекции до копирования и проверки ссылок. Произвольные итераторы не принимаем.
        for name, item_type, maximum in (("destinations", Destination, 64),
                ("principals", GatewayPrincipal, 256)):
            items = getattr(self, name)
            if not isinstance(items, (tuple, list)) or not 1 <= len(items) <= maximum:
                raise ValueError(f"{name} must be a nonempty bounded list or tuple")
            if any(not isinstance(item, item_type) for item in items):
                raise TypeError(f"invalid {name}")
            object.__setattr__(self, name, tuple(items))

        # Alias однозначно выбирает локальный прямой канал. Сервер не строит цепочки relay.
        aliases = set()
        for destination in self.destinations:
            validate_alias(destination.destination_id)
            if destination.mode is not DeliveryMode.DIRECT or destination.retry.max_attempts != 1:
                raise ValueError("gateway destinations must be direct with max_attempts=1")
            if destination.destination_id in aliases:
                raise ValueError("duplicate gateway alias")
            aliases.add(destination.destination_id)
        if len({item.name for item in self.principals}) != len(self.principals):
            raise ValueError("duplicate principal name")
        if any(not set(item.aliases) <= aliases for item in self.principals):
            raise ValueError("principal refers to an unknown alias")

        require_int(self.capacity, "capacity")
        if self.capacity > 256:
            raise ValueError("gateway capacity exceeds 256")
        for name in ("body_timeout", "attempt_timeout", "startup_timeout", "shutdown_timeout"):
            require_number(getattr(self, name), name)
        require_number(self.future_tolerance, "future_tolerance", allow_zero=True)
        require_number(self.clock_skew_tolerance, "clock_skew_tolerance", allow_zero=True)
        if self.clock_skew_tolerance > 3600:
            raise ValueError("clock_skew_tolerance exceeds one hour")
        require_number(self.destination_interval, "destination_interval", allow_zero=True)
        if self.destination_interval > 3600:
            raise ValueError("destination_interval exceeds one hour")
        if self.future_tolerance > 30:
            raise ValueError("future_tolerance exceeds thirty seconds")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль remote_watch.gateway_config не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
