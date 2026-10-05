# Запуск отдельного командного gateway из JSON без пользовательского Python-кода.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-144006
#
# Классы:
# -> CommandParser: Разбор CLI без раскрытия ошибочных значений.
#    Интерфейс:
#    -> error(): Безопасное сообщение об ошибке CLI.
#
# Функции:
# -> serve(): Работа gateway до остановки или отказа источника.
# -> main(): Разбор параметров и явный запуск выбранного режима.
# -> _version(): Версия установленного пакета без приватных путей.
# -> _failure_details(): Причина отказа и подсказка без исходного текста исключения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import asyncio
import errno
import json
import signal
import ssl
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from remote_watch.commands.transport import CommandError
from remote_watch.gateway.command_config import CommandConfigError, check_command_gateway, load_command_gateway
from remote_watch.gateway.command_service import CommandGateway


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Разбор CLI без раскрытия ошибочных значений
#------------------------------------------------------------------------------------------------------------------
class CommandParser(argparse.ArgumentParser):
    """Report fixed CLI failures without echoing values that may contain credentials."""


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Безопасное сообщение об ошибке CLI
    #--------------------------------------------------------------------------------------------------------------
    def error(
        self,
        message: str,
    ) -> None:

        """Reject invalid arguments without including their content in stderr.

        :param message: Argument parser error text, deliberately omitted from public output.
        :type message: str
        """

        # message — исходная ошибка argparse; она может содержать приватный аргумент.

        self.exit(2, "Неверные параметры командного gateway. Используйте --help.\n")
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Работа gateway до остановки или отказа источника
#------------------------------------------------------------------------------------------------------------------
async def serve(
    gateway: CommandGateway,
    *,
    host: str,
    port: int,
    context: ssl.SSLContext | None,
    allow_loopback_http: bool,
    stop_file: Path | None,
) -> None:

    """Run the explicit command service until interruption, source failure or a local stop file.

    :param gateway: Command service owning its listener and source readers.
    :type gateway: CommandGateway

    :param host: Explicit listener address.
    :type host: str

    :param port: Listener port; zero requests a free local port.
    :type port: int

    :param context: Server TLS context, or None for an explicit loopback test.
    :type context: ssl.SSLContext | None

    :param allow_loopback_http: Explicit permission for plaintext numeric loopback only.
    :type allow_loopback_http: bool

    :param stop_file: Optional stop signal path; it must be absent at startup.
    :type stop_file: Path | None
    """

    # gateway — командный сервер, владеющий слушателем и источниками.
    # host — явный адрес прослушивания сервера.
    # port — порт сервера; ноль выбирает свободный локальный порт.
    # context — серверный TLS-контекст; None только для локального теста.
    # allow_loopback_http — явное разрешение HTTP только на числовом loopback-адресе.
    # stop_file — необязательный файл остановки; перед запуском он должен отсутствовать.

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal_installed = False

    try:
        if stop_file is not None and stop_file.exists():
            raise ValueError("stop file must be absent")
        try:
            loop.add_signal_handler(signal.SIGTERM, stop.set)
            signal_installed = True
        except NotImplementedError:
            pass
        await gateway.start(host=host, port=port, ssl_context=context, allow_loopback_http=allow_loopback_http)
        print(f"Command gateway запущен, TCP-порт {gateway.server.port}. Остановка: Ctrl+C.", flush=True)
        previous = None
        while not stop.is_set():
            if stop_file is not None and stop_file.exists():
                break
            stats = gateway.stats()
            status = {key: stats[key] for key in ("ready", "reason", "fatal")}
            # Печатаем только смену состояния, а не очередную строку каждую секунду.
            # Временная потеря UTC оставляет loop обновления активным для восстановления.
            if status != previous:
                print(json.dumps({"kind": "command_gateway_status", **status}, sort_keys=True), flush=True)
                previous = status
            if stats["fatal"]:
                raise RuntimeError("command service stopped")
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except asyncio.TimeoutError:
                pass
    finally:
        try:
            await gateway.close()
        finally:
            if signal_installed:
                loop.remove_signal_handler(signal.SIGTERM)
            summary = {"kind": "command_gateway_summary", "stats": gateway.stats()}
            print(json.dumps(summary, sort_keys=True), flush=True)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Разбор параметров и явный запуск выбранного режима
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Validate or run a separate command-only gateway with explicit JSON configuration and TLS.

    :param argv: Explicit CLI arguments, or None for the process arguments.
    :type argv: Sequence[str] | None

    :return: Process exit status: zero for a completed successful check.
    :rtype: int
    """

    # argv — параметры CLI; None читает аргументы процесса.

    parser = CommandParser(description="Команды приложений через Telegram и закрытые топики ntfy.")
    parser.add_argument("--version", action="version", version="remote-watch " + _version())
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--stop-file", type=Path)
    args = parser.parse_args(argv)
    stage = "config"

    try:
        if args.check_config:
            check_command_gateway(args.config)
            print("Настройки команд корректны. Окружение, сертификаты, сеть и ACL провайдера не проверялись.")
            return 0
        config = load_command_gateway(args.config)
        stage = "tls"
        if bool(args.cert) != bool(args.key):
            print("Ошибка command gateway: code=tls_pair field=tls. "
                  "Укажите вместе --cert и --key либо уберите оба для HTTP за Caddy.", flush=True)
            return 1
        context = None
        if args.cert is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(args.cert, args.key)
        stage = "startup"
        gateway = CommandGateway(config)
        asyncio.run(serve(gateway, host=args.host, port=args.port, context=context,
                          allow_loopback_http=args.allow_loopback_http, stop_file=args.stop_file))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        code, field, hint = _failure_details(error, stage)
        print(f"Ошибка command gateway: code={code} field={field}. {hint} "
              f"Версия remote-watch: {_version()}. Значения настроек скрыты.", flush=True)
        return 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Версия установленного пакета без приватных путей
#------------------------------------------------------------------------------------------------------------------
def _version() -> str:

    """Read installed distribution metadata without exposing installation paths.

    :return: Installed version or a fixed label for an unpackaged source checkout.
    :rtype: str
    """

    try:
        return version("remote-watch")
    except PackageNotFoundError:
        return "unknown (source checkout)"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Причина отказа и подсказка без исходного текста исключения
#------------------------------------------------------------------------------------------------------------------
def _failure_details(
    error: Exception,
    stage: str,
) -> tuple[str, str, str]:

    """Classify failures by trusted types and numeric codes, never by exception messages.

    :param error: Failure caught at the CLI boundary.
    :type error: Exception

    :param stage: Fixed local phase in which the failure was observed.
    :type stage: str

    :return: Safe code, field and actionable Russian explanation.
    :rtype: tuple[str, str, str]
    """

    # error — исключение может содержать токен, URL, SQL или полный путь; str(error) запрещён.
    # stage — только известный этап CLI, не значение из JSON или сообщения провайдера.

    if isinstance(error, CommandConfigError):
        return error.code, error.field, error.hint
    field = stage if stage in ("config", "tls", "startup") else "startup"

    if isinstance(error, ImportError):
        return ("dependency_missing", field,
                "Установите remote-watch[commands] в тот же venv, которым запускаете сервер.")
    if isinstance(error, ssl.SSLError):
        return ("tls_invalid", field,
                "Проверьте формат PEM, соответствие сертификата ключу и параметры TLS. "
                "За Caddy используйте HTTP на loopback без --cert/--key.")
    if isinstance(error, PermissionError):
        return ("permission_denied", field,
                "Проверьте права учётной записи на конфиг, сертификаты и запись в state_dir; "
                "при открытии listener проверьте также ограничения ОС на порт.")
    if isinstance(error, FileNotFoundError):
        return ("file_missing", field,
                "Не найден требуемый файл. Для TLS проверьте --cert/--key; "
                "относительные пути этих аргументов считаются от текущей папки PowerShell.")
    if isinstance(error, OSError):
        if error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048:
            return ("address_in_use", field,
                    "Адрес и порт уже заняты. Проверьте прежний command gateway и --port; "
                    "порт notification gateway должен отличаться.")
        if error.errno == errno.EADDRNOTAVAIL or getattr(error, "winerror", None) == 10049:
            return ("address_unavailable", field,
                    "Адрес --host недоступен на этой машине. За локальным Caddy укажите 127.0.0.1.")

    # StoreWorker уже переводит ошибки журналов в фиксированные command-коды.
    # Conflict не доказывает наличие второго процесса: возможна несовместимость
    # owner/generation. Сохраняем это различие и не советуем удалять журнал.
    if isinstance(error, CommandError):
        hints = {
            "conflict": "Проверьте другой reader/владельца и соответствие state_dir прежней установке. "
                        "Не удаляйте locks или SQLite для обхода защиты.",
            "capacity": "Исчерпан предел журнала команд. Нужен разбор накопленного состояния без сброса UNKNOWN.",
            "unavailable": "Командный ресурс недоступен или операция превысила срок. "
                           "Проверьте доступность и права state_dir; сохраните предыдущий status/summary.",
            "busy": "Предыдущая операция с журналом ещё не завершилась. Проверьте нагрузку и состояние диска.",
            "denied": "Командный ресурс отклонил авторизацию. Проверьте отдельные командные токены и ACL.",
        }
        code = error.code if error.code in hints else "unavailable"
        return "command_" + code, field, hints[code]

    # Даже для непредусмотренного сбоя оставляем полезную категорию. Имена
    # пользовательских классов тоже не выводим: только закрытый набор типов.
    kinds = {KeyError: "KeyError", TypeError: "TypeError", ValueError: "ValueError",
             AttributeError: "AttributeError", RuntimeError: "RuntimeError", OSError: "OSError"}
    kind = kinds.get(type(error), "unexpected_exception")
    hint = (f"Необработанный сбой ({kind}). Выполните --check-config и сообщите эту строку с версией пакета. "
            "Если проверка прошла, проверьте параметры listener/TLS и доступность state_dir.")
    return "operation_failed", field, hint
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Явный запуск CLI
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
