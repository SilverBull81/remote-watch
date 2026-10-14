# Регистрация команд приложения; сетевое выполнение подключается отдельно.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
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
