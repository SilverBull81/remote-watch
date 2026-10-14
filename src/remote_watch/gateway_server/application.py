# Настройки интерфейса и независимых оповещений владельца gateway server.
#
# Version 1.0.5
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-164146
#
# Классы:
# -> ApplicationConfig: Трей, ротация журналов и прямые каналы владельца
#    Специальные методы:
#    -> __post_init__(): Проверка типов и конечных пределов настроек
#
# -> OperatorAlerts: Ограничение повторов и доставка служебных сообщений
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта.
#    Интерфейс:
#    -> start(): Подготовка ресурсов и запуск фоновой работы
#    -> observe(): Учёт сбоя или восстановления с ограничением повторов
#    -> close(): Освобождение только собственных ресурсов
#    -> stats(): Счётчики попыток доставки служебных оповещений
#
# Функции:
# -> application_config(): Разбор настроек без разрешения секретов или сетевых вызовов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

from remote_watch._validation import require_int, require_number
from remote_watch.config import DeliveryMode, Destination, Route, RuntimeConfig, WatcherConfig
from remote_watch.events import Identity
from remote_watch.gateway.notification_json_config import _destination
from remote_watch.gateway_server.messages import REASONS, STATES
from remote_watch.watcher import RemoteWatcher

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Трей, ротация журналов и прямые каналы владельца
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    """
    Keep optional operator presentation separate from component credentials and routing.

    :param ui: Headless or interactive Windows tray mode.
    :type ui: str

    :param log_max_bytes: Maximum server log file size before rotation, in bytes.
    :type log_max_bytes: int

    :param log_backups: Number of rotated server log files retained.
    :type log_backups: int

    :param cooldown: Minimum interval between owner notices for one component, in seconds.
    :type cooldown: float

    :param destinations: Notification destinations or their observed health snapshots.
    :type destinations: tuple[Destination, ...]

    :param icon_path: Configured tray icon path, or None for the packaged default.
    :type icon_path: Path | None
    """

    ui: str = "headless"                         # headless либо Windows tray.
    log_max_bytes: int = 1048576                 # Размер одного служебного журнала.
    log_backups: int = 5                         # Количество архивов журнала.
    cooldown: float = 60.0                       # Минимальный интервал сообщений одного компонента.
    destinations: tuple[Destination, ...] = field(default=(), repr=False)  # Прямые каналы владельца.
    icon_path: Path | None = field(default=None, repr=False)  # Своя иконка; None — встроенный логотип.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка типов и конечных пределов настроек
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(
        self,
        ) -> None:

        """
        Validate finite bounds and direct operator-only channels.
        """

        if self.ui not in ("headless", "tray"):
            raise ValueError("invalid application UI")

        if self.icon_path is not None and (
                not isinstance(self.icon_path, Path) or not self.icon_path.is_absolute()):
            raise ValueError("absolute icon path required")

        require_int(self.log_max_bytes, "log_max_bytes", 4096)
        require_int(self.log_backups, "log_backups", 1)
        require_number(self.cooldown, "cooldown")
        if self.log_max_bytes > 100 * 1024 * 1024 or self.log_backups > 20 or not 1 <= self.cooldown <= 86400:
            raise ValueError("invalid application limits")

        if type(self.destinations) is not tuple or len(self.destinations) > 8:
            raise ValueError("invalid operator destinations")

        if any(not isinstance(item, Destination) or item.provider not in ("telegram", "ntfy")
               or item.mode is not DeliveryMode.DIRECT
               for item in self.destinations):
            raise ValueError("invalid operator destination")

        if len({item.destination_id for item in self.destinations}) != len(self.destinations):
            raise ValueError("duplicate operator destination")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Разбор настроек без разрешения секретов или сетевых вызовов.
