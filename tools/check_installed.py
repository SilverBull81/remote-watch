# Проверка установленного пакета без импорта исходников из checkout.
#
# Version 1.1.9
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Функции:
# -> main(): Запуск воспроизводимой проверки
#    Вложенные объявления:
#    -> lifecycle(): Проверка необязательных зависимостей и очистки


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import logging
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запуск воспроизводимой проверки
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """
    Check an isolated installed distribution before adding test dependencies.

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
    from remote_watch.adapters.notification_gateway import (
        NotificationGatewayChannel,
        NotificationGatewayClientConfig,
    )
    from remote_watch.adapters.ntfy_commands import NtfyCommandProvider
    from remote_watch.adapters.ntfy_notifications import NtfyChannel, NtfyConfig
    from remote_watch.adapters.telegram_commands import TelegramCommandProvider
    from remote_watch.adapters.telegram_notifications import TelegramChannel, TelegramConfig
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
    from remote_watch.gateway import NotificationGateway as PublicGateway
    from remote_watch.gateway.command_server import CommandHubServer
    from remote_watch.gateway.command_service import CommandGateway
    from remote_watch.gateway.notification_json_config import load_notification_gateway_config
    from remote_watch.gateway.notification_server import NotificationGateway
    from remote_watch.gateway_server import ApplicationConfig
    from remote_watch.gateway_server.manager import GatewayManager
    from remote_watch.gateway_server.tray import GatewayTray

    assert Path(sys.prefix).resolve() in Path(remote_watch.__file__).resolve().parents
    assert Path(remote_watch.__file__).with_name("py.typed").is_file()
    from remote_watch.gateway_server.tray import default_icon
    assert default_icon().is_file()
    assert not (Path(remote_watch.__file__).parent / "gateway_server/assets").exists()
    assert importlib.metadata.version("remote-watch") == args.version
    assert "aiohttp" not in sys.modules
    assert "pystray" not in sys.modules and "PIL" not in sys.modules
    from remote_watch.gateway_server.service import ServiceSpec

    assert ServiceSpec is not None and "win32service" not in sys.modules
    assert tuple(threading.enumerate()) == threads
    assert tuple(logging.getLogger().handlers) == handlers
    assert importlib.util.find_spec("cryptography") is None
    assert (importlib.util.find_spec("aiohttp") is not None) == (args.mode == "extras")
    # Установка новой версии не должна возвращать удалённые имена через файлы из старой сборки.
    for removed in (
        "remote_watch.adapters.relay", "remote_watch.relay_protocol",
        "remote_watch.notification_gateway_protocol", "remote_watch.gateway.server",
        "remote_watch.gateway.config", "remote_watch.gateway.json_config",
        "remote_watch.adapters.telegram", "remote_watch.adapters.ntfy",
    ):
        assert importlib.util.find_spec(removed) is None
    assert "relay" not in importlib.metadata.metadata("remote-watch").get_all("Provides-Extra", [])
    assert all("extra ==" in item for item in importlib.metadata.requires("remote-watch") or [])

    assert all(item is not None for item in (CommandChallenge, CommandRegistration, decode_command, CommandRecord))
    assert all(item is not None for item in (CommandStore, SQLiteCommandStore, TrustedClock, HttpsDateTimeSource))
    assert TimeApiTimeSource()._accuracy == 1.0
    assert CommandRegistry is remote_watch.CommandRegistry
    assert PublicGateway is NotificationGateway
    assert all(item is not None for item in (CommandClient, CommandHub, CommandHubServer, HttpsCommandTransport))
    assert all(item is not None for item in (CommandDispatcher, DispatcherStats))
    assert all(item is not None for item in (ApplicationConfig, GatewayManager, GatewayTray))
    if sys.platform == "win32":
        assert (importlib.util.find_spec("pystray") is not None) == (args.mode == "extras")
        assert (importlib.util.find_spec("win32service") is not None) == (args.mode == "extras")
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
        "gateway_server.deploy", "diagnostics.gateway_doctor", "diagnostics.command_resolution",
        "gateway_server.service",
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
    assert all(item is not None for item in (
        NotificationGatewayChannel, NotificationGatewayClientConfig, TelegramChannel,
        TelegramConfig, NotificationGateway, load_notification_gateway_config))

    # Deploy должен работать из wheel без docs/checkout, сети и optional providers.
    with tempfile.TemporaryDirectory(prefix="remote-watch-deploy-") as temporary:
        target = Path(temporary) / "installation & %UNEXPANDED% $literal"
        arguments = [sys.executable, "-I", "-m", "remote_watch.gateway_server.deploy",
                     "--directory", str(target), "--endpoint", "https://example.test:8443"]
        preview = subprocess.run(arguments + ["--dry-run"], cwd=temporary, capture_output=True, timeout=15)
        assert preview.returncode == 0 and not target.exists(), preview.stderr
        prepared = subprocess.run(arguments, cwd=temporary, capture_output=True, timeout=15)
        assert prepared.returncode == 0, prepared.stderr
        assert (target / "START_HERE.md").is_file() and (target / "check-config.ps1").is_file()
        from remote_watch.gateway_server.config import load_deployment_config
        deployment = load_deployment_config(target / "gateway_server.local.json")
        assert deployment.commands.port == 8766
        assert deployment.application.icon_path == target / "logo" / "remote_watch.ico"
        assert deployment.application.icon_path.read_bytes() == default_icon().read_bytes()

        # Doctor работает offline даже в core wheel и не разрешает token_env.
        from remote_watch.diagnostics.gateway_doctor import client_checks
        doctor_path = target / "clients" / "gateway_doctor.client.example.json"
        doctor_value = json.loads(doctor_path.read_text(encoding="utf-8"))
        doctor_value["ca_file"] = None
        doctor_path.write_text(json.dumps(doctor_value), encoding="utf-8")
        assert all(row["status"] != "failed" for row in client_checks(doctor_path, False, 1))
        assert (target / "doctor.ps1").is_file() and (target / "doctor.sh").is_file()
        assert (target / "SERVICE.md").is_file() and (target / "install-service.cmd").is_file()
        if sys.platform == "win32":
            # Запускаем CMD launcher из папки со спецсимволами. Явный фиктивный
            # SID позволяет dry-run работать и без optional pywin32. SCM/HKCU/ACL
            # и production процессы не затрагиваются.
            service_preview = subprocess.run(["cmd.exe", "/d", "/c", "install-service.cmd", "--dry-run",
                "--operator-sid", "S-1-5-21-101-202-303-1001"], cwd=target,
                capture_output=True, timeout=15)
            assert service_preview.returncode == 0, service_preview.stdout + service_preview.stderr
            assert json.loads(service_preview.stdout)["writes"] is False

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка необязательных зависимостей и очистки
    #--------------------------------------------------------------------------------------------------------------
    async def lifecycle() -> None:

        """
        Open and close an anonymous channel or verify its missing-extra error.
        """

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

# ТОЧКА ВХОДА : Явный запуск проверки
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
