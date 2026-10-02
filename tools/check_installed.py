# Проверка установленного пакета без импорта исходников из checkout.
#
# Version 1.0.8
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-184007
#
# Функции:
# -> main(): Запуск воспроизводимой проверки.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import logging
import subprocess
import sys
import threading
from pathlib import Path


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запуск воспроизводимой проверки
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """Check an isolated installed distribution before adding test dependencies.

    :return: Zero on success, otherwise a documented nonzero process exit code.
    :rtype: int
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("core", "extras"))
    parser.add_argument("version")
    args = parser.parse_args()
    threads, handlers = tuple(threading.enumerate()), tuple(logging.getLogger().handlers)

    # -I и отдельный venv исключают src и пользовательские site-packages.
    import remote_watch
    from remote_watch.adapters.command_http import HttpsCommandTransport
    from remote_watch.adapters.ntfy import NtfyChannel, NtfyConfig
    from remote_watch.adapters.ntfy_commands import NtfyCommandProvider
    from remote_watch.adapters.relay import RelayChannel, RelayConfig
    from remote_watch.adapters.telegram import TelegramChannel, TelegramConfig
    from remote_watch.adapters.telegram_commands import TelegramCommandProvider
    from remote_watch.adapters.time_source import HttpsDateTimeSource
    from remote_watch.adapters.timeapi import TimeApiTimeSource
    from remote_watch.commands import CommandRegistry
    from remote_watch.commands._confirmation import CommandChallenge
    from remote_watch.commands.client import CommandClient
    from remote_watch.commands.dispatcher import CommandDispatcher, DispatcherStats
    from remote_watch.commands.hub import CommandHub
    from remote_watch.commands.protocol import CommandRegistration, decode_command
    from remote_watch.commands.source import CommandSourceRunner
    from remote_watch.commands.sqlite_store import SQLiteCommandStore
    from remote_watch.commands.state import CommandRecord
    from remote_watch.commands.storage import CommandStore
    from remote_watch.commands.time import TrustedClock
    from remote_watch.gateway import Gateway as PublicGateway
    from remote_watch.gateway.command_server import CommandHubServer
    from remote_watch.gateway.command_service import CommandGateway
    from remote_watch.gateway.json_config import load_gateway_config
    from remote_watch.gateway.server import Gateway

    assert Path(sys.prefix).resolve() in Path(remote_watch.__file__).resolve().parents
    assert Path(remote_watch.__file__).with_name("py.typed").is_file()
    assert importlib.metadata.version("remote-watch") == args.version
    assert "aiohttp" not in sys.modules
    assert tuple(threading.enumerate()) == threads
    assert tuple(logging.getLogger().handlers) == handlers
    assert importlib.util.find_spec("cryptography") is None
    assert (importlib.util.find_spec("aiohttp") is not None) == (args.mode == "extras")
    assert all("extra ==" in item for item in importlib.metadata.requires("remote-watch") or [])

    assert all(item is not None for item in (CommandChallenge, CommandRegistration, decode_command, CommandRecord))
    assert all(item is not None for item in (CommandStore, SQLiteCommandStore, TrustedClock, HttpsDateTimeSource))
    assert TimeApiTimeSource()._accuracy == 1.0
    assert CommandRegistry is remote_watch.CommandRegistry
    assert PublicGateway is Gateway
    assert all(item is not None for item in (CommandClient, CommandHub, CommandHubServer, HttpsCommandTransport))
    assert all(item is not None for item in (CommandDispatcher, DispatcherStats))
    assert all(item is not None for item in (
        CommandSourceRunner, TelegramCommandProvider, NtfyCommandProvider, CommandGateway))
    for removed in ("smoke", "field_smoke", "ntfy_diagnostic"):
        assert importlib.util.find_spec("remote_watch." + removed) is None

    # Проверяем только действующие точки запуска именно из установленного wheel.
    # --help не читает credentials и не выполняет сетевых запросов даже без extras.
    for module in (
        "gateway",
        "diagnostics.smoke", "diagnostics.field_smoke", "diagnostics.ntfy_diagnostic",
        "diagnostics.time_probe", "diagnostics.command_smoke", "gateway.commands", "gateway_server",
    ):
        result = subprocess.run(
            [sys.executable, "-I", "-m", "remote_watch." + module, "--help"],
            capture_output=True, timeout=15, check=False,
        )
        assert result.returncode == 0, (module, result.stderr.decode("utf-8", errors="replace"))

        # Воспроизводим западную системную кодировку Windows и на Linux.
        # -I исключает влияние PYTHONIOENCODING; CLI обязан настроить свой вывод.
        bootstrap = ("import runpy,sys; sys.stdout.reconfigure(encoding='cp1252'); "
                     "sys.stderr.reconfigure(encoding='cp1252'); sys.argv=[sys.argv[1], '--help']; "
                     "runpy.run_module(sys.argv[0], run_name='__main__')")
        result = subprocess.run(
            [sys.executable, "-I", "-c", bootstrap, "remote_watch." + module],
            capture_output=True, timeout=15, check=False,
        )
        assert result.returncode == 0, (module, result.stderr.decode("utf-8", errors="replace"))
        assert "--help" in result.stdout.decode("utf-8")

    # Импорт всех интерфейсов не должен сам создавать ресурсы или читать токены.
    assert all(item is not None for item in (RelayChannel, RelayConfig, TelegramChannel,
                                            TelegramConfig, Gateway, load_gateway_config))

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка необязательных зависимостей и очистки
    #--------------------------------------------------------------------------------------------------------------
    async def lifecycle() -> None:

        """Open and close an anonymous channel or verify its missing-extra error."""

        channel = NtfyChannel(NtfyConfig(topic="synthetic", token_env=None))
        try:
            if args.mode == "core":
                try:
                    await channel.open()
                except ImportError:
                    pass
                else:
                    raise AssertionError("core unexpectedly opened an HTTP client")
            else:
                await channel.open()
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(lifecycle())
    print("Installed distribution, lazy imports and optional dependencies: OK")
    return 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явный запуск проверки
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