#------------------------------------------------------------------------------------------------------------------
def application_config(
    value: object,
    base_dir: Path | None = None,
    ) -> ApplicationConfig:

    """
    Parse a small JSON section using the existing direct-provider configuration schema.

    :param value: Untrusted application section from the deployment file.
    :type value: object

    :param base_dir: Deployment directory for relative icon paths, or None for absolute paths only.
    :type base_dir: Path | None

    :return: Validated lazy settings without credential resolution or filesystem writes.
    :rtype: ApplicationConfig
    """

    # value — только фиксированные ключи; тела ошибок провайдера не выводятся.
    # base_dir — путь считается от конфига, а не от текущей папки запуска.

    if type(value) is not dict or set(value) - {"ui", "icon_path", "log_max_bytes", "log_backups", "alerts"}:
        raise ValueError("invalid application settings")

    settings = {key: item for key, item in value.items() if key not in {"alerts", "icon_path"}}
    icon = value.get("icon_path")
    if icon is not None:
        if type(icon) is not str or not icon.strip() or "\x00" in icon:
            raise ValueError("invalid icon path")
        path = Path(icon)
        if not path.is_absolute():
            if base_dir is None:
                raise ValueError("icon base directory required")
            path = base_dir / path
        settings["icon_path"] = path.resolve()

    alerts = value.get("alerts", {})
    if type(alerts) is not dict or set(alerts) - {"destinations", "cooldown"}:
        raise ValueError("invalid alert settings")

    destinations = alerts.get("destinations", [])
    if type(destinations) is not list or len(destinations) > 8:
        raise ValueError("invalid alert destinations")

    # Малые независимые очереди; отправка напрямую, без обращения к себе через шлюз и без чтения команд бота.
    channels = tuple(replace(_destination(item), outstanding_capacity=16) for item in destinations)
    return ApplicationConfig(**settings, cooldown=alerts.get("cooldown", 60), destinations=channels)
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ограничение повторов и доставка служебных сообщений
#------------------------------------------------------------------------------------------------------------------
class OperatorAlerts:
    """
    Coalesce component transitions and use an independent bounded notification runtime.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: ApplicationConfig,
        ) -> None:

        """
        Retain lazy channels and fixed-size transition state without network access.

        :param config: Application settings with explicitly configured operator recipients.
        :type config: ApplicationConfig
        """

        # config — отдельные назначения владельца, не все чаты приложений автоматически.

        self.config = config
        self.logger = logging.Logger("remote_watch.gateway_operator", logging.INFO)
        self.logger.propagate = False
        self.logger.addHandler(logging.NullHandler())
        self.watcher = None
        self._seen: dict[str, tuple[str, str | None]] = {}
        self._pending: dict[str, tuple[str, str | None]] = {}
        self._sent: dict[str, float] = {}
        self._troubled: set[str] = set()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка ресурсов и запуск фоновой работы
    #--------------------------------------------------------------------------------------------------------------
    async def start(
        self,
        ) -> None:

        """
        Start direct outbound-only channels when recipients are explicitly configured.
        """

        if not self.config.destinations:
            return

        identity = Identity(service="gateway-server", environment="operations", region="local",
                            host="gateway", instance_id="manager")
        route = Route(destination_ids=tuple(item.destination_id for item in self.config.destinations),
                      min_level=logging.INFO)
        self.watcher = RemoteWatcher(WatcherConfig(identity=identity, destinations=self.config.destinations,
                                                  routes=(route,), runtime=RuntimeConfig(shutdown_timeout=2)),
                                     logger=self.logger)
        await self.watcher.astart()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Учёт сбоя или восстановления с ограничением повторов
    #--------------------------------------------------------------------------------------------------------------
    def observe(
        self,
        name: str,
        state: str,
        reason: str | None,
        now: float,
        ) -> None:

        """
        Keep only the latest transition per fixed component and rate-limit sends.

        :param name: Fixed component label assigned by the supervisor.
        :type name: str


        :param state: Sanitized lifecycle state.
        :type state: str


        :param reason: Sanitized diagnostic code or None.
        :type reason: str | None


        :param now: Manager monotonic observation time.
        :type now: float
        """

        # name/state/reason — не содержимое провайдера; now задаёт cooldown без UTC VM.

        if name not in {"notifications", "commands", "caddy", "manager"}:
            return

        if state not in STATES or (reason is not None and reason not in REASONS):
            return

        current = (state, reason)
        if self._seen.get(name) != current:
            self._seen[name] = current
            if state in {"failed", "degraded", "restarting"}:
                self._troubled.add(name)
                self._pending[name] = current
            elif state == "ready" and name in self._troubled:
                self._pending[name] = current
            else:
                self._pending.pop(name, None)

        if name in self._pending and now - self._sent.get(name, -float("inf")) >= self.config.cooldown:
            state, reason = self._pending.pop(name)
            self._sent[name] = now
            if state == "ready":
                self._troubled.discard(name)
            self.logger.log(logging.INFO if state == "ready" else logging.ERROR,
                            "Gateway server: %s is %s; reason=%s%s", name, state, reason or "none",
                            ". Applications need new command sessions after a hub restart."
                            if name == "commands" and state == "restarting" else "")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Освобождение только собственных ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(
        self,
        ) -> None:

        """
        Close the bounded outbound runtime without depending on a child gateway.
        """

        if self.watcher is not None:
            await self.watcher.astop()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Счётчики попыток доставки служебных оповещений
    #--------------------------------------------------------------------------------------------------------------
    def stats(
        self,
        ) -> dict[str, int]:

        """
        Expose cumulative direct-alert delivery counters without claiming phone receipt.

        :return: Fixed aggregate counters, empty when alerts are disabled.
        :rtype: dict[str, int]
        """

        if self.watcher is None:
            return {}

        snapshot = self.watcher.runtime.stats()
        return {name: getattr(snapshot, name) for name in (
            "attempts", "accepted", "failed_attempts", "unknown_attempts", "expired", "destination_overflow")}
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль application не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
