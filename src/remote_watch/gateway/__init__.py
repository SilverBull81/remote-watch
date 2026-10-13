# Исходящий gateway и публичные настройки сервера.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261013-164040
#
# Экспорт:
# -> NotificationGateway, NotificationGatewayStartupError, main: Сервер и его запуск.
# -> NotificationGatewayServerConfig, NotificationGatewayPrincipal: Настройки сервера и прав приложений.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from .notification_config import NotificationGatewayPrincipal as NotificationGatewayPrincipal
from .notification_config import NotificationGatewayServerConfig as NotificationGatewayServerConfig
from .notification_server import NotificationGateway as NotificationGateway
from .notification_server import NotificationGatewayStartupError as NotificationGatewayStartupError
from .notification_server import main as main

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.gateway не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
