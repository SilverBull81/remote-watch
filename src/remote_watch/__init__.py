# Общие типы Remote Watch, доступные приложениям при импорте пакета.
#
# Version 1.0.4
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-142747
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


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from .channels import NotificationChannel as NotificationChannel
from .commands import ArgumentValidator as ArgumentValidator
from .commands import CommandCallback as CommandCallback
from .commands import CommandContext as CommandContext
from .commands import CommandRegistry as CommandRegistry
from .commands import CommandSpec as CommandSpec
from .config import DeliveryMode as DeliveryMode
from .config import Destination as Destination
from .config import RetryPolicy as RetryPolicy
from .config import Route as Route
from .config import RuntimeConfig as RuntimeConfig
from .config import WatcherConfig as WatcherConfig
from .delivery import Delivery as Delivery
from .delivery import DeliveryResult as DeliveryResult
from .delivery import DeliveryStatus as DeliveryStatus
from .delivery import ResultSource as ResultSource
from .events import Identity as Identity
from .events import Notification as Notification
from .events import SnapshotLimits as SnapshotLimits
from .logging_handler import NotificationHandler as NotificationHandler
from .routing import PolicyRouter as PolicyRouter
from .runtime import NotificationRuntime as NotificationRuntime
from .runtime import RuntimeState as RuntimeState
from .runtime import RuntimeStats as RuntimeStats
from .timing import DeliveryClock as DeliveryClock
from .timing import SystemDeliveryClock as SystemDeliveryClock

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.__init__ не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
