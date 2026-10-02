# Локальная проверка deployment перед реализацией управления процессами.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-184007
#
# Классы:
# -> DeploymentParser: Разбор CLI без раскрытия неизвестных значений.
#    Интерфейс:
#    -> error(): Безопасный отказ при ошибке аргументов.
#
# Функции:
# -> main(): Проверка связанных конфигов без запуска процессов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import DeploymentConfigError, load_deployment_config


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Разбор CLI без раскрытия неизвестных значений
#------------------------------------------------------------------------------------------------------------------
class DeploymentParser(argparse.ArgumentParser):

    """Do not echo unknown arguments that may accidentally contain secrets."""

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Безопасный отказ при ошибке аргументов
    #--------------------------------------------------------------------------------------------------------------
    def error(
        self,
        message: str,
    ) -> None:

        """Report a fixed usage failure.

        :param message: Original argparse diagnostic, deliberately not printed.
        :type message: str
        """

        # message — текст argparse может содержать ошибочно переданный токен.

        self.exit(2, "Неверные параметры gateway server. Используйте --help.\n")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка связанных конфигов без запуска процессов
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Validate deployment configuration without claiming that a server has started.

    :param argv: Explicit CLI arguments, or None to read process arguments.
    :type argv: Sequence[str] | None

    :return: Zero on successful validation, one on a private configuration failure.
    :rtype: int
    """

    # argv — аргументы запуска; режим проверки обязателен до появления supervisor.

    parser = DeploymentParser(description="Gateway server 0.4.1: локальная проверка общего конфига.")
    parser.add_argument("--config", required=True, type=Path, help="Общий JSON-файл deployment.")
    parser.add_argument("--check-config", required=True, action="store_true",
                        help="Проверить настройки; запуск процессов появится в 0.4.2.")
    args = parser.parse_args(argv)

    try:
        load_deployment_config(args.config)
    except Exception as error:
        code = error.code if isinstance(error, DeploymentConfigError) else "operation_failed"
        field_name = error.field if isinstance(error, DeploymentConfigError) else "config"
        print(f"Ошибка gateway server: code={code} field={field_name}. Значения и детали скрыты.", flush=True)
        return 1

    print("Структура deployment и конфиги компонентов корректны. Процессы не запускались; "
          "окружение, доступность портов, Caddy/TLS и провайдеры не проверялись.", flush=True)
    return 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явная локальная проверка конфигурации
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
