# Закрытый старт установленного Caddy с наследованием владельца процесса.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261008-203635
#
# Функции:
# -> main(): Привязка к родителю до exec установленного binary.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import os
import subprocess
import sys

from remote_watch.gateway_server.processes import parent_guard


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Привязка к родителю до exec установленного binary.
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """Execute only parent-supplied Caddy operations after the private startup gate.

    :return: Fixed failure status if ownership or exec fails.
    :rtype: int
    """

    try:
        parent_guard(int(sys.argv[1]))
        raw = sys.stdin.buffer.readline(65537)
        if len(raw) > 65536 or not raw.endswith(b"\n"):
            return 1
        value = json.loads(raw)
        # Это внутренний pipe, не CLI для произвольных команд из deployment JSON.
        arguments = value["arguments"]
        if arguments[1] not in ("adapt", "run"):
            return 1
        if os.name == "nt":
            # Windows exec не сохраняет наблюдаемый PID. Wrapper ждёт binary,
            # а оба процесса наследуют Job; закрытие владельца завершает всю группу.
            return subprocess.call(arguments, stdin=subprocess.DEVNULL, close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        os.execv(arguments[0], arguments)
    except BaseException:
        return 1
    return 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
