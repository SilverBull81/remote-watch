# Проверки установки службы, границ прав, отдельного UI и переходов жизненного цикла.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-124120
#
# Тесты:
# -> service_plan(): Изолированные конфиги и фиктивный venv без сетевых вызовов.
# -> backend(): Fake SCM без регистрации реальной службы.
# -> test_plan(): Прежние пути и отсутствие побочных изменений при проверке.
# -> test_unsafe_spec(): Запрет чужих имён/неверных SID.
# -> test_ownership(): Exact binary/account перед любой командой.
# -> test_install(): Остановленная служба, ограниченные права и конечный recovery.
# -> test_install_refusal(): Отказ при существующей службе и откат частичной установки.
# -> test_live_owner(): Отказ при действующем foreground manager.
# -> test_second_manager_acl(): Второй start не меняет ACL живого владельца.
# -> test_remove(): Удаление только остановленной точно совпадающей службы.
# -> test_dry_run(): CLI dry-run не загружает SCM и не пишет файлы.
# -> test_headless_host(): Служба не создаёт UI и останавливает один manager.
# -> test_startup_failure(): Только временный startup отказ разрешает конечный restart.
# -> test_host_controls(): Native callbacks, безопасная ошибка и конечный stop.
# -> test_tray_config(): UI не зависит от поломанных связанных конфигов.
# -> test_registry(): Run-запись не перезаписывает чужое приложение.
# -> test_controller(): SCM-наблюдение без создания manager.
# -> test_native_dispatcher(): Compiled host отказывает вне SCM без регистрации службы.
# -> test_reparse(): Рекурсивные права не выдаются через ссылку.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from remote_watch.commands.source_store import ProcessLock
from remote_watch.gateway_server.deployment import DeployOptions, deploy, prepare_files
from remote_watch.gateway_server.service import ServicePlan, ServiceSpec, main, make_plan
from remote_watch.gateway_server.service_host import TransientServiceFailure, run_service, supervise
from remote_watch.gateway_server.service_tray import tray_settings
from remote_watch.gateway_server.service_windows import WindowsService, checked_tree

SID = "S-1-5-21-101-202-303-1001"


#------------------------------------------------------------------------------------------------------------------
# ФИКСТУРА : Изолированные конфиги и фиктивный venv без сетевых вызовов
#------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def service_plan(tmp_path: Path) -> ServicePlan:

    """Prepare synthetic deployment files without any installed or started OS service.

    :param tmp_path: Isolated scratch directory.
    :type tmp_path: Path
    :return: Valid installation plan referencing only synthetic local resources.
    :rtype: ServicePlan
    """

    # tmp_path — даже Python-файлы здесь заглушки; ни один subprocess не запускается.
    root = tmp_path / "gateway space & percent% $literal"
    executable = root / ".venv" / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic-python")
    executable.with_name("pythonw.exe").write_bytes(b"synthetic-pythonw")
    (executable.parent.parent / "pyvenv.cfg").write_text("synthetic", encoding="utf-8")
    deploy(DeployOptions(directory=root, python=executable, endpoint="https://example.test:8443", ui="tray"))
    return make_plan(ServiceSpec(root / "gateway_server.local.json", executable, SID))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФИКСТУРА : Fake SCM без регистрации реальной службы
