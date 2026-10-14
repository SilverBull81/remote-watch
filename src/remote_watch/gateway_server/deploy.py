# Команда подготовки файлов gateway server без запуска и перезаписи установки.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-164146
#
# Функции:
# -> main(): Подготовка установки и вывод безопасного перечня изменений


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from remote_watch.gateway_server.__main__ import DeploymentParser
from remote_watch.gateway_server.deployment import DeployError, DeployOptions, deploy

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка установки и вывод безопасного перечня изменений
#------------------------------------------------------------------------------------------------------------------
def main(
    argv: Sequence[str] | None = None,
    ) -> int:

    """
    Generate an offline deployment kit or preview its safe file-level differences.

    :param argv: Explicit command arguments, or None to read the process arguments.
    :type argv: Sequence[str] | None

    :return: Zero after preparation, one on a private failure; argparse uses two for usage errors.
    :rtype: int
    """

    # argv — здесь нет токенов провайдеров; неизвестные аргументы не печатаются.

    parser = DeploymentParser(description="Подготовка gateway server без запуска и перезаписи файлов.")
    parser.add_argument("--version", action="version", version=version("remote-watch"))
    parser.add_argument("--directory", required=True, type=Path, help="Папка комплекта установки.")
    parser.add_argument("--endpoint", required=True, help="Внешний HTTPS-адрес, например https://lv.example:8443.")
    parser.add_argument("--python", type=Path, default=Path(sys.executable),
                        help="Python/venv для команд запуска.")
    parser.add_argument("--components", choices=("both", "notifications", "commands"), default="both")
    parser.add_argument("--notifications-config", type=Path, help="Существующий конфиг уведомлений без переноса.")
    parser.add_argument("--commands-config", type=Path, help="Существующий конфиг команд: оставить на месте.")
    parser.add_argument("--notification-port", type=int, default=8765)
    parser.add_argument("--command-port", type=int, default=8766)
    parser.add_argument("--caddy-mode", choices=("external", "managed"), default="external")
    parser.add_argument("--caddy-executable", type=Path, help="Только managed: установленный локальный Caddy.")
    parser.add_argument("--caddy-config", type=Path, help="Только managed: прежний Caddyfile без копирования.")
    parser.add_argument("--caddy-data", type=Path, help="Только managed: явное постоянное хранилище CA.")
    parser.add_argument("--ui", choices=("headless", "tray"), default="headless")
    parser.add_argument("--dry-run", action="store_true", help="Только сравнить; не создавать даже папку.")
    parser.add_argument("--update-templates", action="store_true",
                        help="Новые образцы в templates/версия, без замены.")
    args = vars(parser.parse_args(argv))
    dry_run, update = args.pop("dry_run"), args.pop("update_templates")
    try:
        report = deploy(DeployOptions(**args), dry_run=dry_run, update_templates=update)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        codes = {"linked_path", "directory_invalid", "reference_missing", "options_invalid", "port_invalid",
                 "port_conflict", "version_invalid", "managed_paths_required", "external_paths_unused",
                 "disabled_reference", "caddy_path_invalid", "destination_invalid"}
        code = str(error) if type(error) is DeployError and str(error) in codes else "deployment_failed"
        print(f"Ошибка deploy: code={code}. Проверьте параметры, наличие файлов и права доступа; значения скрыты.")
        return 1

    for name, state in report:
        print(f"{state}: {name}")

    print("Предварительный просмотр: файлы не записаны." if dry_run else
          "Подготовка завершена. Откройте START_HERE.md; серверы не запускались.")
    return 0
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явная подготовка файлов без запуска сервера
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
