# Запуск отдельного командного gateway из JSON без пользовательского Python-кода.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-184110
#
# Классы:
# -> CommandParser: Разбор CLI без раскрытия ошибочных значений.
#    Интерфейс:
#    -> error(): Безопасное сообщение об ошибке CLI.
#
# Функции:
# -> serve(): Работа gateway до остановки или отказа источника.
# -> main(): Разбор параметров и явный запуск выбранного режима.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import ssl
import sys
from collections.abc import Sequence
from pathlib import Path

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

        :param message: Typed request for the selected command operation.
        :type message: str
        """

        # message — типизированное сообщение выбранной командной операции.

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
        while not stop.is_set():
            if stop_file is not None and stop_file.exists():
                break
            if any(source.stats.closed for source in gateway.sources):
                raise RuntimeError("command source stopped")
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
            raise ValueError("both TLS certificate and key required")
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
        field = error.field if isinstance(error, CommandConfigError) else stage
        code = "dependency_missing" if isinstance(error, ImportError) else "operation_failed"
        print(f"Ошибка command gateway: code={code} field={field}. Значения и детали скрыты.", flush=True)
        return 1
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Явный запуск CLI
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