#------------------------------------------------------------------------------------------------------------------
def backend(plan: ServicePlan) -> WindowsService:

    """Create a native-API substitute that works in core and on Linux.

    :param plan: Synthetic validated installation plan.
    :type plan: ServicePlan
    :return: Backend with all mutations replaced by mocks.
    :rtype: WindowsService
    """

    # plan — fake handles не поддерживают with: настоящий PySC_HANDLE тоже не поддерживает.
    result = object.__new__(WindowsService)
    result.spec = plan.spec
    result.binary = subprocess.list2cmdline(plan.spec.arguments("run"))
    handle = Mock()
    result.api = Mock()
    result.api.OpenService.return_value = handle
    result.api.QueryServiceConfig.return_value = (16, 2, 1, result.binary, None, 0, None,
                                                  "NT SERVICE\\" + plan.spec.name, "Gateway")
    result.api.QueryServiceStatus.return_value = (16, 1, 0, 0, 0, 0, 0)
    result.api.QueryServiceStatusEx.return_value = {"CurrentState": 1, "Win32ExitCode": 0,
                                                    "ServiceSpecificExitCode": 0}
    result.security = Mock()
    result.security.LookupAccountName.return_value = ("synthetic-sid", None, None)
    result.security.ConvertSidToStringSid.return_value = "S-1-5-80-11-22-33-44-55"
    result._grant = Mock()
    result._sync_entry = Mock()
    return result
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Прежние пути и отсутствие побочных изменений при проверке
#------------------------------------------------------------------------------------------------------------------
def test_plan(service_plan: ServicePlan) -> None:

    """Preserve configs and future journal paths and ship operational service resources.

    :param service_plan: Synthetic deployment with both channels and tray enabled.
    :type service_plan: ServicePlan
    """

    # service_plan — читаем только конфиги; credentials из ENV не разрешаются.
    root = service_plan.spec.config.parent
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert make_plan(service_plan.spec) == service_plan
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert root / "data" / "commands" in service_plan.write_dirs
    assert not (root / "data").exists()
    command = service_plan.spec.arguments("run")
    assert command[1:5] == ["-I", "-B", "-m", "remote_watch.gateway_server.service"]
    assert str(service_plan.spec.config) in command
    assert "synthetic" not in repr(service_plan)
    for name in ("install-service.cmd", "remove-service.cmd", "sync-tray.ps1", "tray.ps1", "SERVICE.md",
                 "service/install-systemd.sh", "service/remote-watch-gateway.service"):
        assert (root / name).is_file()
    assert b"%%" in (root / "install-service.cmd").read_bytes()
    unit = (root / "service/remote-watch-gateway.service").read_text()
    assert "--start --headless" in unit and "StartLimitBurst=3" in unit and "CHANGE_ME" in unit
    assert "$$literal" in unit
    proposal = prepare_files(DeployOptions(directory=root, endpoint="https://example.test:8443",
                                           python=service_plan.spec.python))
    assert "service/remove-systemd.sh" in proposal
    # Общая папка с логами внешнего Caddy не является runtime directory manager.
    value = json.loads(service_plan.spec.config.read_text())
    value["log_dir"] = "logs"
    service_plan.spec.config.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="service_shared_logs"):
        make_plan(service_plan.spec)
    value["log_dir"] = "other-shared-log-folder"
    service_plan.spec.config.write_text(json.dumps(value), encoding="utf-8")
    shared = root / value["log_dir"]
    shared.mkdir()
    external = shared / "caddy-runtime.jsonl"
    external.write_bytes(b"synthetic-external-log")
    with pytest.raises(ValueError, match="service_shared_logs"):
        make_plan(service_plan.spec)
    assert external.read_bytes() == b"synthetic-external-log"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет чужих имён и неверных SID
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field,value", [("name", "Caddy"), ("name", "RemoteWatch;x"),
    ("operator_sid", "S-1-1-0"), ("operator_sid", SID + ")(A;;GA;;;WD)"),
    ("operator_sid", "S-1-5-21-4294967296-2-3-4")])
def test_unsafe_spec(
    service_plan: ServicePlan,
    field: str,
    value: str,
) -> None:

    """Reject name injection and broader account classes before native API calls.

    :param service_plan: Valid local binding.
    :type service_plan: ServicePlan
    :param field: Binding field being corrupted.
    :type field: str
    :param value: Invalid nonsecret test value.
    :type value: str
    """

    # service_plan/field/value — нельзя подменить разрешения вставкой SDDL.
    with pytest.raises(ValueError):
        replace(service_plan.spec, **{field: value})
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Exact binary/account перед любой командой
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("foreign", ["binary", "account", "type"])
def test_ownership(
    service_plan: ServicePlan,
    foreign: str,
) -> None:

    """Refuse all controls for a same-name service with another owner binding.

    :param service_plan: Synthetic expected owner.
    :type service_plan: ServicePlan
    :param foreign: Changed native configuration field.
    :type foreign: str
    """

    # service_plan/foreign — совпавшее имя или PID само по себе недостаточно.
    item = backend(service_plan)
    config = list(item.api.QueryServiceConfig.return_value)
    config[{"binary": 3, "account": 7, "type": 0}[foreign]] = "foreign" if foreign != "type" else 32
    item.api.QueryServiceConfig.return_value = tuple(config)
    with pytest.raises(ValueError, match="service_foreign"):
        item.stop()
    item.api.ControlService.assert_not_called()
    item.api.OpenService.return_value.Close.assert_called_once()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Остановленная служба, ограниченные права и конечный recovery
