# Замена версии пользовательского трея без остановки SCM, gateway или приложений.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-212332
#
# Тесты:
# -> test_cooperative_replacement(): Новая версия и явный restart освобождают только lock трея
#    Вложенные объявления:
#    -> controller(): Отдельный владелец реагирует только на своё поколение
#
# -> test_unchanged_or_legacy(): Текущая версия не меняется, старый протокол требует ручного перехода
#
# -> test_stubborn_owner(): Неответивший контроллер не убивается и сохраняет блокировку
#
# -> test_stale_records(): Записи без владельца не мешают запуску и не закрывают новое поколение
#
# -> test_restart_cli(): Явный restart не вызывает start/stop/remove службы и не меняет Run
#
# -> test_controller_exit(): Полученный запрос закрывает GUI и освобождает lock даже при ошибке GUI
#
# -> test_launch_status(): Только отсутствие владельца приводит к новому bootstrap из CLI
#
# -> test_controller_handoff(): Полный запрос CLI завершает старый GUI и единожды запускает новый
#    Вложенные объявления:
#    -> run(): Запуск контроллера с перехватом ошибки фонового тестового потока
#
# -> test_probe_contention(): Проверочный lock CLI не теряет новый bootstrap
#    Вложенные объявления:
#    -> opened(): Подтверждение настоящего пересечения за OS-блокировку
#    -> run(): Контроллер в отдельном тестовом потоке без реального GUI


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

import json
import os
import threading
from pathlib import Path
from secrets import token_hex
from time import monotonic, sleep
from unittest.mock import Mock

import pytest
from test_gateway_service_recovery import controller_environment
from test_gateway_service_recovery import service_plan as _service_plan

from remote_watch.commands.source_store import ProcessLock
from remote_watch.commands.storage import StoreConflict
from remote_watch.gateway_server import _tray_control as control
from remote_watch.gateway_server import service as cli
from remote_watch.gateway_server import service_tray
from remote_watch.gateway_server.service import ServicePlan

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

service_plan = _service_plan

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Новая версия и явный restart освобождают только lock трея
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("installed, force", [("1.0.2", False), ("1.0.1", True)])
def test_cooperative_replacement(
    tmp_path: Path,
    installed: str,
    force: bool,
    ) -> None:

    """
    Exercise a real OS lock and generation-scoped handoff between two independent callers.

    :param tmp_path: Isolated controller directory with no production state.
    :type tmp_path: Path

    :param installed: Version requested by the updating caller.
    :type installed: str

    :param force: Explicit restart of the same installed version.
    :type force: bool
    """

    # tmp_path/installed/force — оба сценария используют настоящий lock и атомарные записи.

    path = tmp_path / "controller.lock"
    generation = token_hex(16)
    ready, finish, observed = threading.Event(), threading.Event(), threading.Event()
    failures = []

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Отдельный владелец реагирует только на своё поколение
    #--------------------------------------------------------------------------------------------------------------
    def controller() -> None:

        """
        Retain the old controller lock until its own restart request is observed.
        """

        guard = ProcessLock(path)
        try:
            guard.open()
            control.publish_owner(path, generation, "1.0.1")
            ready.set()
            while not finish.wait(.01):
                if control.restart_requested(path, generation):
                    observed.set()
                    break
        except BaseException as error:
            failures.append(error)
            ready.set()
        finally:
            control.clear_owner(path, generation)
            guard.close()
    #--------------------------------------------------------------------------------------------------------------

    thread = threading.Thread(target=controller)
    thread.start()
    try:
        assert ready.wait(10) and not failures
        assert control.prepare_launch(path, installed, restart=force) == "restart_requested"
        assert observed.is_set()
    finally:
        finish.set()
        thread.join(10)
    assert not thread.is_alive() and not failures

    # Новый владелец не выполняет оставшийся запрос для старой иконки.
    replacement = ProcessLock(path)
    replacement.open()
    try:
        control.publish_owner(path, token_hex(16), installed)
        assert control.prepare_launch(path, installed) == "already_running"
    finally:
        replacement.close()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Текущая версия не меняется, старый протокол требует ручного перехода
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("record, expected", [("current", "already_running"), ("missing", "restart_required"),
                                             ("invalid", "restart_required")])
