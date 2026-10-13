# Проверки командного процесса под manager и восстановления источника без живых провайдеров.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261013-124741
#
# Тесты:
# -> test_command_process(): Настоящий hub и SQLite с подставным временем и входящим провайдером.
# -> test_source_current_health(): Восстановление приёма не стирает независимый отказ доставки ответа.
# -> test_stop_without_component_config(): Локальный stop не зависит от исправности файлов компонентов.
# -> test_operator_runtime(): Оповещения работают в независимом runtime без gateway.
# -> test_operator_delivery(): Служебные сообщения двум подставным HTTP провайдерам.
# -> test_tray_stop(): Трей передаёт Stop в общий lifecycle.
# -> test_log_failure(): Ошибка журнала видна без раскрытия исключения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from test_command_hub import Rig
from test_command_sources import FakeProvider
from test_gateway_manager import setup_deployment as _setup_deployment
from test_gateway_manager import wait_state

from remote_watch.commands.source import CommandSourceRunner
from remote_watch.commands.source_protocol import SourceTargets
from remote_watch.commands.source_store import SourceJournal
from remote_watch.commands.transport import CommandError
from remote_watch.gateway_server.application import OperatorAlerts, application_config
from remote_watch.gateway_server.config import ComponentConfig, DeploymentConfig, load_control_directory
from remote_watch.gateway_server.manager import GatewayManager, StatusLog, control_request
from remote_watch.gateway_server.processes import ProcessGroup
from remote_watch.gateway_server.tray import GatewayTray

