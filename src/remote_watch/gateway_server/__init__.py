# Настройки общего gateway server и ограниченного перезапуска его компонентов.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Экспорт:
# -> ApplicationConfig: Необязательный трей и прямые оповещения владельца.
# -> CaddyConfig, ComponentConfig, DeploymentConfig: Общие настройки развёртывания сервера.
# -> DeploymentConfigError, load_deployment_config: Локальная проверка файлов.
# -> LifecycleConfig: Пределы запуска, наблюдения и остановки.
# -> FailureKind, RestartBudget, RestartDecision, RestartPolicy: Правила перезапуска.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from .application import ApplicationConfig as ApplicationConfig
from .config import CaddyConfig as CaddyConfig
from .config import ComponentConfig as ComponentConfig
from .config import DeploymentConfig as DeploymentConfig
from .config import DeploymentConfigError as DeploymentConfigError
from .config import LifecycleConfig as LifecycleConfig
from .config import load_deployment_config as load_deployment_config
from .policy import FailureKind as FailureKind
from .policy import RestartBudget as RestartBudget
from .policy import RestartDecision as RestartDecision
from .policy import RestartPolicy as RestartPolicy

#------------------------------------------------------------------------------------------------------------------