def test_unchanged_or_legacy(
    tmp_path: Path,
    record: str,
    expected: str,
    ) -> None:

    """
    Never report a replacement when a legacy controller cannot accept the request.

    :param tmp_path: Isolated real owner lock.
    :type tmp_path: Path

    :param record: Current protocol, absent legacy metadata, or malformed metadata.
    :type record: str

    :param expected: Fixed diagnostic for this ownership state.
    :type expected: str
    """

    # tmp_path/record/expected — действующий владелец сохраняется во всех вариантах.

    path = tmp_path / "controller.lock"
    guard = ProcessLock(path)
    guard.open()
    try:
        if record == "current":
            control.publish_owner(path, token_hex(16), "1.0.1")
        elif record == "invalid":
            path.with_suffix(".owner.json").write_bytes(b"x" * 513)
        assert control.prepare_launch(path, "1.0.1") == expected
        assert not path.with_suffix(".restart.json").exists()
        with pytest.raises(StoreConflict):
            ProcessLock(path).open()
    finally:
        guard.close()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Неответивший контроллер не убивается и сохраняет блокировку
#------------------------------------------------------------------------------------------------------------------
def test_stubborn_owner(
    tmp_path: Path,
    ) -> None:

    """
    Keep the original owner intact after a finite cooperative restart timeout.

    :param tmp_path: Separate controller files used by this test only.
    :type tmp_path: Path
    """

    # tmp_path — исчерпание ожидания не даёт права снять или удалить чужой lock.

    path = tmp_path / "controller.lock"
    guard = ProcessLock(path)
    guard.open()
    try:
        control.publish_owner(path, token_hex(16), "1.0.1")
        assert control.prepare_launch(path, "1.0.2", timeout=.01) == "restart_timeout"
        with pytest.raises(StoreConflict):
            ProcessLock(path).open()
    finally:
        guard.close()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Записи без владельца не мешают запуску и не закрывают новое поколение
#------------------------------------------------------------------------------------------------------------------
def test_stale_records(
    tmp_path: Path,
    ) -> None:

    """
    Fence a previous lifetime and ignore stale metadata when no OS owner exists.

    :param tmp_path: Isolated sidecar and lock paths.
    :type tmp_path: Path
    """

    # tmp_path — статус-файл не заменяет проверку настоящего владения.

    path = tmp_path / "controller.lock"
    old, current = token_hex(16), token_hex(16)
    control.publish_owner(path, old, "1.0.1")
    assert control.prepare_launch(path, "1.0.2") == "launch_requested"
    control._write_record(path.with_suffix(".restart.json"), {"generation": old, "version": "1.0.1"})
    control.publish_owner(path, current, "1.0.2")
    assert not control.restart_requested(path, current)
    control.clear_owner(path, old)
    assert path.with_suffix(".owner.json").exists()
    control.clear_owner(path, current)
    assert not path.with_suffix(".owner.json").exists()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Явный restart не вызывает start/stop/remove службы и не меняет Run
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows service CLI")
@pytest.mark.parametrize("result, accepted", [("restart_requested", True), ("restart_required", False),
                                             ("restart_timeout", False)])