#------------------------------------------------------------------------------------------------------------------
def test_install(service_plan: ServicePlan) -> None:

    """Use a virtual account and delay automatic startup until ACL and recovery setup finish.

    :param service_plan: Original local runtime paths.
    :type service_plan: ServicePlan
    """

    # service_plan — fake API записывает только порядок/аргументы, ничего не регистрирует.
    item = backend(service_plan)
    item.api.OpenService.side_effect = OSError(1060, "private")
    item.api.OpenService.side_effect.winerror = 1060
    item.install(service_plan)
    create = item.api.CreateService.call_args.args
    assert create[4:7] == (16, 3, 1)
    assert create[-2:] == ("NT SERVICE\\RemoteWatchGateway", None)
    assert str(service_plan.spec.config) in create[7]
    actions = item.api.ChangeServiceConfig2.call_args_list[0].args[2]
    assert actions["Actions"] == [(1, 60000), (1, 300000), (0, 0)]
    descriptor = item.security.ConvertStringSecurityDescriptorToSecurityDescriptor.call_args.args[0]
    assert SID in descriptor and "DC" not in descriptor and ";;;WD" not in descriptor
    assert {call.args[0] for call in item._grant.call_args_list} >= set(service_plan.write_dirs)
    item.api.StartService.assert_not_called()
    item.api.DeleteService.assert_not_called()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ при существующей службе и откат частичной установки
#------------------------------------------------------------------------------------------------------------------
def test_install_refusal(service_plan: ServicePlan) -> None:

    """Never overwrite existing services and roll back only a newly created service on failure.

    :param service_plan: Original local runtime paths.
    :type service_plan: ServicePlan
    """

    # service_plan — отсутствие изменений data проверяется до/после fake установки.
    item = backend(service_plan)
    with pytest.raises(ValueError, match="service_exists"):
        item.install(service_plan)
    item.api.CreateService.assert_not_called()
    item._grant.assert_not_called()
    missing = OSError(1060, "private")
    missing.winerror = 1060
    item.api.OpenService.side_effect = missing
    item._grant.side_effect = PermissionError("private-path-and-token")
    with pytest.raises(ValueError, match="service_install_partial"):
        item.install(service_plan)
    item.api.DeleteService.assert_called_once()
    assert not (service_plan.spec.config.parent / "data").exists()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Действующий foreground владелец не меняет аккаунт посреди работы
#------------------------------------------------------------------------------------------------------------------
def test_live_owner(service_plan: ServicePlan) -> None:

    """Reject installation before native service or ACL mutation when a manager owns its lock.

    :param service_plan: Synthetic deployment with a real local owner lock.
    :type service_plan: ServicePlan
    """

    # service_plan — блокировка той же ОС/файла, что используется настоящим manager.
    owner = ProcessLock(service_plan.deployment.control_dir / "manager.lock")
    owner.open()
    item = backend(service_plan)
    try:
        with pytest.raises(ValueError, match="service_manager_running"):
            item.install(service_plan)
        item.api.OpenSCManager.assert_not_called()
        item._grant.assert_not_called()
    finally:
        owner.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Второй --start не меняет ACL живого владельца
