# Проверка эксплуатационных границ, уточнённых при подготовке релиза.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-185741
#
# Тесты:
# -> test_shared_log_directory(): Чужие файлы отвергаются до изменения прав и запуска
#
# -> test_transition_log(): Возраст и счётчики журналов не создают ложные переходы состояния


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from remote_watch.gateway_server.config import ComponentConfig, DeploymentConfig, DeploymentConfigError
from remote_watch.gateway_server.manager import GatewayManager

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Чужие файлы отвергаются до изменения прав и запуска
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("foreign", ["caddy-runtime.log", "gateway_server.jsonl"])
def test_shared_log_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    foreign: str,
    ) -> None:

    """
    Reject foreign log entries before acquiring ownership or changing any directory ACL.

    :param tmp_path: Isolated deployment root without real service state.
    :type tmp_path: Path

    :param monkeypatch: Replacement for the permission-changing operation.
    :type monkeypatch: pytest.MonkeyPatch

    :param foreign: Foreign file or directory imitating the manager log filename.
    :type foreign: str
    """

    # tmp_path/monkeypatch — проверка не меняет настоящие права и не запускает процессы.
    # foreign — даже разрешённое имя не даёт manager права присвоить чужую подпапку.

    logs = tmp_path / "logs"
    logs.mkdir()
    entry = logs / foreign
    if foreign.endswith(".log"):
        entry.write_bytes(b"external writer data")
    else:
        entry.mkdir()

    permissions = Mock(side_effect=AssertionError("permissions must remain unchanged"))
    monkeypatch.setattr("remote_watch.gateway_server.manager.private_directory", permissions)
    config = DeploymentConfig(endpoint="https://gateway.example.test:8443", control_dir=tmp_path / "control",
                              log_dir=logs, notifications=ComponentConfig(tmp_path / "notification.json", 9765))
    with pytest.raises(DeploymentConfigError) as caught:
        asyncio.run(GatewayManager(config).run())

    assert caught.value.code == "log_directory_shared"
    assert caught.value.field == "log_dir"
    permissions.assert_not_called()
    assert not config.control_dir.exists()
    assert entry.is_dir() or entry.read_bytes() == b"external writer data"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Возраст и счётчики журналов не создают ложные переходы состояния
#------------------------------------------------------------------------------------------------------------------
def test_transition_log(
    tmp_path: Path,
    ) -> None:

    """
    Log health and capacity transitions while ignoring ordinary sample-age and counter updates.

    :param tmp_path: Isolated paths retained by an unstarted manager.
    :type tmp_path: Path
    """

    # tmp_path — manager не запускается; чтение status и запись журнала подменены.

    config = DeploymentConfig(endpoint="https://gateway.example.test:8443", control_dir=tmp_path / "control",
                              log_dir=tmp_path / "logs", notifications=ComponentConfig(tmp_path / "n.json", 9765))
    manager = GatewayManager(config)
    journal = {"state": "available", "age_seconds": 0, "snapshot": {"capacity": "ok", "records": 1}}
    component = {"state": "ready", "reason": None, "counters": {"active": 0},
                 "journals": {"hub": journal, "sources": []}}
    manager.status = Mock(return_value={"alerts": "disabled", "ui": "headless", "logging": "available",
                                        "components": {"notifications": component}})
    manager._log.info = Mock()

    manager._report()
    journal["age_seconds"] = 5
    journal["snapshot"]["records"] = 2
    component["counters"]["active"] = 1
    manager._report()
    assert manager._log.info.call_count == 1

    # Изменение ёмкости и отказ наблюдения остаются настоящими переходами.
    journal["snapshot"]["capacity"] = "full"
    manager._report()
    assert manager._log.info.call_count == 2
    journal["state"] = "unavailable"
    manager._report()
    assert manager._log.info.call_count == 3
    logged = json.loads(manager._log.info.call_args.args[0])
    assert logged["components"]["notifications"]["journals"]["hub"] == {
        "state": "unavailable", "capacity": "full"}
    assert journal["snapshot"]["records"] == 2
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Запускайте тесты через pytest.")
#------------------------------------------------------------------------------------------------------------------
