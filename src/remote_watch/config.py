# Настройки приложения; их проверка не запускает потоки, обработчики команд или сетевые клиенты.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-121352
#
# Классы:
#
# -> DeliveryMode: Отправка напрямую или через будущий шлюз.
#
# -> RetryPolicy: Число попыток и время на отправку уведомления.
#    -> __post_init__(): Проверка чисел и взаимных ограничений.
#
# -> RuntimeConfig: Размеры очередей и время на запуск и остановку.
#    -> __post_init__(): Проверка лимитов.
#
# -> Destination: Получатель и функция создания канала.
#    -> __post_init__(): Проверка режима и фабрики без вызова.
#
# -> Route: Условия выбора получателей.
#    -> __post_init__(): Защитная копия условий и ссылок.
#
# -> WatcherConfig: Общие настройки приложения.
#    -> __post_init__(): Проверка ссылок, дубликатов и регистрация обработчиков команд.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from ._validation import require_callback, require_int, require_number, require_text, text_tuple
from .channels import NotificationChannel
from .commands import CommandCallback, CommandRegistry
from .events import Identity, SnapshotLimits

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Способ отправки выбранному получателю
#------------------------------------------------------------------------------------------------------------------
class DeliveryMode(str, Enum):
    """Name the direct mode and the reserved, currently unsupported relay mode."""

    DIRECT = "direct"
    RELAY = "relay"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Политика повторов и таймаутов
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    """Declare bounded delivery policies without scheduling any attempts.

    max_attempts includes the first send; connect_timeout is within attempt_timeout.
    backoff_base/backoff_cap define future exponential full-jitter limits. ttl bounds
    event lifetime. Time values are finite positive seconds.
    """

    max_attempts: int = 3  # Число попыток вместе с первой отправкой.
    connect_timeout: float = 3.0  # Ожидание соединения, секунды.
    attempt_timeout: float = 10.0  # Время на всю попытку, секунды.
    backoff_base: float = 1.0  # Начальная задержка повторов, секунды.
    backoff_cap: float = 30.0  # Наибольшая задержка повторов, секунды.
    ttl: float = 300.0  # Срок актуальности уведомления, секунды.

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка ограничений времени
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate finite budgets and timeout/backoff relationships."""

        # Число попыток включает первую отправку; все интервалы задаются положительным числом секунд.
        require_int(self.max_attempts, "max_attempts")

        for name in ("connect_timeout", "attempt_timeout", "backoff_base", "backoff_cap", "ttl"):
            require_number(getattr(self, name), name)

        # Соединение входит во время попытки, а начальная задержка не должна превышать верхний предел.
        if self.connect_timeout > self.attempt_timeout:
            raise ValueError("connect_timeout exceeds attempt_timeout")

        if self.backoff_base > self.backoff_cap:
            raise ValueError("backoff_base exceeds backoff_cap")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Размеры очередей и время на запуск и остановку
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeConfig:
    """Declare runtime bounds without allocating queues or starting workers.

    ingress_capacity bounds incoming snapshots; max_destinations bounds fan-out.
    startup_timeout/shutdown_timeout are total lifecycle deadlines in seconds.
    snapshot_limits defines byte budgets for notification validation.
    """

    ingress_capacity: int = 1024  # Число мест во входной очереди.
    max_destinations: int = 16  # Наибольшее число настроенных получателей.
    startup_timeout: float = 5.0  # Время на запуск, секунды.
    shutdown_timeout: float = 5.0  # Время на завершение работы, секунды.
    snapshot_limits: SnapshotLimits = field(default_factory=SnapshotLimits)  # Ограничения размера уведомления.

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка ограничений runtime
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject invalid capacities, deadlines and snapshot policies."""

        # Настройки только описывают ограничения: очереди и рабочие потоки здесь не создаются.
        require_int(self.ingress_capacity, "ingress_capacity")
        require_int(self.max_destinations, "max_destinations")
        require_number(self.startup_timeout, "startup_timeout")
        require_number(self.shutdown_timeout, "shutdown_timeout")

        if not isinstance(self.snapshot_limits, SnapshotLimits):
            raise TypeError("snapshot_limits must be SnapshotLimits")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Получатель и настройки его канала
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Destination:
    """Bind a logical destination to a lazy synchronous channel factory.

    destination_id is a unique routing name; provider is descriptive, not an import.
    channel_factory takes no arguments and creates a NotificationChannel when the
    future runtime starts. The factory owns provider configuration/secret references
    and is excluded from repr. mode must currently be DIRECT. outstanding_capacity
    covers queued, active and retrying deliveries. retry is the delivery policy.
    """

    destination_id: str  # Уникальное имя получателя в настройках.
    channel_factory: Callable[[], NotificationChannel] = field(  # Функция создания канала без аргументов.
        repr=False,
        )
    provider: str = "custom"  # Название сервиса или типа канала.
    mode: DeliveryMode = DeliveryMode.DIRECT  # Способ отправки: напрямую или через шлюз.
    outstanding_capacity: int = 256  # Предел всех незавершённых доставок.
    retry: RetryPolicy = field(default_factory=RetryPolicy)  # Настройки попыток отправки.

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка настроек получателя без создания клиента
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate identifiers, lazy factory and currently supported transport."""

        # Сначала проверяем имена и способ отправки; название сервиса не используется для импорта кода.
        require_text(self.destination_id, "destination_id")
        require_text(self.provider, "provider")

        if not isinstance(self.mode, DeliveryMode):
            raise TypeError("mode must be DeliveryMode")

        # Режим шлюза зарезервирован в интерфейсе, но его реализация ещё не готова.
        if self.mode is DeliveryMode.RELAY:
            raise ValueError("relay delivery is not implemented")

        require_int(self.outstanding_capacity, "outstanding_capacity")

        if not isinstance(self.retry, RetryPolicy):
            raise TypeError("retry must be RetryPolicy")

        # Фабрикой может быть функция, partial или класс с конструктором без обязательных аргументов.
        # Проверяем только сигнатуру: создание сетевого клиента отложено до запуска отправки.
        if inspect.isclass(self.channel_factory):
            try:
                inspect.signature(self.channel_factory).bind()
            except (TypeError, ValueError):
                raise ValueError("channel_factory requires a zero-argument signature") from None
        else:
            require_callback(self.channel_factory, 0, "channel_factory", allow_async=False)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Условия выбора получателей уведомления
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Route:
    """Declare an AND predicate selecting one or more destinations.

    destination_ids contains unique names. min_level defaults to ERROR; future
    notify=True bypasses only this threshold. topic/identity fields require exact
    matches when supplied. All required_tags must occur in the event. Rules will
    be combined by union; this class validates data but does not route events.
    """

    destination_ids: tuple[str, ...]  # Имена получателей для этого правила.
    min_level: int = logging.ERROR  # Минимальный уровень важности сообщения.
    topic: str | None = None  # Требуемая тема; None — любая.
    required_tags: tuple[str, ...] = ()  # Метки, которые должны быть у сообщения.
    service: str | None = None  # Требуемое приложение; None — любое.
    environment: str | None = None  # Требуемое окружение; None — любое.
    region: str | None = None  # Требуемый регион; None — любой.
    host: str | None = None  # Требуемый сервер; None — любой.
    instance_id: str | None = None  # Требуемый экземпляр; None — любой.

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка и копирование условий отправки
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Copy immutable predicates and reject empty destination lists."""

        # Копируем списки условий, чтобы последующие изменения настроек вызывающим кодом не меняли правило.
        object.__setattr__(self, "destination_ids", text_tuple(self.destination_ids, "destination_ids"))

        if not self.destination_ids:
            raise ValueError("route must select at least one destination")

        object.__setattr__(self, "required_tags", text_tuple(self.required_tags, "required_tags"))
        require_int(self.min_level, "min_level", 0)

        # None означает отсутствие условия. Если условие задано, строка должна быть непустой.
        for name in ("topic", "service", "environment", "region", "host", "instance_id"):
            value = getattr(self, name)

            if value is not None:
                require_text(value, name)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Проверенная конфигурация приложения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class WatcherConfig:
    """Collect validated configuration without starting a watcher or command source.

    identity binds the application. destinations/routes are defensively copied.
    runtime sets queue/lifecycle limits. commands accepts a CommandRegistry or a
    name-to-zero-argument-callback mapping, normalized to a registry on construction.
    No commands are built in or automatically enabled. Empty routes select nothing.
    """

    identity: Identity  # Сведения о текущем приложении.
    destinations: tuple[Destination, ...] = ()  # Настроенные получатели уведомлений.
    routes: tuple[Route, ...] = ()  # Правила выбора получателей.
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)  # Настройки очередей и времени работы.
    commands: CommandRegistry | Mapping[str, CommandCallback] = field(  # Пользовательские команды.
        default_factory=CommandRegistry,
        )

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка связей и защитное копирование
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate references and copy local command registrations without execution."""

        # Проверяем вложенные объекты до обращения к их ограничениям и спискам.
        if not isinstance(self.identity, Identity):
            raise TypeError("identity must be Identity")

        if not isinstance(self.runtime, RuntimeConfig):
            raise TypeError("runtime must be RuntimeConfig")

        # Принимаем конечные списки и кортежи, затем сохраняем независимые кортежи.
        # Произвольный итератор мог бы оказаться бесконечным или выполнять пользовательский код при чтении.
        for name, item_type in (("destinations", Destination), ("routes", Route)):
            value = getattr(self, name)

            if not isinstance(value, (list, tuple)):
                raise TypeError(f"{name} must be a list or tuple")

            if any(not isinstance(item, item_type) for item in value):
                raise TypeError(f"{name} contains an invalid value")

            object.__setattr__(self, name, tuple(value))

        if len(self.destinations) > self.runtime.max_destinations:
            raise ValueError("destinations exceeds max_destinations")

        # Имена получателей должны быть уникальны; каждое правило ссылается только на объявленные имена.
        known = {destination.destination_id for destination in self.destinations}

        if len(known) != len(self.destinations):
            raise ValueError("duplicate destination_id")

        for route in self.routes:
            if not set(route.destination_ids) <= known:
                raise ValueError("route references an unknown destination_id")

        # Привычный словарь callback-функций преобразуем в реестр, не вызывая ни одну из функций.
        if not isinstance(self.commands, CommandRegistry):
            object.__setattr__(self, "commands", CommandRegistry.from_callbacks(self.commands))
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.config не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