setup_deployment = _setup_deployment


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящий hub и SQLite с подставным временем и входящим провайдером.
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("both", [False, True])
def test_command_process(
    setup_deployment: DeploymentConfig,
    monkeypatch: pytest.MonkeyPatch,
    both: bool,
) -> None:

    """Exercise real command child listeners and journals with only time/provider network calls replaced.

    :param setup_deployment: Synthetic parent settings.
    :type setup_deployment: DeploymentConfig

    :param monkeypatch: Trusted test-only bootstrap override.
    :type monkeypatch: pytest.MonkeyPatch

    :param both: Enable notifications alongside commands.
    :type both: bool
    """

    # setup_deployment/monkeypatch/both — продукт не получает механизма выбора произвольного bootstrap.
    pytest.importorskip("aiohttp")
    base = setup_deployment.control_dir.parent
    config_path = base / "commands.json"
    example = Path(__file__).resolve().parents[2] / "docs/examples/command_gateway.telegram.example.json"
    config_path.write_bytes(example.read_bytes())
    script = base / "command_test_child.py"
    script.write_text("""import asyncio,sys,time
from remote_watch.adapters.timeapi import TimeApiTimeSource
from remote_watch.adapters.telegram_commands import TelegramCommandProvider
from remote_watch.commands.time import TimeSample
from remote_watch.gateway_server.child import main
async def sample(self):
    now=time.time()
    return TimeSample(lower_utc=now-0.01,upper_utc=now+0.01,observed_at=time.monotonic())
async def opened(self): return 'synthetic-provider-owner'
async def poll(self,*args,**kwargs):
    await asyncio.sleep(.05)
    return ()
TimeApiTimeSource.sample=sample
TelegramCommandProvider.open=opened
TelegramCommandProvider.poll=poll
raise SystemExit(main())
""", encoding="utf-8")
    original = ProcessGroup.spawn

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Запуск процесса с закрытым стартовым каналом
    #--------------------------------------------------------------------------------------------------------------
    def spawn(
        group: ProcessGroup,
        arguments: list[str],
        capture: bool = False,
    ) -> subprocess.Popen:

        """Retain real ownership and replace only external network edges in the child.

        :param group: OS process owner.
        :type group: ProcessGroup

        :param arguments: Original fixed argument vector.
        :type arguments: list[str]

        :param capture: Standard output mode.
        :type capture: bool

        :return: Real owned Python child.
        :rtype: subprocess.Popen
        """

        # group/arguments/capture — реальные hub, SQLite, listener и heartbeat не подменяются.
        return original(group, [arguments[0], str(script), arguments[-1]], capture)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(ProcessGroup, "spawn", spawn)
    config = replace(setup_deployment, notifications=setup_deployment.notifications if both else None,
                     commands=ComponentConfig(config_path, setup_deployment.notifications.port + 1))

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка жизненного цикла на отдельном event loop
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Restart the command hub while preserving its journals and the neighboring process."""

        manager = GatewayManager(config)
        task = asyncio.create_task(manager.run())
        try:
            await wait_state(manager, "ready")
            neighbor = manager.children["notifications"].process if both else None
            child = manager.children["commands"]
            old = child.process
            journals = set(base.rglob("*.sqlite"))
            assert len(journals) >= 2
            old.kill()
            end = monotonic() + 12
            while child.process is old or manager.state != "ready":
                assert monotonic() < end, manager.status()
                await asyncio.sleep(.05)
            assert manager.status()["command_sessions"] == "re_registration_required"
            assert set(base.rglob("*.sqlite")) == journals
            if both:
                assert manager.children["notifications"].process is neighbor and neighbor.poll() is None
        finally:
            manager.stop_event.set()
            await asyncio.wait_for(task, 6)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Восстановление приёма не стирает независимый отказ доставки ответа.
#------------------------------------------------------------------------------------------------------------------
def test_source_current_health(tmp_path: Path) -> None:

    """Keep historical incidents while independently clearing current receive and reply failures.

    :param tmp_path: Isolated real SQLite directory.
    :type tmp_path: Path
    """

    # tmp_path — время и провайдер подставные; loops и журнал настоящие.
    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка жизненного цикла на отдельном event loop
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Observe temporary busy and recovery while reply storage remains unavailable."""

        rig = Rig(tmp_path)
        await rig.start()
        provider = FakeProvider()
        runner = CommandSourceRunner(rig.hub, rig.hub.config.sources[0],
            SourceTargets(aliases={"loader": rig.identity}), provider,
            SourceJournal(tmp_path / "source.sqlite"), retry_interval=.01)
        normal_results = rig.hub.results
        normal_poll = provider.poll
        provider.poll = AsyncMock(side_effect=CommandError("busy"))
        rig.hub.results = AsyncMock(side_effect=CommandError("unavailable"))
        try:
            await runner.start()
            end = monotonic() + 3
            while runner.stats.receive_error != "busy" or runner.stats.reply_error != "unavailable":
                assert monotonic() < end
                await asyncio.sleep(.01)
            assert not runner.stats.ready
            provider.poll = normal_poll
            end = monotonic() + 3
            while runner.stats.receive_error is not None:
                assert monotonic() < end
                await asyncio.sleep(.01)
            assert not runner.stats.ready and runner.stats.reply_error == "unavailable"
            rig.hub.results = normal_results
            end = monotonic() + 3
            while not runner.stats.ready:
                assert monotonic() < end
                await asyncio.sleep(.01)
            assert runner.stats.last_error in ("busy", "unavailable")
        finally:
            await runner.close()
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Локальный stop не зависит от исправности файлов компонентов.
#------------------------------------------------------------------------------------------------------------------
def test_stop_without_component_config(setup_deployment: DeploymentConfig) -> None:

    """Allow local control after a linked file is removed during an active deployment.

    :param setup_deployment: Synthetic real notification gateway.
    :type setup_deployment: DeploymentConfig
    """

    # setup_deployment — локальная команда не должна перечитывать секреты компонентов.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка жизненного цикла на отдельном event loop
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Read status and request common shutdown after removing the component JSON."""

        manager = GatewayManager(setup_deployment)
        task = asyncio.create_task(manager.run())
        try:
            await wait_state(manager, "ready")
            setup_deployment.notifications.config.unlink()
            file = setup_deployment.control_dir.parent / "deployment.json"
            file.write_text(json.dumps({"schema_version": 1, "control_dir": "control"}), encoding="utf-8")
            directory = load_control_directory(file)
            assert (await control_request(directory, "status"))["state"] == "ready"
            assert (await control_request(directory, "stop"))["state"] == "stopping"
        finally:
            manager.stop_event.set()
            assert await asyncio.wait_for(task, 5) == 0
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Оповещения работают в независимом runtime без gateway.
#------------------------------------------------------------------------------------------------------------------
def test_operator_runtime() -> None:

    """Start and close the independent operator runtime without gateway listeners or sends."""

    pytest.importorskip("aiohttp")
    config = application_config({"alerts": {"destinations": [{"alias": "owner", "provider": "ntfy",
        "settings": {"endpoint": "https://example.invalid", "topic": "synthetic", "token_env": None}}]}})

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка жизненного цикла на отдельном event loop
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Open direct provider resources without publishing any live notification."""

        alerts = OperatorAlerts(config)
        try:
            await alerts.start()
            assert alerts.watcher is not None
            assert alerts.stats()["attempts"] == 0
        finally:
            await alerts.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Прямая отправка служебного сообщения двум подставным провайдерам
