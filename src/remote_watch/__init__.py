# Общие типы Remote Watch, доступные приложениям при импорте пакета.
#
# Version 1.0.6
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Экспорт:
# -> Identity, Notification, SnapshotLimits: Данные приложения и уведомления.
# -> Delivery, DeliveryResult, DeliveryStatus, ResultSource: Одна попытка доставки.
# -> NotificationChannel: Асинхронный интерфейс канала.
# -> Destination, DeliveryMode, RetryPolicy, Route, RuntimeConfig, WatcherConfig: Настройки.
# -> CommandCallback, ArgumentValidator, CommandContext, CommandRegistry, CommandSpec: Регистрация команд.
# -> NotificationHandler, PolicyRouter: Подготовка записей logging и выбор получателей.
# -> NotificationRuntime, RuntimeState, RuntimeStats: Фоновая отправка, состояние и счётчики.
# -> DeliveryClock, SystemDeliveryClock: Подменяемые часы доставки и ожидания повторов.
# -> RemoteWatcher, ConsoleConfig, RotatingFileConfig: Подключение logger и локальных журналов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from remote_watch.commands.registry import ArgumentValidator as ArgumentValidator
from remote_watch.commands.registry import CommandCallback as CommandCallback
from remote_watch.commands.registry import CommandContext as CommandContext
from remote_watch.commands.registry import CommandRegistry as CommandRegistry
from remote_watch.commands.registry import CommandSpec as CommandSpec
from remote_watch.config import DeliveryMode as DeliveryMode
from remote_watch.config import Destination as Destination
from remote_watch.config import RetryPolicy as RetryPolicy
from remote_watch.config import Route as Route
from remote_watch.config import RuntimeConfig as RuntimeConfig
from remote_watch.config import WatcherConfig as WatcherConfig
from remote_watch.events import Identity as Identity
from remote_watch.events import Notification as Notification
from remote_watch.events import SnapshotLimits as SnapshotLimits
from remote_watch.notifications.channels import NotificationChannel as NotificationChannel
from remote_watch.notifications.delivery import Delivery as Delivery
from remote_watch.notifications.delivery import DeliveryResult as DeliveryResult
from remote_watch.notifications.delivery import DeliveryStatus as DeliveryStatus
from remote_watch.notifications.delivery import ResultSource as ResultSource
from remote_watch.notifications.handler import NotificationHandler as NotificationHandler
from remote_watch.notifications.routing import PolicyRouter as PolicyRouter
from remote_watch.notifications.runtime import NotificationRuntime as NotificationRuntime
from remote_watch.notifications.runtime import RuntimeState as RuntimeState
from remote_watch.notifications.runtime import RuntimeStats as RuntimeStats
from remote_watch.notifications.timing import DeliveryClock as DeliveryClock
from remote_watch.notifications.timing import SystemDeliveryClock as SystemDeliveryClock
from remote_watch.watcher import ConsoleConfig as ConsoleConfig
from remote_watch.watcher import RemoteWatcher as RemoteWatcher
from remote_watch.watcher import RotatingFileConfig as RotatingFileConfig

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.__init__ не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
