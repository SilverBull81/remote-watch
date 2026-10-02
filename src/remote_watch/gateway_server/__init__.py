# Настройки общего gateway server и ограниченного перезапуска его компонентов.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-184007
#
# Экспорт:
# -> CaddyConfig, ComponentConfig, DeploymentConfig: Общие настройки deployment.
# -> DeploymentConfigError, load_deployment_config: Локальная проверка файлов.
# -> LifecycleConfig: Пределы запуска, наблюдения и остановки.
# -> FailureKind, RestartBudget, RestartDecision, RestartPolicy: Правила перезапуска.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
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
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль экспорта remote_watch.gateway_server не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
