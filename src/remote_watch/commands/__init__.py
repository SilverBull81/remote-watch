# Регистрация команд приложения; сетевое выполнение подключается отдельно.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Экспорт:
# -> ArgumentValidator, CommandCallback, CommandContext, CommandRegistry, CommandSpec: Регистрация команд.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from .registry import ArgumentValidator as ArgumentValidator
from .registry import CommandCallback as CommandCallback
from .registry import CommandContext as CommandContext
from .registry import CommandRegistry as CommandRegistry
from .registry import CommandSpec as CommandSpec

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.commands не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