def test_restart_cli(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: str,
    accepted: bool,
    ) -> None:

    """
    Query exact SCM binding and request only GUI replacement through the explicit action.

    :param service_plan: Synthetic deployment and service binding.
    :type service_plan: ServicePlan

    :param monkeypatch: Replace SCM and spawning with observation-only mocks.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Safe CLI output containing no credentials.
    :type capsys: pytest.CaptureFixture[str]

    :param result: Cooperative handoff outcome.
    :type result: str

    :param accepted: Whether the explicit action can report acceptance.
    :type accepted: bool
    """

    # service_plan/monkeypatch/capsys/result/accepted — реальные службы и GUI не открываем.

    from remote_watch.gateway_server import service_windows

    backend = Mock()
    monkeypatch.setattr(service_windows, "WindowsService", Mock(return_value=backend))
    launch = Mock(return_value=result)
    monkeypatch.setattr(service_tray, "launch_tray", launch)
    spec = service_plan.spec
    code = cli.main(["--config", str(spec.config), "--operator-sid", spec.operator_sid,
                     "--name", spec.name, "--restart-tray"])
    assert code == (0 if accepted else 1)
    output = json.loads(capsys.readouterr().out)
    assert output["tray"] == result and output["accepted"] is accepted
    launch.assert_called_once()
    actual = launch.call_args.args[0]
    assert (actual.config, actual.name, actual.operator_sid) == (spec.config, spec.name, spec.operator_sid)
    assert launch.call_args.kwargs == {"restart": True}
    backend.status.assert_called_once()
    backend.start.assert_not_called()
    backend.stop.assert_not_called()
    backend.remove.assert_not_called()
    backend.sync_tray.assert_not_called()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полученный запрос закрывает GUI и освобождает lock даже при ошибке GUI
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("pending_stop", [False, True])
def test_controller_exit(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    close_fails: bool,
    pending_stop: bool,
    ) -> None:

    """
    Close only the controller on restart and release its exact lock after GUI cleanup failure.

    :param service_plan: Synthetic exact service invocation.
    :type service_plan: ServicePlan

    :param monkeypatch: Fake GUI, service calls and current-session identity.
    :type monkeypatch: pytest.MonkeyPatch

    :param tmp_path: Private scratch profile for the real lock and status record.
    :type tmp_path: Path

    :param close_fails: Simulate failure inside optional GUI cleanup.
    :type close_fails: bool

    :param pending_stop: A real user Stop action arrived before the restart request was consumed.
    :type pending_stop: bool
    """

    # service_plan/monkeypatch/tmp_path/close_fails — ошибки закрытия не удерживают lock.
    # pending_stop — команда пользователя из меню не должна потеряться при обновлении GUI.

    tray, backend = controller_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(service_tray, "restart_requested", Mock(return_value=True))
    relaunch = Mock(return_value="launch_requested")
    monkeypatch.setattr(service_tray, "_spawn_tray", relaunch)
    backend.status.return_value = {"state": "stopped"}
    if pending_stop:
        tray.start.side_effect = lambda **kwargs: service_tray.GatewayTray.call_args.args[0]()
    if close_fails:
        tray.close.side_effect = RuntimeError("synthetic-gui-close")
        with pytest.raises(RuntimeError):
            service_tray.run_tray(service_plan.spec)
    else:
        assert service_tray.run_tray(service_plan.spec) == 0
    backend.start.assert_not_called()
    assert backend.stop.call_count == int(pending_stop)
    assert backend.status.call_count == int(pending_stop)
    path = next((tmp_path / "profile/RemoteWatch/tray").glob("*.lock"))
    guard = ProcessLock(path)
    guard.open()
    guard.close()
    assert not path.with_suffix(".owner.json").exists()
    relaunch.assert_called_once_with(service_plan.spec)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Только отсутствие владельца приводит к новому bootstrap из CLI
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows user session")
@pytest.mark.parametrize("status", ["launch_requested", "already_running", "restart_requested",
                                    "restart_required", "restart_timeout"])