#------------------------------------------------------------------------------------------------------------------
def test_second_manager_acl(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Acquire existing deployment ownership before changing a service/operator DACL.

    :param service_plan: Synthetic deployment with its original control path.
    :type service_plan: ServicePlan
    :param monkeypatch: Observe ACL calls without changing any native permissions.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # service_plan/monkeypatch — действующая OS lock отвергает другой запуск до
    # смены прав. Ошибка самой защиты каталога также не должна оставлять lock.
    from remote_watch.commands.storage import StoreConflict
    from remote_watch.gateway_server.manager import GatewayManager

    path = service_plan.deployment.control_dir / "manager.lock"
    owner = ProcessLock(path)
    owner.open()
    acl = Mock(side_effect=PermissionError("synthetic"))
    monkeypatch.setattr("remote_watch.gateway_server.manager.private_directory", acl)
    try:
        with pytest.raises(StoreConflict):
            asyncio.run(GatewayManager(service_plan.deployment).run())
        acl.assert_not_called()
    finally:
        owner.close()
    with pytest.raises(PermissionError):
        asyncio.run(GatewayManager(service_plan.deployment).run())
    owner.open()
    owner.close()
    # Предварительное открытие lock не должно обойти отказ по symlink/junction.
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == path.parent or original(self))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="control_permissions"):
        asyncio.run(GatewayManager(service_plan.deployment).run())
    assert path.read_bytes() == before
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Удаление только остановленной точно совпадающей службы
#------------------------------------------------------------------------------------------------------------------
def test_remove(service_plan: ServicePlan) -> None:

    """Keep running services and all working configuration and journal bytes intact.

    :param service_plan: Original synthetic deployment.
    :type service_plan: ServicePlan
    """

    # service_plan — remove не имеет рекурсивного удаления файлов или ACL reset.
    item = backend(service_plan)
    item.api.QueryServiceStatus.return_value = (16, 4, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="service_running"):
        item.remove()
    item._sync_entry.assert_not_called()
    item.api.QueryServiceStatus.return_value = (16, 1, 0, 0, 0, 0, 0)
    before = service_plan.spec.config.read_bytes()
    item.remove()
    item._sync_entry.assert_called_once_with(False)
    item.api.DeleteService.assert_called_once()
    assert service_plan.spec.config.read_bytes() == before
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : CLI dry-run не загружает SCM и не пишет файлы
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows service CLI")
def test_dry_run(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:

    """Exercise the real parser while prohibiting native service mutations.

    :param service_plan: Synthetic validated files and fake executable.
    :type service_plan: ServicePlan
    :param monkeypatch: Override only the selected Python and native backend.
    :type monkeypatch: pytest.MonkeyPatch
    :param capsys: Captured safe CLI report.
    :type capsys: pytest.CaptureFixture
    """

    # service_plan/monkeypatch/capsys — отчёт не раскрывает пути, SID или JSON.
    monkeypatch.setattr(sys, "executable", str(service_plan.spec.python))
    monkeypatch.setattr(WindowsService, "__init__", Mock(side_effect=AssertionError("SCM touched")))
    before = {p: p.read_bytes() for p in service_plan.spec.config.parent.rglob("*") if p.is_file()}
    assert main(["--config", str(service_plan.spec.config), "--operator-sid", SID, "--install", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["writes"] is False
    assert SID not in output and str(service_plan.spec.config) not in output
    assert before == {p: p.read_bytes() for p in service_plan.spec.config.parent.rglob("*") if p.is_file()}
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Служба не создаёт UI и останавливает один manager
#------------------------------------------------------------------------------------------------------------------
def test_headless_host(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Keep a tray-configured service headless and report startup only after manager initialization.

    :param service_plan: Tray-enabled deployment.
    :type service_plan: ServicePlan
    :param monkeypatch: Substitute one in-memory manager with no processes.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # service_plan/monkeypatch — callback/SQLite/провайдеры вообще не участвуют.
    instance = SimpleNamespace(state="starting", stop_event=asyncio.Event())
    reports = []
    stopped = threading.Event()
    constructor = Mock(return_value=instance)
    monkeypatch.setattr("remote_watch.gateway_server.service_host.GatewayManager", constructor)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Один искусственный жизненный цикл manager
    #--------------------------------------------------------------------------------------------------------------
    async def manager_run() -> int:

        """Become ready, then complete only after the service requests shutdown.

        :return: Clean manager exit code.
        :rtype: int
        """

        instance.state = "ready"
        await asyncio.sleep(1.2)
        stopped.set()
        await instance.stop_event.wait()
        return 0
    #--------------------------------------------------------------------------------------------------------------

    instance.run = manager_run
    assert asyncio.run(supervise(service_plan.spec, stopped, reports.append)) == 0
    assert constructor.call_count == 1
    assert constructor.call_args.args[0].application.ui == "headless"
    assert constructor.call_args.kwargs == {"operator_sid": SID}
    assert reports[0] == "starting" and "running" in reports
    assert asyncio.run(supervise(service_plan.spec, stopped, reports.append)) == 0
    assert constructor.call_count == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Только временный startup отказ выбирает конечный restart ОС
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("reason,stop,retry", [("startup_timeout", False, True),
    ("configuration", False, False), ("authorization", False, False), ("ownership", False, False),
    ("startup_timeout", True, False)])
def test_startup_failure(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    stop: bool,
    retry: bool,
) -> None:

    """Distinguish finite network startup recovery from permanent errors and an intentional stop.

    :param service_plan: Synthetic original paths.
    :type service_plan: ServicePlan
    :param monkeypatch: Fake manager with completed rollback.
    :type monkeypatch: pytest.MonkeyPatch
    :param reason: Fixed original manager reason.
    :type reason: str
    :param stop: Whether SCM requested stop during manager completion.
    :type stop: bool
    :param retry: Whether the finite OS recovery path is allowed.
    :type retry: bool
    """

    # service_plan/monkeypatch/reason/stop/retry — никакого callback replay;
    # повтор разрешён только после возврата завершённого manager.run.
    stopped = threading.Event()
    instance = SimpleNamespace(state="starting", reason=reason, stop_event=asyncio.Event())

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Полный искусственный rollback до возврата результата
    #--------------------------------------------------------------------------------------------------------------
    async def failed_run() -> int:

        """Finish rollback before exposing the fixed failure.

        :return: Nonzero manager result with an independently retained reason.
        :rtype: int
        """

        instance.state = "stopped"
        if stop:
            stopped.set()
        return 1
    #--------------------------------------------------------------------------------------------------------------

    instance.run = failed_run
    monkeypatch.setattr("remote_watch.gateway_server.service_host.GatewayManager", Mock(return_value=instance))
    if retry:
        with pytest.raises(TransientServiceFailure):
            asyncio.run(supervise(service_plan.spec, stopped, Mock()))
    else:
        assert asyncio.run(supervise(service_plan.spec, stopped, Mock())) == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : SCM callbacks не выдают ложный RUNNING и скрывают исходное исключение
#------------------------------------------------------------------------------------------------------------------
def test_host_controls(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Test the actual host class with a fake compiled framework, including its hard-stop boundary.

    :param service_plan: Original synthetic deployment config.
    :type service_plan: ServicePlan
    :param monkeypatch: Substitute only native host bindings and deliberate failure boundaries.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # service_plan/monkeypatch — реальный SCM не вызывается, а os._exit заменён
    # SystemExit. Проверка не может остановить pytest или production процессы.
    from remote_watch.gateway_server import service_host as host_module

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Минимальный контракт pywin32 framework без регистрации handler
    #--------------------------------------------------------------------------------------------------------------
    class Framework:
        """Retain the native service reports without Windows side effects."""

        #----------------------------------------------------------------------------------------------------------
        # КОНСТРУКТОР
        #----------------------------------------------------------------------------------------------------------
        def __init__(
            self,
            args: list[str],
        ) -> None:

            """Retain only report calls from the tested subclass.

            :param args: Synthetic service name.
            :type args: list[str]
            """

            # args — имя не используется для доступа к ОС.
            self.reports = []
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Запись статуса вместо Windows API
        #----------------------------------------------------------------------------------------------------------
        def ReportServiceStatus(
            self,
            state: int,
            **kwargs: object,
        ) -> None:

            """Record fixed lifecycle and exit-code fields.

            :param state: Native service lifecycle number.
            :type state: int
            :param kwargs: Optional wait and exit-code fields.
            :type kwargs: object
            """

            # state/kwargs — только числа, без сообщения ошибки или конфигов.
            self.reports.append((state, kwargs))
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    native = Mock()
    monkeypatch.setitem(sys.modules, "servicemanager", native)
    monkeypatch.setitem(sys.modules, "win32serviceutil", SimpleNamespace(ServiceFramework=Framework))
    assert run_service(service_plan.spec) == 0
    factory = native.PrepareToHostSingle.call_args.args[0]
    item = factory([service_plan.spec.name])
    assert item.GetAcceptedControls() == 0
    item._report("running")
    assert item.GetAcceptedControls() & 0x100
    item.SvcStop()
    item.SvcInterrogate()
    assert item.reports[-1][0] == 3 and item.GetAcceptedControls() == 0
    # Ошибка runtime не раскрывает исходное исключение через C framework.
    item = factory([service_plan.spec.name])
    monkeypatch.setattr(host_module, "os", SimpleNamespace(chdir=Mock()))
    monkeypatch.setattr(host_module, "supervise", AsyncMock(side_effect=RuntimeError("PRIVATE_PROVIDER_TOKEN")))
    with pytest.raises(RuntimeError, match="^gateway_service_failed$") as caught:
        item.SvcRun()
    assert caught.value.__suppress_context__
    assert "PRIVATE_PROVIDER_TOKEN" not in str(native.LogErrorMsg.call_args)
    # Даже отказ Event Log не отменяет конечную остановку. STOPPED/0 заранее
    # исключает автоматический restart после сознательного stop оператора.
    item = factory([service_plan.spec.name])
    item.stopped.set()
    item.stop_at, item.stop_budget = 0, 0
    item.finished = Mock()
    item.finished.wait.return_value = False
    native.LogErrorMsg.side_effect = RuntimeError("log unavailable")
    monkeypatch.setattr(host_module, "monotonic", lambda: 1)
    exit_call = Mock(side_effect=SystemExit(0))
    monkeypatch.setattr(host_module, "os", SimpleNamespace(_exit=exit_call))
    with pytest.raises(SystemExit):
        item._watch_stop()
    exit_call.assert_called_once_with(0)
    assert item.reports[-1] == (1, {"win32ExitCode": 0})
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : UI не зависит от поломанных связанных конфигов
#------------------------------------------------------------------------------------------------------------------
def test_tray_config(service_plan: ServicePlan) -> None:

    """Read presentation independently of provider credentials and component availability.

    :param service_plan: Synthetic configuration to corrupt safely.
    :type service_plan: ServicePlan
    """

    # service_plan — испорченный linked JSON не должен лишать оператора UI остановки.
    service_plan.deployment.commands.config.write_text("PRIVATE invalid JSON", encoding="utf-8")
    assert tray_settings(service_plan.spec.config) == (True, service_plan.deployment.application.icon_path)
    data = json.loads(service_plan.spec.config.read_text())
    data["application"]["ui"] = "headless"
    service_plan.spec.config.write_text(json.dumps(data), encoding="utf-8")
    assert tray_settings(service_plan.spec.config)[0] is False
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Run-запись не перезаписывает чужое приложение
#------------------------------------------------------------------------------------------------------------------
def test_registry(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Create and remove only the exact operator's Run command, preserving foreign values.

    :param service_plan: Exact synthetic binding.
    :type service_plan: ServicePlan
    :param monkeypatch: Replace registry calls, never the real user profile.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # service_plan/monkeypatch — содержимое HKCU не читается и не меняется.
    registry = MagicMock(HKEY_USERS=1, KEY_READ=1, KEY_QUERY_VALUE=1, KEY_SET_VALUE=2, REG_SZ=1)
    registry.QueryValueEx.side_effect = FileNotFoundError()
    monkeypatch.setitem(sys.modules, "winreg", registry)
    item = backend(service_plan)
    assert WindowsService._sync_entry(item, True)
    expected = subprocess.list2cmdline(service_plan.spec.arguments("tray"))
    assert registry.SetValueEx.call_args.args[-1] == expected
    registry.QueryValueEx.side_effect = None
    registry.QueryValueEx.return_value = ("foreign-application", 1)
    registry.SetValueEx.reset_mock()
    with pytest.raises(ValueError, match="service_tray_entry"):
        WindowsService._sync_entry(item, False)
    registry.SetValueEx.assert_not_called()
    registry.DeleteValue.assert_not_called()
    registry.QueryValueEx.return_value = (expected, 1)
    assert not WindowsService._sync_entry(item, False)
    registry.DeleteValue.assert_called_once()
    registry.OpenKey.side_effect = FileNotFoundError()
    assert not WindowsService._sync_entry(item, False)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Controller использует только SCM, выход не останавливает gateway
#------------------------------------------------------------------------------------------------------------------
def test_controller(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:

    """Keep UI in its own session with exact service controls and no manager construction.

    :param service_plan: Tray-enabled synthetic binding.
    :type service_plan: ServicePlan
    :param monkeypatch: Fake native session, UI and service APIs.
    :type monkeypatch: pytest.MonkeyPatch
    :param tmp_path: Isolated user-profile cache.
    :type tmp_path: Path
    """

    # service_plan/monkeypatch/tmp_path — настоящий lock только отдельной иконки,
    # не lock manager/source и не SQLite. Провайдеры и control token не используются.
    from remote_watch.gateway_server import service_tray as controller

    session_api = Mock(side_effect=lambda pid, ptr: setattr(ptr._obj, "value", 1) or 1)
    monkeypatch.setattr(controller, "ctypes", SimpleNamespace(c_uint32=ctypes.c_uint32, byref=ctypes.byref,
        windll=SimpleNamespace(kernel32=SimpleNamespace(ProcessIdToSessionId=session_api))))
    monkeypatch.setattr(controller, "current_sid", lambda: SID)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    close, stop = MagicMock(), threading.Event()
    close.is_set.return_value = False
    monkeypatch.setattr(controller, "threading", SimpleNamespace(Event=Mock(side_effect=[close, stop])))
    tray = Mock(ready=True)
    factory = Mock(return_value=tray)
    tray.start.side_effect = stop.set
    tray.update.side_effect = lambda state: setattr(tray, "ready", False)
    monkeypatch.setattr(controller, "GatewayTray", factory)
    native = Mock()
    native.status.return_value = {"state": "running"}
    monkeypatch.setattr(controller, "WindowsService", Mock(return_value=native))
    assert controller.run_tray(service_plan.spec) == 0
    native.stop.assert_called_once()
    tray.update.assert_called_once_with("running")
    tray.close.assert_called_once()
    assert factory.call_args.kwargs["exit_callback"] == close.set
    # Без ui controller не создаёт ни GUI, ни native backend.
    data = json.loads(service_plan.spec.config.read_text())
    data["application"]["ui"] = "headless"
    service_plan.spec.config.write_text(json.dumps(data), encoding="utf-8")
    assert controller.run_tray(service_plan.spec) == 0
    assert factory.call_count == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Compiled pywin32 host не запускается как произвольный console process
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows SCM dispatcher")
def test_native_dispatcher(service_plan: ServicePlan) -> None:

    """Load the real optional host DLL and refuse console invocation without creating any service.

    :param service_plan: Synthetic config; service main must not be called.
    :type service_plan: ServicePlan
    """

    # service_plan — SCM dispatcher возвращает 1063 в обычном console process;
    # CreateService/регистрация в ОС не вызываются, manager вообще не создаётся.
    pytest.importorskip("servicemanager")
    from remote_watch.gateway_server.service_host import run_service

    with pytest.raises(Exception) as caught:
        run_service(service_plan.spec)
    assert caught.value.winerror == 1063
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Рекурсивные права не выдаются через ссылку
#------------------------------------------------------------------------------------------------------------------
def test_reparse(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Reject a reparse descendant before any recursive ACL operation.

    :param service_plan: Isolated environment tree.
    :type service_plan: ServicePlan
    :param monkeypatch: Simulate an NTFS reparse point without requiring symlink privileges.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # service_plan/monkeypatch — attribute подменён только для вложенного файла.
    original = Path.lstat
    file = service_plan.spec.python
    fake = Mock(st_mode=0, st_file_attributes=0x400)
    monkeypatch.setattr(Path, "lstat", lambda self: fake if self == file else original(self))
    with pytest.raises(ValueError, match="service_linked_path"):
        checked_tree(service_plan.environment)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Запуск тестов через pytest
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_gateway_service не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
