# Повторная установка службы и автоматическая видимость иконки в пользовательском сеансе.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-154136
#
# Функции:
# -> service_plan(): Проверенный изолированный deployment из общего контракта службы
#
# -> controller_environment(): Изолированный пользовательский сеанс без GUI и вызовов SCM
#
# Тесты:
# -> test_readonly_reinstall(): Установщик удерживает прежний файл без записи и не обходит живого владельца
#    Вложенные объявления:
#    -> readonly(): Разрешение только чтения существующего служебного файла
#    -> protected(): Проверка непрерывного владения во время изменения прав
#
# -> test_owner_access_error(): Отказ доступа не выдаётся за работающий manager
#    Вложенные объявления:
#    -> denied(): Подмена только открытия файла блокировки
#
# -> test_native_readonly_file(): Настоящий Windows ACL разрешает повторную проверку только для чтения
#
# -> test_controller_visibility(): Одна иконка следует SCM и настройке ui без повторного запуска сервера
#
# -> test_tray_launch(): CLI запрашивает только скрытый процесс выбранного оператора
#
# -> test_owner_cli(): CLI сохраняет точную безопасную причину отказа доступа к control
#
# -> test_start_cli_tray(): Принятый SCM запуск не становится отказом при недоступной иконке
#
# -> test_existing_grant(): Уже выданные права не требуют повторного изменения служебных файлов


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

import ctypes
import errno
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest
from test_gateway_service import SID, backend
from test_gateway_service import service_plan as make_service_plan

from remote_watch.commands.source_store import ProcessLock
from remote_watch.gateway_server import service as service_cli
from remote_watch.gateway_server import service_tray as controller
from remote_watch.gateway_server import service_windows
from remote_watch.gateway_server.service import ServicePlan, main
from remote_watch.gateway_server.service_windows import ServiceControlError, WindowsService, installation_owner

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверенный изолированный deployment из общего контракта службы
#------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def service_plan(
    tmp_path: Path,
    ) -> ServicePlan:

    """
    Reuse the original synthetic service layout without registering its fixture as an import.

    :param tmp_path: Isolated deployment root.
    :type tmp_path: pathlib.Path

    :return: Validated synthetic service plan with original data paths.
    :rtype: ServicePlan
    """

    # tmp_path — общая фабрика создаёт только тестовые JSON и фиктивный venv.
    return make_service_plan.__wrapped__(tmp_path)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Изолированный пользовательский сеанс без GUI и вызовов SCM
