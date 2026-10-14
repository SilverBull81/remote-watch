# Запуск gateway server, проверка настроек, локальное состояние и остановка.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-124120
#
# Классы:
# -> DeploymentParser: Разбор CLI без раскрытия неизвестных значений.
#    Интерфейс:
#    -> error(): Безопасный отказ при ошибке аргументов.
#
# Функции:
# -> main(): Выбор проверки, запуска либо локального управления deployment.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from .config import DeploymentConfigError, load_control_directory, load_deployment_config


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
# ФУНКЦИЯ : Выбор проверки, запуска либо локального управления deployment
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Select explicit local validation, foreground supervision, status or stop.

    :param argv: Explicit CLI arguments, or None to read process arguments.
    :type argv: Sequence[str] | None

    :return: Zero on success, one on a safely reported configuration or operational failure.
    :rtype: int
    """

    # argv — режим обязателен, случайный вызов без --start не запускает серверы.

    parser = DeploymentParser(description="Gateway server: управление процессами и локальный статус.")
    parser.add_argument("--version", action="version", version=version("remote-watch"))
    parser.add_argument("--config", required=True, type=Path, help="Общий JSON-файл deployment.")
    parser.add_argument("--headless", action="store_true", help="Запустить --start без UI, например из systemd.")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check-config", action="store_true", help="Проверить настройки без запуска.")
    actions.add_argument("--start", action="store_true", help="Работать до Ctrl+C либо --stop.")
    actions.add_argument("--status", action="store_true", help="Прочитать локальный статус manager.")
    actions.add_argument("--stop", action="store_true", help="Запросить общую остановку manager.")
    args = parser.parse_args(argv)
    if args.headless and not args.start:
        parser.error("headless")

    try:
        if args.status or args.stop:
            from .manager import control_request

            directory = load_control_directory(args.config)
            print(json.dumps(asyncio.run(control_request(directory, "stop" if args.stop else "status"))),
                  flush=True)
            return 0
        config = load_deployment_config(args.config)
        if args.headless:
            config = replace(config, application=replace(config.application, ui="headless"))
        if not args.check_config:
            from .manager import GatewayManager

            if args.start:
                print("Gateway server: запуск. Статус: --status; остановка: Ctrl+C или --stop.", flush=True)
                manager = GatewayManager(config)
                result = asyncio.run(manager.run())
                if result:
                    print(f"Gateway server: code=manager_failed reason={manager.reason}. "
                          "Проверьте служебный журнал в log_dir.", flush=True)
                return result
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        from remote_watch.commands.storage import StoreConflict

        code = error.code if isinstance(error, DeploymentConfigError) else "operation_failed"
        field_name = error.field if isinstance(error, DeploymentConfigError) else "config"
        if isinstance(error, StoreConflict):
            code, field_name = "ownership", "control"
        elif isinstance(error, ImportError):
            code, field_name = "dependency_missing", "runtime"
        elif args.status or args.stop:
            code, field_name = "control_unavailable", "control"
        # Связанный JSON имеет собственную схему: показываем её проверенный путь и подсказку,
        # а не исходное исключение, фрагмент файла или значения настроек.
        detail = ""
        if isinstance(error, DeploymentConfigError) and error.component_code is not None:
            detail = (f" component_code={error.component_code} component_field={error.component_field}. "
                      f"{error.hint}")
        print(f"Ошибка gateway server: code={code} field={field_name}.{detail} "
              f"Версия remote-watch: {version('remote-watch')}. Значения настроек скрыты.", flush=True)
        return 1

    print("Структура deployment и конфиги компонентов корректны. Процессы не запускались; "
          "окружение, доступность портов, Caddy/TLS и провайдеры не проверялись.", flush=True)
    return 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Управление deployment по явно выбранному режиму
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
