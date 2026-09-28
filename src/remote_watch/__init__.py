# Публичные контракты Remote Watch без запуска runtime и импорта провайдеров.
# Экспорт:
# -> Identity, Notification, SnapshotLimits: Неизменяемые события.
# -> Delivery, DeliveryResult, DeliveryStatus, ResultSource: Одна попытка доставки.
# -> NotificationChannel: Структурный async-протокол канала.
# -> Destination, DeliveryMode, RetryPolicy, Route, RuntimeConfig, WatcherConfig: Настройки.
# -> CommandCallback, ArgumentValidator, CommandContext, CommandRegistry, CommandSpec: Регистрация команд.
#
# Version 1.0.0
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
# Дата и время последнего изменения: 260928-110519

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
#------------------------------------------------------------------------------------------------------------------