#------------------------------------------------------------------------------------------------------------------
def controller_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ) -> tuple[Mock, Mock]:

    """
    Replace Windows session, optional GUI and native service APIs for controller tests.

    :param monkeypatch: Temporary replacements restored after each scenario.
    :type monkeypatch: pytest.MonkeyPatch

    :param tmp_path: Isolated profile directory for the controller lock.
    :type tmp_path: pathlib.Path

    :return: Fake tray and fake exact-binding service backend.
    :rtype: tuple[Mock, Mock]
    """

    # monkeypatch/tmp_path — блокировка контроллера настоящая, но находится только
    # во временном профиле. Рабочие службы, control и журналы не открываются.
    session_api = Mock(side_effect=lambda pid, ptr: setattr(ptr._obj, "value", 1) or 1)
    monkeypatch.setattr(controller, "ctypes", SimpleNamespace(c_uint32=ctypes.c_uint32, byref=ctypes.byref,
        windll=SimpleNamespace(kernel32=SimpleNamespace(ProcessIdToSessionId=session_api))))
    monkeypatch.setattr(controller, "current_sid", lambda: SID)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    close = MagicMock()
    close.is_set.return_value = False
    stop = threading.Event()
    monkeypatch.setattr(controller, "threading", SimpleNamespace(Event=Mock(side_effect=[close, stop])))
    tray = Mock(ready=True)
    monkeypatch.setattr(controller, "GatewayTray", Mock(return_value=tray))
    native = Mock()
    monkeypatch.setattr(controller, "WindowsService", Mock(return_value=native))
    return tray, native
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Установщик удерживает прежний файл без записи и не обходит живого владельца
#------------------------------------------------------------------------------------------------------------------
def test_readonly_reinstall(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """
    Reinstall with read-only lock access while preserving the same exclusive OS lock.

    :param service_plan: Synthetic deployment with fake SCM and original journal paths.
    :type service_plan: ServicePlan

    :param monkeypatch: Deny writable opens of the existing lock and observe the held guard.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # service_plan/monkeypatch — воспроизводим права после работы виртуального
    # аккаунта: оператор читает lock, но не может его перезаписать или пересоздать.
    path = service_plan.deployment.control_dir / "manager.lock"
    owner = ProcessLock(path)
    owner.open()
    owner.close()
    before = path.read_bytes()
    original = Path.open

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Разрешение только чтения существующего служебного файла
    #--------------------------------------------------------------------------------------------------------------
    def readonly(
        target: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
        ) -> object:

        """
        Simulate the operator's read-only data permission without changing native ACL.

        :param target: File selected by the tested code.
        :type target: Path

        :param mode: Requested Python file mode.
        :type mode: str

        :param args: Remaining original open arguments.
        :type args: object

        :param kwargs: Remaining original open keyword arguments.
        :type kwargs: object

        :return: Original file object when access is allowed.
        :rtype: object
        """

        # target/mode/args/kwargs — запрет записи не должен превращаться в busy.
        if target == path and any(flag in mode for flag in "wa+"):
            raise PermissionError(errno.EACCES, "synthetic-private-path")
        return original(target, mode, *args, **kwargs)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(Path, "open", readonly)
    item = backend(service_plan)
    missing = OSError("synthetic-missing-service")
    missing.winerror = 1060
    item.api.OpenService.side_effect = missing

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка непрерывного владения во время изменения прав
    #--------------------------------------------------------------------------------------------------------------
    def protected(
        *args: object,
        ) -> None:

        """
        Reject a competing read-only guard while installation is mutating ACL.

        :param args: Ignored fake grant parameters; no native ACL is changed.
        :type args: object
        """

        # args — используем тот же механизм ОС, а не фиктивный флаг владения.
        with pytest.raises(ServiceControlError, match="service_manager_running"):
            with installation_owner(path):
                pytest.fail("competing owner admitted")
    #--------------------------------------------------------------------------------------------------------------

    item._grant.side_effect = protected
    item.install(service_plan)
    assert path.read_bytes() == before
    item.api.CreateService.assert_called_once()
    with installation_owner(path):
        pass
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ доступа не выдаётся за работающий manager
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("failure, code", [(errno.EACCES, "service_control_access"),
                                         (errno.EIO, "service_control_io")])
def test_owner_access_error(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    failure: int,
    code: str,
    ) -> None:

    """
    Preserve distinct fixed access and I/O failures before any native service mutation.

    :param service_plan: Original synthetic deployment.
    :type service_plan: ServicePlan

    :param monkeypatch: Inject only the lock file open failure.
    :type monkeypatch: pytest.MonkeyPatch

    :param failure: OS errno selected for the denied open.
    :type failure: int

    :param code: Expected safe ownership category.
    :type code: str
    """

    # service_plan/monkeypatch/failure/code — ошибка не раскрывает путь и не
    # разрешает обходить владение через удаление lock или изменение DACL.
    path = service_plan.deployment.control_dir / "manager.lock"
    original = Path.open

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Подмена только открытия файла блокировки
    #--------------------------------------------------------------------------------------------------------------
    def denied(
        target: Path,
        *args: object,
        **kwargs: object,
        ) -> object:

        """
        Raise a private OS error only for the selected lock file.

        :param target: Requested file path.
        :type target: Path

        :param args: Forwarded original positional arguments.
        :type args: object

        :param kwargs: Forwarded original keyword arguments.
        :type kwargs: object

        :return: Original file object for unrelated paths.
        :rtype: object
        """

        # target/args/kwargs — остальные проверки исходных файлов остаются настоящими.
        if target == path:
            raise OSError(failure, "synthetic-private-path")
        return original(target, *args, **kwargs)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(Path, "open", denied)
    item = backend(service_plan)
    with pytest.raises(ServiceControlError) as caught:
        item.install(service_plan)
    assert caught.value.code == code and caught.value.stage == "ownership"
    assert "synthetic-private-path" not in str(caught.value)
    item.api.OpenSCManager.assert_not_called()
    item._grant.assert_not_called()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящий Windows ACL разрешает повторную проверку только для чтения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows read-only ACL")
def test_native_readonly_file(
    tmp_path: Path,
    ) -> None:

    """
    Acquire the real byte-range guard with a genuinely read-only operator data ACL.

    :param tmp_path: Isolated file whose original DACL is always restored.
    :type tmp_path: pathlib.Path
    """

    # tmp_path — меняем права только собственного тестового файла, не службы,
    # рабочего control, SQLite или CA. Владелец сохраняет право восстановить DACL.
    security = pytest.importorskip("win32security")
    api = pytest.importorskip("win32api")
    path = tmp_path / "manager.lock"
    path.write_bytes(b"0")
    original = security.GetFileSecurity(str(path), 4)
    token = security.OpenProcessToken(api.GetCurrentProcess(), 8)
    try:
        sid = security.GetTokenInformation(token, 1)[0]
    finally:
        token.Close()
    acl = security.ACL()
    acl.AddAccessAllowedAce(2, 0x120089, sid)
    descriptor = security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, acl, 0)
    try:
        security.SetFileSecurity(str(path), 4, descriptor)
        with pytest.raises(PermissionError):
            with path.open("r+b"):
                pass
        with installation_owner(path):
            assert path.stat().st_size == 1
    finally:
        security.SetFileSecurity(str(path), 4, original)
    assert path.read_bytes() == b"0"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Одна иконка следует SCM и настройке ui без повторного запуска сервера
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("states, settings, visibility", [
    (["running", "stopped", "running"], [True, True, True], [True, False, True]),
    (["running", "running", "running"], [True, False, True], [True, False, True]),
    (["stopped", "starting", "stopping", "stopped"], [True] * 4, [False, True, True, False]),
])
def test_controller_visibility(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    states: list[str],
    settings: list[bool],
    visibility: list[bool],
    ) -> None:

    """
    Hide and restore one GUI loop across service restarts and live UI preference changes.

    :param service_plan: Synthetic exact service binding.
    :type service_plan: ServicePlan

    :param monkeypatch: Scoped native and UI replacements.
    :type monkeypatch: pytest.MonkeyPatch

    :param tmp_path: Isolated user profile holding the controller lock.
    :type tmp_path: pathlib.Path

    :param states: Consecutive observed SCM states.
    :type states: list[str]

    :param settings: UI-enabled preference at each observation.
    :type settings: list[bool]

    :param visibility: Expected icon visibility for those observations.
    :type visibility: list[bool]
    """

    # service_plan/monkeypatch/tmp_path/states/settings/visibility — работа службы
    # не привязана к иконке; контроллер не вызывает start, не создаёт manager/reader.
    tray, native = controller_environment(monkeypatch, tmp_path)
    native.status.side_effect = [{"state": state} for state in states]
    monkeypatch.setattr(controller, "tray_settings", Mock(side_effect=[(True, None)] +
        [(setting, None) for setting in settings]))
    tray.set_visible.side_effect = lambda visible: setattr(tray, "ready", native.status.call_count < len(states))
    assert controller.run_tray(service_plan.spec) == 0
    tray.start.assert_called_once_with(visible=False)
    assert tray.set_visible.call_args_list == [call(value) for value in visibility]
    assert "exit_callback" not in controller.GatewayTray.call_args.kwargs
    tray.close.assert_called_once()
    native.start.assert_not_called()
    native.stop.assert_not_called()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : CLI запрашивает только скрытый процесс выбранного оператора
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows interactive controller")
@pytest.mark.parametrize("selection", ["tray", "headless", "foreign", "session_zero"])
def test_tray_launch(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    selection: str,
    ) -> None:

    """
    Limit bootstrap to the current operator's nonzero session with no console or gateway child.

    :param service_plan: Explicit original service invocation.
    :type service_plan: ServicePlan

    :param monkeypatch: Replace process creation with an observation-only mock.
    :type monkeypatch: pytest.MonkeyPatch

    :param tmp_path: Isolated controller profile.
    :type tmp_path: pathlib.Path

    :param selection: UI mode or session authorization variant.
    :type selection: str
    """

    # service_plan/monkeypatch/tmp_path/selection — не запускаем настоящий pythonw
    # и не передаём GUI в другой сеанс ради обхода ограничений Windows.
    controller_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(controller, "tray_settings", Mock(return_value=(selection != "headless", None)))
    if selection == "foreign":
        monkeypatch.setattr(controller, "current_sid", lambda: "different-operator")
    if selection == "session_zero":
        controller.ctypes.windll.kernel32.ProcessIdToSessionId.side_effect = (
            lambda pid, ptr: setattr(ptr._obj, "value", 0) or 1)
    spawn = Mock()
    monkeypatch.setattr(controller.subprocess, "Popen", spawn)
    result = controller.launch_tray(service_plan.spec)
    if selection == "tray":
        assert result == "launch_requested"
        assert spawn.call_args.args[0] == service_plan.spec.arguments("tray")
        assert spawn.call_args.kwargs["creationflags"] == 0x08000000
        assert spawn.call_args.kwargs["stdout"] == controller.subprocess.DEVNULL
    else:
        assert result in ("headless", "operator_session_required")
        spawn.assert_not_called()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : CLI сохраняет точную безопасную причину отказа доступа к control
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows service CLI")
def test_owner_cli(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    ) -> None:

    """
    Report ownership access separately from an active manager without exposing exception text.

    :param service_plan: Synthetic service configuration.
    :type service_plan: ServicePlan

    :param monkeypatch: Substitute the native backend with one fixed access error.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured safe CLI output.
    :type capsys: pytest.CaptureFixture
    """

    # service_plan/monkeypatch/capsys — чтение CLI не обращается к реальному SCM.
    native = Mock()
    native.status.side_effect = ServiceControlError("service_control_access", 5)
    monkeypatch.setattr(service_windows, "WindowsService", Mock(return_value=native))
    assert main(["--config", str(service_plan.spec.config), "--operator-sid", SID, "--status"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"code": "service_control_access", "stage": "ownership", "winerror": 5}
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Принятый SCM запуск не становится отказом при недоступной иконке
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows service CLI")
@pytest.mark.parametrize("available", [False, True])
@pytest.mark.parametrize("action", ["start", "sync-tray"])
def test_start_cli_tray(
    service_plan: ServicePlan,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    available: bool,
    action: str,
    ) -> None:

    """
    Bootstrap the operator controller after accepted startup without rolling back a running service.

    :param service_plan: Original synthetic service configuration.
    :type service_plan: ServicePlan

    :param monkeypatch: Replace SCM and hidden process launch with safe mocks.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured action result without private errors.
    :type capsys: pytest.CaptureFixture

    :param available: Whether the optional controller launch succeeds.
    :type available: bool

    :param action: Startup or UI-registration action that requests the operator controller.
    :type action: str
    """

    # service_plan/monkeypatch/capsys/available/action — отсутствие GUI не влияет на
    # основной сервер и не должно побуждать оператора повторно запускать службу.
    native = Mock()
    monkeypatch.setattr(service_windows, "WindowsService", Mock(return_value=native))
    monkeypatch.setattr(service_cli, "make_plan", Mock(return_value=service_plan))
    launch = Mock(return_value="launch_requested")
    if not available:
        launch.side_effect = RuntimeError("synthetic-private-path")
    monkeypatch.setattr(controller, "launch_tray", launch)
    assert main(["--config", str(service_plan.spec.config), "--operator-sid", SID, "--" + action]) == 0
    if action == "start":
        native.start.assert_called_once()
    else:
        native.start.assert_not_called()
        native.sync_tray.assert_called_once_with(True)
    native.stop.assert_not_called()
    launch.assert_called_once()
    text = capsys.readouterr().out
    result = json.loads(text)
    assert result["accepted"] and result["tray"] == ("launch_requested" if available else "unavailable")
    assert "synthetic-private-path" not in text and SID not in text
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Уже выданные права не требуют повторного изменения служебных файлов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["allowed", "deny", "missing", "partial", "no_inheritance", "child_missing"])
def test_existing_grant(
    service_plan: ServicePlan,
    tmp_path: Path,
    case: str,
    ) -> None:

    """
    Reuse only fully observed service access while rejecting denied, incomplete or uncertain ACL.

    :param service_plan: Fake SCM/security backend without native registration.
    :type service_plan: ServicePlan

    :param tmp_path: Isolated directory and child standing in for service-owned runtime files.
    :type tmp_path: pathlib.Path

    :param case: Grant condition applied to the root or child ACL.
    :type case: str
    """

    # service_plan/tmp_path/case — оператор может читать служебный файл, но не
    # менять его DACL. Быстрый путь допустим только при уже достаточных правах SID.
    root = tmp_path / "private-runtime"
    root.mkdir()
    child = root / "service.log"
    child.write_bytes(b"synthetic")
    sid = "S-1-5-80-1-2-3-4-5"
    flags = 0 if case == "no_inheritance" else 3
    mask = 0x120089 if case == "partial" else 0x1F01FF
    entries = [((0, flags), mask, "different" if case == "missing" else sid)]
    if case == "deny":
        entries.append(((1, 0), 0x1F01FF, "another-group"))
    acl = SimpleNamespace(GetAceCount=lambda: len(entries), GetAce=lambda index: entries[index])
    missing_acl = SimpleNamespace(GetAceCount=lambda: 0)
    descriptor = SimpleNamespace(GetSecurityDescriptorDacl=lambda: acl)
    missing_descriptor = SimpleNamespace(GetSecurityDescriptorDacl=lambda: missing_acl)
    item = backend(service_plan)
    item.security.ConvertSidToStringSid.side_effect = lambda principal: principal
    item.security.GetFileSecurity.side_effect = lambda name, kind: (
        missing_descriptor if case == "child_missing" and Path(name) == child else descriptor)
    assert item._has_grant(root, sid, "(OI)(CI)F", True) is (case == "allowed")
    if case == "allowed":
        # Не запускается даже icacls: тест работает и в core без pywin32.
        WindowsService._grant(item, root, sid, "(OI)(CI)F", True)
    assert child.read_bytes() == b"synthetic"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Тестовый модуль не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