def test_launch_status(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    ) -> None:

    """
    Do not launch a competing GUI after a handoff request, timeout, or legacy refusal.

    :param service_plan: Synthetic session and configuration binding.
    :type service_plan: ServicePlan

    :param monkeypatch: Observe bootstrap without creating a Windows process.
    :type monkeypatch: pytest.MonkeyPatch

    :param tmp_path: Private temporary profile.
    :type tmp_path: Path

    :param status: Controlled cooperative ownership outcome.
    :type status: str
    """

    # service_plan/monkeypatch/tmp_path/status — SCM и настоящее окно не открываются.

    controller_environment(monkeypatch, tmp_path)
    prepare = Mock(return_value=status)
    spawn = Mock()
    monkeypatch.setattr(service_tray, "prepare_launch", prepare)
    monkeypatch.setattr(service_tray.subprocess, "Popen", spawn)
    assert service_tray.launch_tray(service_plan.spec, restart=True) == status
    assert prepare.call_args.kwargs == {"restart": True}
    assert spawn.call_count == int(status == "launch_requested")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полный запрос CLI завершает старый GUI и единожды запускает новый
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows user session")
def test_controller_handoff(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ) -> None:

    """
    Connect the real controller loop, sidecar request, OS lock and replacement bootstrap.

    :param service_plan: Synthetic exact service binding.
    :type service_plan: ServicePlan

    :param monkeypatch: Replace GUI, SCM and final bootstrap only.
    :type monkeypatch: pytest.MonkeyPatch

    :param tmp_path: Isolated real controller records.
    :type tmp_path: Path
    """

    # service_plan/monkeypatch/tmp_path — передача владения настоящая; новый pythonw не запускается.

    tray, backend = controller_environment(monkeypatch, tmp_path)
    backend.status.return_value = {"state": "running"}
    installed = {"version": "1.0.1"}
    monkeypatch.setattr(service_tray, "version", lambda name: installed["version"])
    monkeypatch.setattr(service_tray, "tray_settings", Mock(return_value=(True, None)))
    spawn = Mock()
    monkeypatch.setattr(service_tray.subprocess, "Popen", spawn)
    failures = []

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Запуск контроллера с перехватом ошибки фонового тестового потока
    #--------------------------------------------------------------------------------------------------------------
    def run() -> None:

        """
        Retain any thread failure for an assertion in the owning test.
        """

        try:
            service_tray.run_tray(service_plan.spec)
        except BaseException as error:
            failures.append(error)
    #--------------------------------------------------------------------------------------------------------------

    worker = threading.Thread(target=run)
    worker.start()
    try:
        root = tmp_path / "profile/RemoteWatch/tray"
        deadline = monotonic() + 10
        while not list(root.glob("*.owner.json")):
            assert not failures and monotonic() < deadline
            sleep(.01)
        installed["version"] = "1.0.2"
        assert service_tray.launch_tray(service_plan.spec) == "restart_requested"
    finally:
        tray.ready = False
        worker.join(10)
    assert not worker.is_alive() and not failures
    tray.close.assert_called_once()
    spawn.assert_called_once()
    assert spawn.call_args.args[0] == service_plan.spec.arguments("tray")
    backend.start.assert_not_called()
    backend.stop.assert_not_called()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверочный lock CLI не теряет новый bootstrap
#------------------------------------------------------------------------------------------------------------------
def test_probe_contention(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ) -> None:

    """
    Start the replacement while a real ownership probe is held and admit exactly one GUI afterwards.

    :param service_plan: Exact synthetic service binding.
    :type service_plan: ServicePlan

    :param monkeypatch: Replace GUI, SCM and observe lock contention only.
    :type monkeypatch: pytest.MonkeyPatch

    :param tmp_path: Isolated profile containing the controller lock.
    :type tmp_path: Path
    """

    # service_plan/monkeypatch/tmp_path — занятость создаётся настоящим lock, не случайным sleep.

    import hashlib

    tray, backend = controller_environment(monkeypatch, tmp_path)
    backend.status.return_value = {"state": "running"}
    tray.set_visible.side_effect = lambda visible: setattr(tray, "ready", False)
    root = tmp_path / "profile/RemoteWatch/tray"
    root.mkdir(parents=True)
    spec = service_plan.spec
    digest = hashlib.sha256(str(spec.config).casefold().encode()).hexdigest()[:24]
    path = root / f"{spec.name}-1-{digest}.lock"
    probe = ProcessLock(path)
    probe.open()
    blocked = threading.Event()
    original = ProcessLock.open
    failures = []

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Подтверждение настоящего пересечения за OS-блокировку
    #--------------------------------------------------------------------------------------------------------------
    def opened(
        owner: ProcessLock,
        ) -> None:

        """
        Observe a rejected real lock acquisition before releasing the synthetic CLI probe.

        :param owner: Replacement controller's separate lock instance.
        :type owner: ProcessLock
        """

        # owner — основное поведение OS lock не подменяем.

        try:
            original(owner)
        except StoreConflict:
            blocked.set()
            raise
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Контроллер в отдельном тестовом потоке без реального GUI
    #--------------------------------------------------------------------------------------------------------------
    def run() -> None:

        """
        Keep unexpected controller errors visible to the owning test.
        """

        try:
            service_tray.run_tray(spec)
        except BaseException as error:
            failures.append(error)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(ProcessLock, "open", opened)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert blocked.wait(10)
        tray.start.assert_not_called()
    finally:
        probe.close()
        worker.join(10)
    assert not worker.is_alive() and not failures
    tray.start.assert_called_once_with(visible=False)
    backend.start.assert_not_called()
    backend.stop.assert_not_called()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Тестовый модуль не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