#------------------------------------------------------------------------------------------------------------------
def test_operator_delivery() -> None:

    """Send owner failure and recovery notices without a notification gateway process."""

    pytest.importorskip("aiohttp")
    from aiohttp import web

    # Настоящий HTTP только на loopback; ни credentials, ни сообщения в Интернет не уходят.
    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Доставка через независимый runtime и два локальных HTTP назначения
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Accept synthetic Telegram/ntfy payloads and observe provider acceptance counters."""

        received = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Приём синтетического HTTP сообщения провайдера
        #----------------------------------------------------------------------------------------------------------
        async def publish(request: web.Request) -> web.Response:

            """Accept a provider request using an isolated test-only listener.

            :param request: Synthetic provider request.
            :type request: web.Request

            :return: A provider-shaped success response.
            :rtype: web.Response
            """

            # request — тело содержит только фиксированное служебное сообщение теста.
            received.append(await request.read())
            return web.json_response({"ok": True, "result": {"message_id": 1}, "id": "synthetic",
                                      "event": "message", "topic": "synthetic"})
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_post("/{tail:.*}", publish)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        endpoint = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
        config = application_config({"alerts": {"cooldown": 1, "destinations": [
            {"alias": "telegram", "provider": "telegram", "settings": {
                "endpoint": endpoint, "allow_http": True, "token": "12345:synthetic_token_for_tests",
                "chat_id": 123, "display_mode": "text"}},
            {"alias": "ntfy", "provider": "ntfy", "settings": {
                "endpoint": endpoint, "allow_http": True, "topic": "synthetic", "token_env": None,
                "display_mode": "text"}}]}})
        alerts = OperatorAlerts(config)
        try:
            await alerts.start()
            alerts.observe("commands", "degraded", "time_unavailable", 0)
            end = monotonic() + 5
            while alerts.stats()["accepted"] < 2:
                assert monotonic() < end, alerts.stats()
                await asyncio.sleep(.02)
            alerts.observe("commands", "ready", None, 1)
            while alerts.stats()["accepted"] < 4:
                assert monotonic() < end, alerts.stats()
                await asyncio.sleep(.02)
            assert len(received) == 4
            assert sum(b"time_unavailable" in body for body in received) == 2
        finally:
            await alerts.close()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Трей передаёт Stop в общий lifecycle без самостоятельного управления процессами
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows tray contract")
@pytest.mark.parametrize("custom", [False, True])
def test_tray_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    custom: bool,
) -> None:

    """Exercise tray lifecycle and menu wiring with a fake GUI backend and no visible window.

    :param monkeypatch: Replace optional GUI imports only within this test.
    :type monkeypatch: pytest.MonkeyPatch

    :param tmp_path: Isolated directory for a custom icon.
    :type tmp_path: Path

    :param custom: Select an explicit file instead of the bundled resource.
    :type custom: bool
    """

    # monkeypatch — тест не показывает пользователю окно и не требует установленного GUI extra.
    # tmp_path/custom — проверяем выбор файла и освобождение его дескриптора.
    ended = threading.Event()
    icon = Mock()
    icon.run.side_effect = lambda setup: (setup(icon), ended.wait(5))
    icon.stop.side_effect = ended.set
    pystray = SimpleNamespace(Icon=Mock(return_value=icon), Menu=Mock(), MenuItem=Mock())
    monkeypatch.setitem(sys.modules, "pystray", pystray)
    image = MagicMock()
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=image))
    source = tmp_path / "custom.ico" if custom else None
    if source is not None:
        source.write_bytes(b"synthetic icon")
    stop = Mock()
    tray = GatewayTray(stop, icon_path=source)
    try:
        tray.start()
        stream = image.open.call_args.args[0]
        assert stream.closed
        assert Path(stream.name) == source if custom else Path(stream.name).name == "remote_watch.ico"
        bitmap = image.open.return_value.__enter__.return_value.convert.return_value
        assert pystray.Icon.call_args.args[1] is bitmap
        tray.update("degraded")
        assert icon.title == "Remote Watch: degraded"
        pystray.MenuItem.call_args.args[1](icon, None)
        stop.assert_called_once_with()
    finally:
        tray.close()
    assert not tray._thread.is_alive()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибка служебного журнала видна без раскрытия исключения
#------------------------------------------------------------------------------------------------------------------
def test_log_failure(
    setup_deployment: DeploymentConfig,
    capsys: pytest.CaptureFixture,
) -> None:

    """Expose a latched file-write failure without logging its private exception text.

    :param setup_deployment: Synthetic configuration with an unused manager.
    :type setup_deployment: DeploymentConfig

    :param capsys: Captured console output.
    :type capsys: pytest.CaptureFixture
    """

    # setup_deployment/capsys — испорчена только тестовая запись, не реальные права или диск.
    manager = GatewayManager(setup_deployment)
    handler = StatusLog(setup_deployment.control_dir.parent / "synthetic.jsonl", maxBytes=4096, backupCount=1)
    handler.shouldRollover = Mock(side_effect=OSError("private-error-must-not-appear"))
    manager._log.addHandler(handler)
    manager._log_handler = handler
    try:
        manager._report()
        assert manager.status()["logging"] == "failed"
        assert not capsys.readouterr().err
    finally:
        handler.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Запускайте тесты через pytest.")
#------------------------------------------------------------------------------------------------------------------
