# Проверки владения процессами, heartbeat, локального управления и оповещений manager.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Функции:
# -> setup_deployment(): Настройки только с синтетическими адресами и credentials.
#
# -> wait_state(): Ограниченное ожидание состояния без жёсткого sleep.
#
# Тесты:
# -> test_real_notification_lifecycle(): Реальный listener, статус, перезапуск и остановка.
#    Вложенные объявления:
#    -> scenario(): Проверка жизненного цикла на отдельном event loop
#
# -> test_control_authentication(): Отказ чужому token, поколению и браузерному Origin.
#    Вложенные объявления:
#    -> scenario(): Проверка жизненного цикла на отдельном event loop
#
# -> test_hang_and_limit(): Зависший loop, подтверждение смерти и исчерпание бюджета.
#    Вложенные объявления:
#    -> spawn(): Запуск процесса с закрытым стартовым каналом
#    -> scenario(): Проверка жизненного цикла на отдельном event loop
#
# -> test_startup_rollback(): Отказ одного компонента закрывает уже запущенный соседний.
#
# -> test_os_owner_death(): Смерть родителя не оставляет серверный дочерний процесс.
#    Вложенные объявления:
#    -> scenario(): Проверка жизненного цикла на отдельном event loop
#
# -> test_application_and_alerts(): Прямая конфигурация и ограничение повторов владельцу.
#    Вложенные объявления:
#    -> Capture: Запись синтетических сообщений без обращения к провайдеру
#       Интерфейс:
#       -> emit(): Сохранение текста служебного сообщения в тесте
#
# -> test_caddy_storage(): Проверка каталога CA и admin без запуска Caddy.
#
# -> test_status_privacy(): Состояние не принимает произвольные строки/поля.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from time import monotonic

import pytest

