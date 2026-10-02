# Проверки готовности командного gateway и безопасной диагностики отказов.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-143102
#
# Тесты:
# -> make_gateway(): Подготовка gateway с подставными временем и провайдером.
# -> test_maintenance_failure(): Закрытие работы после отказа, отмены или раннего выхода обслуживания.
# -> test_time_readiness(): Потеря, истечение и восстановление доверия ко времени.
# -> test_storage_readiness(): Отличие занятости журнала от временного отказа обслуживания.
# -> test_source_cancellation(): Неожиданная отмена каждого из двух циклов источника.
# -> test_gateway_cli_health(): Состояние готовности и завершение CLI при критическом отказе.
# -> test_config_reasons(): Точные безопасные причины серверных ошибок.
# -> test_client_config_reasons(): Причины клиентских ошибок без открытия журнала.
# -> test_error_labels_are_private(): Запрет произвольных строк в диагностических полях.
# -> test_resolved_duplicate_tokens(): Совпадение значений разных переменных обнаруживается при загрузке.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from test_command_dispatcher import until
from test_command_gateway_config import FakeTime, settings, write_config
from test_command_sources import FakeProvider

from remote_watch import CommandRegistry
from remote_watch.commands.transport import CommandError
from remote_watch.diagnostics.command_smoke import main as smoke_main
from remote_watch.gateway.command_config import (
    CommandConfigError,
    check_command_gateway,
    load_command_client,
    load_command_gateway,
)
from remote_watch.gateway.command_service import CommandGateway
from remote_watch.gateway.commands import main, serve


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка gateway с подставными временем и провайдером
#------------------------------------------------------------------------------------------------------------------
def make_gateway(path: Path) -> CommandGateway:

    """Build a local test deployment without real credentials or network time.

    :param path: Isolated test directory for synthetic configuration and journals.
    :type path: Path

    :return: Unstarted gateway with fake provider and trusted time source.
    :rtype: CommandGateway
    """

    # path — отдельный каталог подставных настроек и журналов.
    config = load_command_gateway(write_config(path / "gateway.json", settings()))
    gateway = CommandGateway(config, time_source=FakeTime())
    gateway.sources[0].provider = FakeProvider()
    return gateway
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Закрытие работы после отказа, отмены или раннего выхода обслуживания
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["exception", "cancel", "return"])
def test_maintenance_failure(
    tmp_path: Path,
    mode: str,
) -> None:

    """Fence a failed hub immediately and consume private task exceptions.

    :param tmp_path: Isolated test directory.
    :type tmp_path: Path

    :param mode: Injected maintenance termination type.
    :type mode: str
    """

    # tmp_path — отдельный каталог теста.
    # mode — способ неожиданного завершения задачи.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Отказ обслуживания после успешного запуска
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Finish a gated maintenance task while the listener and source loops remain live."""

        gateway = make_gateway(tmp_path)
        gate = asyncio.Event()
        reports: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _, context: reports.append(context))

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Управляемое завершение фоновой задачи
        #----------------------------------------------------------------------------------------------------------
        async def maintenance() -> None:

            """Wait for the test gate and then return or fail with a private marker."""

            await gate.wait()
            if mode == "exception":
                raise RuntimeError("private-maintenance-payload")
        #----------------------------------------------------------------------------------------------------------

        gateway.hub._refresh_time = maintenance
        try:
            await gateway.start(port=0, allow_loopback_http=True)
            assert gateway.stats()["ready"]
            task = gateway.hub._refresh
            assert task is not None
            if mode == "cancel":
                task.cancel()
            else:
                gate.set()
            await until(task.done)
            # Даже до отдельного опроса health проверка запроса закрывает работу hub.
            with pytest.raises(CommandError, match="unavailable"):
                gateway.hub.authenticate("a" * 40)
            state = gateway.stats()
            assert state["fatal"] and not state["ready"]
            assert state["hub"]["reason"] == "maintenance_failed"
            assert "private-maintenance-payload" not in json.dumps(state)
            assert not task._log_traceback
            assert not reports
        finally:
            await gateway.close()
            loop.set_exception_handler(None)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Потеря, истечение и восстановление доверия ко времени
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["startup", "refresh", "expiry"])
def test_time_readiness(
    tmp_path: Path,
    mode: str,
) -> None:

    """Keep a time outage visible and recover without restarting the hub or changing its epoch.

    :param tmp_path: Isolated test directory.
    :type tmp_path: Path

    :param mode: Missing initial sample, failed refresh or expired sample.
    :type mode: str
    """

    # tmp_path — отдельный каталог теста.
    # mode — причина утраты доверенного времени.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Потеря показания и успешное обновление в том же запуске
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Observe readiness transitions without touching VM clocks or real providers."""

        gateway = make_gateway(tmp_path)
        source = gateway.hub._source
        original = source.sample
        failed = AsyncMock(side_effect=RuntimeError("private-time-payload"))
        if mode == "startup":
            source.sample = failed
        try:
            assert gateway.stats()["reason"] == "not_started"
            await gateway.start(port=0, allow_loopback_http=True)
            epoch = gateway.hub.epoch
            if mode == "refresh":
                source.sample = failed
                await gateway.hub._update_time()
            elif mode == "expiry":
                clock = gateway.hub._trusted
                sample = clock.bounds()
                clock._sample = replace(sample, observed_at=sample.observed_at - clock.policy.sample_ttl - 1)
            state = gateway.stats()
            assert state["reason"] == "time_unavailable"
            assert not state["ready"] and not state["fatal"]
            assert state["hub"]["maintenance_running"]
            assert gateway.hub._trusted.check(1000, epoch).deadline is None
            assert "private-time-payload" not in json.dumps(state)
            # Повторение health не запрашивает время, не открывает новый journal и не лечит отказ.
            calls = failed.await_count
            gateway.stats()
            assert failed.await_count == calls
            source.sample = original
            await gateway.hub._update_time()
            assert gateway.stats()["ready"]
            assert gateway.hub.epoch == epoch
        finally:
            await gateway.close()
        assert gateway.stats()["reason"] == "closed"
        assert not gateway.stats()["fatal"]
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отличие занятости журнала от временного отказа обслуживания
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("code", ["busy", "unavailable", "capacity"])
def test_storage_readiness(
    tmp_path: Path,
    code: str,
) -> None:

    """Expose maintenance storage errors until a successful sweep without restarting the hub.

    :param tmp_path: Isolated test directory.
    :type tmp_path: Path

    :param code: Synthetic storage failure classified by the worker.
    :type code: str
    """

    # tmp_path — отдельный каталог теста.
    # code — подставной код занятости или отказа журнала.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Отказ одного прохода обслуживания и восстановление
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Inject errors only in background sweeps and leave the source result path intact."""

        gateway = make_gateway(tmp_path)
        gateway.hub.config = replace(gateway.hub.config, refresh_interval=0.1)
        original = gateway.hub.sweep
        failed = True
        observed = asyncio.Event()

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной отказ только фоновой очистки
        #----------------------------------------------------------------------------------------------------------
        async def sweep() -> None:

            """Distinguish a timer sweep from the normal source result lookup."""

            if failed and asyncio.current_task() is gateway.hub._refresh:
                observed.set()
                raise CommandError(code)
            await original()
        #----------------------------------------------------------------------------------------------------------

        gateway.hub.sweep = sweep
        try:
            await gateway.start(port=0, allow_loopback_http=True)
            await asyncio.wait_for(observed.wait(), 5)
            expected = None if code == "busy" else "storage_" + code
            assert gateway.stats()["reason"] == expected
            assert not gateway.stats()["fatal"]
            failed = False
            await until(lambda: gateway.stats()["ready"])
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Неожиданная отмена каждого из двух циклов источника
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("mode", ["cancel", "return", "exception"])
def test_source_cancellation(
    tmp_path: Path,
    index: int,
    mode: str,
) -> None:

    """Report a cancelled receive or reply loop as fatal, while planned shutdown remains normal.

    :param tmp_path: Isolated test directory.
    :type tmp_path: Path

    :param index: Receive or reply task index.
    :type index: int

    :param mode: Unexpected task termination type.
    :type mode: str
    """

    # tmp_path — отдельный каталог теста.
    # index — номер отменяемого цикла источника.
    # mode — отмена, ранний выход или исключение в задаче.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Отмена активного источника
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Cancel one source task and inspect aggregate readiness."""

        gateway = make_gateway(tmp_path)
        gate = asyncio.Event()

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Завершение выбранного цикла после запуска
        #----------------------------------------------------------------------------------------------------------
        async def worker() -> None:

            """Wait for a deterministic failure trigger without accessing provider state."""

            await gate.wait()
            if mode == "exception":
                raise RuntimeError("private-source-failure")
        #----------------------------------------------------------------------------------------------------------

        if mode != "cancel":
            setattr(gateway.sources[0], "_receive" if index == 0 else "_respond", worker)
        try:
            await gateway.start(port=0, allow_loopback_http=True)
            task = gateway.sources[0]._tasks[index]
            if mode == "cancel":
                task.cancel()
            else:
                gate.set()
            await until(task.done)
            state = gateway.stats()
            assert state["fatal"] and not state["ready"]
            assert state["reason"] == "source_stopped"
            assert "private-source-failure" not in json.dumps(state)
            assert not task._log_traceback
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Состояние готовности и завершение CLI при критическом отказе
#------------------------------------------------------------------------------------------------------------------
def test_gateway_cli_health(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:

    """Publish readiness changes and terminate the command CLI after a critical task stops.

    :param tmp_path: Isolated test directory.
    :type tmp_path: Path

    :param capsys: Captured CLI output.
    :type capsys: pytest.CaptureFixture
    """

    # tmp_path — отдельный каталог теста.
    # capsys — перехваченные сообщения CLI без настоящих секретов.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Наблюдение отказа через действующий цикл CLI
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise the real serve loop and its cleanup, rather than only calling stats."""

        gateway = make_gateway(tmp_path)
        gateway.hub._source.sample = AsyncMock(side_effect=RuntimeError("private-time"))
        task = asyncio.create_task(serve(gateway, host="127.0.0.1", port=0, context=None,
                                        allow_loopback_http=True, stop_file=None))
        try:
            await until(lambda: gateway._started, timeout=10)
            gateway.hub._source = FakeTime()
            await gateway.hub._update_time()
            await until(lambda: '"ready": true' in capsys.readouterr().out, timeout=5)
            gateway.hub._refresh.cancel()
            with pytest.raises(RuntimeError, match="command service stopped"):
                await asyncio.wait_for(task, 10)
            output = capsys.readouterr().out
            assert '"fatal": true' in output and "maintenance_failed" in output
            assert "command_gateway_summary" in output
            assert "private-time" not in output
            assert gateway.stats()["closed"]
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Точные безопасные причины серверных ошибок
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode, code, field", [
    ("token", "duplicate_principal_token", "principals[1].token"),
    ("env", "duplicate_principal_token", "principals[1].token_env"),
    ("name", "duplicate_principal_name", "principals[1].name"),
    ("identity", "duplicate_identity", "principals[1].targets"),
    ("target", "unknown_target", "principals[1].targets"),
    ("unassigned", "unassigned_target", "targets"),
    ("source", "duplicate_source", "sources[1].source_id"),
    ("provider", "duplicate_provider", "sources[1].settings"),
    ("acl_target", "unknown_target", "sources[0].access[0].targets"),
    ("actor", "source_acl", "sources[0].access[0]"),
])
def test_config_reasons(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    mode: str,
    code: str,
    field: str,
) -> None:

    """Identify structural mistakes without reading environment secrets or echoing supplied values.

    :param tmp_path: Isolated test directory.
    :type tmp_path: Path

    :param capsys: Captured CLI output.
    :type capsys: pytest.CaptureFixture

    :param mode: Configuration mutation selected by the test.
    :type mode: str

    :param code: Expected fixed diagnostic code.
    :type code: str

    :param field: Expected safe schema path.
    :type field: str
    """

    # tmp_path — отдельный каталог теста.
    # capsys — перехваченный вывод проверки конфигурации.
    # mode — вносимая ошибка подставных настроек.
    # code — ожидаемая фиксированная причина.
    # field — ожидаемый путь схемы без пользовательских имён.
    raw = settings()
    first, second = raw["principals"]
    source = raw["sources"][0]
    if mode == "token":
        second["token"] = first["token"]
    elif mode == "env":
        for principal in (first, second):
            del principal["token"]
            principal["token_env"] = "PRIVATE_UNSET_ENVIRONMENT"
    elif mode == "name":
        second["name"] = first["name"]
    elif mode == "identity":
        second["targets"] = first["targets"]
    elif mode == "target":
        second["targets"] = ["private-target-value"]
    elif mode == "unassigned":
        raw["principals"].pop()
    elif mode in ("source", "provider"):
        raw["sources"].append({**source, "source_id": "telegram" if mode == "source" else "second"})
    elif mode == "acl_target":
        source["access"][0]["targets"] = ["private-target-value"]
    else:
        source["access"][0]["actor_id"] = "private-actor-value"
    path = write_config(tmp_path / "gateway.json", raw)
    with pytest.raises(CommandConfigError) as caught:
        check_command_gateway(path)
    assert (caught.value.code, caught.value.field) == (code, field)
    assert main(["--config", str(path), "--check-config"]) == 1
    output = capsys.readouterr().out
    assert f"code={code} field={field}" in output
    assert not any(value in output for value in ("a" * 40, "PRIVATE_UNSET", "private-target", "private-actor"))
    assert not (tmp_path / "state").exists()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Причины клиентских ошибок без открытия журнала
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode, code, field", [
    ("missing", "config_read", "config"),
    ("json", "config_json", "config"),
    ("schema", "config_schema", "schema"),
    ("ca_missing", "ca_read", "client.ca_file"),
    ("ca_invalid", "ca_invalid", "client.ca_file"),
    ("endpoint", "endpoint_invalid", "client.endpoint"),
])
def test_client_config_reasons(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    mode: str,
    code: str,
    field: str,
) -> None:

    """Keep file, CA and endpoint failures distinguishable in the public smoke CLI.

    :param tmp_path: Isolated test directory.
    :type tmp_path: Path

    :param capsys: Captured CLI output.
    :type capsys: pytest.CaptureFixture

    :param mode: Client configuration failure case.
    :type mode: str

    :param code: Expected diagnostic code.
    :type code: str

    :param field: Expected safe configuration field.
    :type field: str
    """

    # tmp_path — отдельный каталог теста.
    # capsys — перехваченный вывод клиентского smoke.
    # mode — ошибка клиентского файла настроек.
    # code — ожидаемая причина ошибки.
    # field — безопасное имя поля, не его значение.
    raw = {"schema_version": 1, "identity": settings()["targets"]["one"],
           "token": "a" * 40, "owner_id": "one", "state_file": "client.sqlite",
           "endpoint": "https://example.invalid:8443"}
    if mode == "schema":
        raw["schema_version"] = 2
    elif mode.startswith("ca_"):
        raw["ca_file"] = "private-ca.pem"
        if mode == "ca_invalid":
            (tmp_path / "private-ca.pem").write_text("not a certificate", encoding="utf-8")
    elif mode == "endpoint":
        raw["endpoint"] = "https://private-secret@example.invalid/private"
    path = tmp_path / "client.json"
    if mode != "missing":
        write_config(path, raw)
    if mode == "json":
        path.write_text('{"private-secret":', encoding="utf-8")
    with pytest.raises(CommandConfigError) as caught:
        load_command_client(path, CommandRegistry.from_callbacks({"status": lambda: "ok"}))
    assert (caught.value.code, caught.value.field) == (code, field)
    assert smoke_main(["--config", str(path)]) == 2
    output = capsys.readouterr().out
    assert f"code={code} field={field}" in output
    assert "private-secret" not in output and "private-ca" not in output and "a" * 40 not in output
    assert not (tmp_path / "client.sqlite").exists()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет произвольных строк в диагностических полях
#------------------------------------------------------------------------------------------------------------------
def test_error_labels_are_private() -> None:

    """Reject arbitrary error labels, including control characters and private key names."""

    error = CommandConfigError("sources[0].private-secret", "private-secret\n")
    assert error.field == "config" and error.code == "config_value"
    assert "private-secret" not in str(error) and "private-secret" not in repr(error)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Совпадение значений разных переменных обнаруживается при загрузке
#------------------------------------------------------------------------------------------------------------------
def test_resolved_duplicate_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Keep offline checking independent of environment while rejecting actual duplicate credentials at load.

    :param tmp_path: Isolated test directory.
    :type tmp_path: Path

    :param monkeypatch: Scoped synthetic environment variables.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # tmp_path — отдельный каталог теста.
    # monkeypatch — временные переменные с вымышленными секретами.
    raw = settings()
    for index, principal in enumerate(raw["principals"]):
        del principal["token"]
        name = "RW_TEST_PRIVATE_TOKEN_" + str(index)
        principal["token_env"] = name
        monkeypatch.setenv(name, "z" * 40)
    path = write_config(tmp_path / "gateway.json", raw)
    check_command_gateway(path)
    with pytest.raises(CommandConfigError) as caught:
        load_command_gateway(path)
    assert caught.value.code == "duplicate_principal_token"
    assert caught.value.field == "principals[1].token_env"
    assert "z" * 40 not in str(caught.value)
    assert not (tmp_path / "state").exists()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_health не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