import remote_watch
from remote_watch.gateway_server.application import ApplicationConfig, OperatorAlerts, application_config
from remote_watch.gateway_server.caddy import check_caddy
from remote_watch.gateway_server.config import ComponentConfig, DeploymentConfig, LifecycleConfig
from remote_watch.gateway_server.manager import GatewayManager, control_request
from remote_watch.gateway_server.messages import status_message
from remote_watch.gateway_server.policy import RestartPolicy
from remote_watch.gateway_server.processes import ProcessGroup

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Настройки только с синтетическими адресами и credentials.
#------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def setup_deployment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ) -> DeploymentConfig:

    """
    Create a real outbound-only ntfy gateway without performing any provider sends.

    :param tmp_path: Isolated scratch directory.
    :type tmp_path: Path

    :param monkeypatch: Child interpreter environment override for source and installed-wheel runs.
    :type monkeypatch: pytest.MonkeyPatch

    :return: Typed deployment referencing synthetic configuration only.
    :rtype: DeploymentConfig
    """

    # tmp_path/monkeypatch — подпроцесс импортирует тот же пакет, что проверяет pytest.
    monkeypatch.setenv("PYTHONPATH", str(Path(remote_watch.__file__).resolve().parent.parent))
    config = tmp_path / "notifications.json"
    config.write_text(json.dumps({"schema_version": 1,
        "destinations": [{"alias": "owner", "provider": "ntfy", "settings": {
            "endpoint": "https://example.invalid", "topic": "synthetic", "token_env": None}}],
        "principals": [{"name": "app", "token": "synthetic-token-that-is-long-enough-123456",
            "identity": {"service": "app", "environment": "test", "region": "test",
                         "host": "test", "instance_id": "one"}, "aliases": ["owner"]}]}), encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return DeploymentConfig(endpoint="https://example.invalid:8443", control_dir=tmp_path / "control",
                            log_dir=tmp_path / "logs", notifications=ComponentConfig(config, port),
                            lifecycle=LifecycleConfig(startup_timeout=8, heartbeat_interval=.1,
                                heartbeat_timeout=3, shutdown_timeout=1, kill_timeout=2),
                            restart=RestartPolicy(max_restarts=1, initial_delay=.1, max_delay=.1))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ограниченное ожидание состояния без жёсткого sleep.
#------------------------------------------------------------------------------------------------------------------
async def wait_state(
    manager: GatewayManager,
    state: str,
    timeout: float = 12,
    ) -> None:

    """
    Wait for a cached supervisor state with an explicit test deadline.

    :param manager: Running local manager.
    :type manager: GatewayManager

    :param state: Expected manager lifecycle state.
    :type state: str

    :param timeout: Finite wall-clock test budget.
    :type timeout: float
    """

    # manager/state/timeout — наблюдение состояния вместо предположения о скорости CI.
    end = monotonic() + timeout
    while manager.state != state:
        assert monotonic() < end, manager.status()
        await asyncio.sleep(.03)
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Реальный listener, статус, перезапуск и остановка.
#------------------------------------------------------------------------------------------------------------------
def test_real_notification_lifecycle(
    setup_deployment: DeploymentConfig,
    ) -> None:

    """
    Exercise real process ownership, provider-free HTTP startup and one bounded restart.

    :param setup_deployment: Synthetic ntfy deployment.
    :type setup_deployment: DeploymentConfig
    """

    # setup_deployment — провайдер не получает сообщений даже при падении процесса.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка жизненного цикла на отдельном event loop
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Run manager and operations on one event loop.
        """

        manager = GatewayManager(setup_deployment)
        task = asyncio.create_task(manager.run())
        try:
            await wait_state(manager, "ready")
            state = await control_request(setup_deployment, "status")
            assert state["ingress"] == "unverified" and state["caddy_mode"] == "external"
            child = manager.children["notifications"]
            first = child.process
            first.kill()
            end = monotonic() + 10
            while child.process is first or manager.state != "ready":
                assert monotonic() < end, manager.status()
                await asyncio.sleep(.03)
            assert first.poll() is not None and child.restarts == 1
            other = GatewayManager(setup_deployment)
            with pytest.raises(Exception):
                await other.run()
            assert (await control_request(setup_deployment, "status"))["state"] == "ready"
            await control_request(setup_deployment, "stop")
            assert await asyncio.wait_for(task, 5) == 0
            assert child.process.poll() is not None
            assert not (setup_deployment.control_dir / "runtime.json").exists()
            assert list(setup_deployment.log_dir.glob("*.jsonl"))
            with pytest.raises(RuntimeError, match="control_unavailable"):
                await control_request(setup_deployment, "stop")
        finally:
            manager.stop_event.set()
            await asyncio.wait_for(task, 5)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ чужому token, поколению и браузерному Origin.
#------------------------------------------------------------------------------------------------------------------
def test_control_authentication(
    setup_deployment: DeploymentConfig,
    ) -> None:

    """
    Reject unauthenticated, stale, oversized and browser requests without state changes.

    :param setup_deployment: Synthetic local deployment.
    :type setup_deployment: DeploymentConfig
    """

    # setup_deployment — токены только синтетические, в диагностике теста не выводятся.
    aiohttp = pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка жизненного цикла на отдельном event loop
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Send bounded adversarial requests against a real loopback control listener.
        """

        manager = GatewayManager(setup_deployment)
        task = asyncio.create_task(manager.run())
        try:
            await wait_state(manager, "ready")
            record = json.loads((setup_deployment.control_dir / "runtime.json").read_text())
            async with aiohttp.ClientSession() as http:
                url = f"http://127.0.0.1:{record['port']}/control"
                for token, generation, origin, expected in [
                    ("wrong", record["generation"], None, 403),
                    (record["token"], "stale", None, 409),
                    (record["token"], record["generation"], "https://example.invalid", 403)]:
                    headers = {"Authorization": "Bearer " + token}
                    if origin:
                        headers["Origin"] = origin
                    async with http.post(url, json={"action": "stop", "generation": generation},
                                         headers=headers) as response:
                        assert response.status == expected
                async with http.post(url, data=b"x" * 5000,
                                     headers={"Authorization": "Bearer " + record["token"]}) as response:
                    assert response.status == 413
                child_url = url.replace("/control", "/child/notifications")
                async with http.post(child_url, json={},
                                     headers={"Authorization": "Bearer " + record["token"]}) as response:
                    assert response.status == 403
            assert manager.state == "ready" and not manager.stop_event.is_set()
        finally:
            manager.stop_event.set()
            await asyncio.wait_for(task, 5)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Зависший loop, подтверждение смерти и исчерпание бюджета.
#------------------------------------------------------------------------------------------------------------------
def test_hang_and_limit(
    setup_deployment: DeploymentConfig,
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """
    Stop a nonresponsive loop before replacement and latch restart exhaustion.

    :param setup_deployment: Isolated process settings.
    :type setup_deployment: DeploymentConfig

    :param monkeypatch: Trusted test-only bootstrap substitution.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # setup_deployment/monkeypatch — bootstrap заменяется только в Python теста, не через JSON продукта.
    pytest.importorskip("aiohttp")
    script = setup_deployment.control_dir.parent / "fake.py"
    script.write_text("""import asyncio,json,sys,time,urllib.request
from remote_watch.gateway_server.processes import parent_guard
parent_guard(int(sys.argv[1]))
s=json.loads(sys.stdin.buffer.readline())
m={'state':'ready','reason':None,'time_reason':None,'failure':None,'counters':{}}
u=f"http://127.0.0.1:{s['control_port']}/child/{s['name']}"
r=urllib.request.Request(u,data=json.dumps(m).encode(),headers={'Authorization':'Bearer '+s['token']})
urllib.request.urlopen(r,timeout=3).close()
time.sleep(60)
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

        """
        Replace only the trusted component bootstrap in this fault-injection test.

        :param group: Existing owned process group.
        :type group: ProcessGroup

        :param arguments: Original fixed bootstrap arguments.
        :type arguments: list[str]

        :param capture: Requested stdout mode.
        :type capture: bool

        :return: Owned synthetic child.
        :rtype: subprocess.Popen
        """

        # group/arguments/capture — сохраняем настоящий OS owner и pipe-барьер.
        return original(group, [arguments[0], str(script), arguments[-1]], capture)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(ProcessGroup, "spawn", spawn)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка жизненного цикла на отдельном event loop
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Observe a hang, one replacement, and permanent recovery-budget exhaustion.
        """

        manager = GatewayManager(setup_deployment)
        task = asyncio.create_task(manager.run())
        try:
            await wait_state(manager, "ready")
            child = manager.children["notifications"]
            old = child.process
            end = monotonic() + 12
            while child.message["reason"] != "restart_limit":
                assert monotonic() < end, manager.status()
                await asyncio.sleep(.05)
            assert old.poll() is not None and child.process.poll() is not None
            assert child.restarts == 1 and manager.state == "degraded"
        finally:
            manager.stop_event.set()
            await asyncio.wait_for(task, 5)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ одного компонента закрывает уже запущенный соседний.
#------------------------------------------------------------------------------------------------------------------
def test_startup_rollback(
    setup_deployment: DeploymentConfig,
    ) -> None:

    """
    Never leave an outbound child after the second component fails configuration.

    :param setup_deployment: Synthetic valid outbound component.
    :type setup_deployment: DeploymentConfig
    """

    # setup_deployment — второй компонент заведомо не читает credentials и не обращается к сети.
    pytest.importorskip("aiohttp")
    bad = setup_deployment.control_dir.parent / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    config = replace(setup_deployment, commands=ComponentConfig(bad, setup_deployment.notifications.port + 1))
    manager = GatewayManager(config)
    assert asyncio.run(manager.run()) == 1
    assert all(child.process.poll() is not None for child in manager.children.values())
    assert not (config.control_dir / "runtime.json").exists()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Смерть родителя не оставляет серверный дочерний процесс.
#------------------------------------------------------------------------------------------------------------------
def test_os_owner_death(
    setup_deployment: DeploymentConfig,
    ) -> None:

    """
    Kill a real manager process and verify the operating system closes its child listener.

    :param setup_deployment: Synthetic deployment settings.
    :type setup_deployment: DeploymentConfig
    """

    # setup_deployment — проверка не ищет и не завершает никакие чужие процессы по PID.
    pytest.importorskip("aiohttp")
    path = setup_deployment.control_dir.parent / "deployment.json"
    path.write_text(json.dumps({"schema_version": 1, "endpoint": setup_deployment.endpoint,
        "control_dir": str(setup_deployment.control_dir), "log_dir": str(setup_deployment.log_dir),
        "notifications": {"config": str(setup_deployment.notifications.config),
                          "port": setup_deployment.notifications.port}}), encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "remote_watch.gateway_server", "--config", str(path),
                                "--start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Проверка жизненного цикла на отдельном event loop
        #----------------------------------------------------------------------------------------------------------
        async def scenario() -> None:

            """
            Wait for readiness through authenticated IPC, then verify port release after parent death.
            """

            end = monotonic() + 12
            while True:
                assert monotonic() < end and process.poll() is None
                try:
                    status = await control_request(setup_deployment, "status")
                    if status["state"] == "ready":
                        break
                except RuntimeError:
                    pass
                await asyncio.sleep(.05)
            process.kill()
            await asyncio.to_thread(process.wait, timeout=5)
            end = monotonic() + 5
            while True:
                with socket.socket() as probe:
                    probe.settimeout(.2)
                    if probe.connect_ex(("127.0.0.1", setup_deployment.notifications.port)) != 0:
                        break
                assert monotonic() < end
                await asyncio.sleep(.05)
            with pytest.raises(RuntimeError, match="control_unavailable"):
                await control_request(setup_deployment, "stop")
        #----------------------------------------------------------------------------------------------------------

        asyncio.run(scenario())
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Прямая конфигурация и ограничение повторов владельцу.
#------------------------------------------------------------------------------------------------------------------
def test_application_and_alerts() -> None:

    """
    Coalesce fixed component failures and recovery without duplicate alert floods.
    """

    config = application_config({"alerts": {"cooldown": 10, "destinations": [
        {"alias": "owner", "provider": "ntfy", "settings": {
            "endpoint": "https://example.invalid", "topic": "synthetic", "token_env": None}}]}})
    assert config.destinations[0].outstanding_capacity == 16
    alerts = OperatorAlerts(config)
    records = []

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Запись синтетических сообщений без обращения к провайдеру
    #--------------------------------------------------------------------------------------------------------------
    class Capture(logging.Handler):
        """
        Capture only local synthetic alert text.
        """

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Сохранение текста служебного сообщения в тесте
        #----------------------------------------------------------------------------------------------------------
        def emit(
            self,
            record: logging.LogRecord,
            ) -> None:

            """
            Retain formatted operator notices without network access.

            :param record: Fixed supervisor message.
            :type record: logging.LogRecord
            """

            # record — сообщения не содержат пользовательских идентификаторов.
            records.append(record.getMessage())
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    alerts.logger.addHandler(Capture())
    alerts.observe("commands", "degraded", "time_unavailable", 0)
    alerts.observe("commands", "degraded", "time_unavailable", 1)
    alerts.observe("commands", "ready", None, 2)
    assert len(records) == 1
    alerts.observe("commands", "ready", None, 10)
    assert len(records) == 2 and "ready" in records[-1]
    alerts.observe("commands", "failed", "secret-detail", 20)
    assert len(records) == 2
    with pytest.raises(ValueError):
        ApplicationConfig(ui="unknown")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка каталога CA и admin без запуска Caddy.
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change", ["missing", "wrong_root", "disabled", "wildcard", "hostname", "bad_port"])
def test_caddy_storage(
    tmp_path: Path,
    change: str,
    ) -> None:

    """
    Reject a different CA directory and public or ambiguous Caddy admin addresses.

    :param tmp_path: Synthetic expected storage root.
    :type tmp_path: Path

    :param change: Invalid effective-configuration variant.
    :type change: str
    """

    # tmp_path/change — только JSON, binary и CA никогда не открываются.
    config = {"storage": {"module": "file_system", "root": str(tmp_path)}}
    assert check_caddy(config, tmp_path) == "http://127.0.0.1:2019"
    if change == "missing":
        del config["storage"]
    elif change == "wrong_root":
        config["storage"]["root"] = str(tmp_path / "other")
    else:
        config["admin"] = {"disabled": True} if change == "disabled" else {"listen": {
            "wildcard": "0.0.0.0:2019", "hostname": "localhost:2019", "bad_port": "127.0.0.1:0"}[change]}
    with pytest.raises(ValueError):
        check_caddy(config, tmp_path)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Состояние не принимает произвольные строки/поля.
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key,value", [("reason", "secret"), ("time_reason", "secret"), ("state", []),
                                      ("failure", "secret"), ("counters", {"secret": 1})])
def test_status_privacy(
    key: str,
    value: object,
    ) -> None:

    """
    Reject arbitrary strings and structures before retaining IPC or operator output.

    :param key: Known field to corrupt.
    :type key: str

    :param value: Unsupported private payload.
    :type value: object
    """

    # key/value — провокационная строка не должна попасть в успешный статус.
    data = {"state": "ready", "reason": None, "time_reason": None, "failure": None, "counters": {}}
    data[key] = value
    with pytest.raises(ValueError):
        status_message(data)
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Запускайте тесты через pytest.")
#------------------------------------------------------------------------------------------------------------------
