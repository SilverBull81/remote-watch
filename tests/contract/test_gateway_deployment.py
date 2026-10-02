# Проверки общей конфигурации deployment без запуска сети и рабочих журналов.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-184007
#
# Тесты:
# -> deployment(): Создание только подставных конфигов в отдельной папке.
# -> test_modes_are_read_only(): Три режима без сети, секретов окружения и новых файлов.
# -> test_invalid_deployment(): Отказ при противоречиях и неверных типах.
# -> test_json_failures(): Строгий ограниченный JSON и приватные сообщения.
# -> test_managed_caddy(): Явные пути собственного Caddy без исполнения binary.
# -> test_component_failure(): Проверка вложенного конфига и безопасная подсказка.
# -> test_cli(): Успех, отказ и обязательность явного режима проверки.
# -> test_typed_models(): Ограничения Python API и приватность repr.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from remote_watch.gateway_server import (
    CaddyConfig,
    ComponentConfig,
    DeploymentConfigError,
    LifecycleConfig,
    load_deployment_config,
)
from remote_watch.gateway_server.__main__ import main


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание только подставных конфигов в отдельной папке
#------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def deployment(tmp_path: Path) -> tuple[Path, dict[str, Any]]:

    """Copy public synthetic examples into a deployment with nested Unicode paths.

    :param tmp_path: Isolated temporary directory.
    :type tmp_path: Path

    :return: Deployment location and editable JSON structure.
    :rtype: tuple[Path, dict[str, Any]]
    """

    # tmp_path — сюда попадают только открытые шаблоны, никогда реальные credentials.

    directory = tmp_path / "с пробелом" / "настройки"
    directory.mkdir(parents=True)
    examples = Path(__file__).resolve().parents[2] / "docs" / "examples"
    for source, target in (("gateway_config.example.json", "notifications.json"),
                           ("command_gateway.telegram.example.json", "commands.json")):
        (directory / target).write_bytes((examples / source).read_bytes())

    value = {"schema_version": 1, "endpoint": "https://lv.example.test:8443",
             "control_dir": "control", "log_dir": "logs",
             "notifications": {"config": "с пробелом/настройки/notifications.json", "port": 8765},
             "commands": {"config": "с пробелом/настройки/commands.json", "port": 8766}}
    return tmp_path / "deployment.json", value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Три режима без сети, секретов окружения и новых файлов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["notifications", "commands", "both"])
def test_modes_are_read_only(
    deployment: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:

    """Validate enabled components from another cwd without opening runtime resources.

    :param deployment: Synthetic file locations and JSON settings.
    :type deployment: tuple[Path, dict[str, Any]]

    :param monkeypatch: Scoped resource and working-directory overrides.
    :type monkeypatch: pytest.MonkeyPatch

    :param mode: Selected component combination.
    :type mode: str
    """

    # deployment — подставные настройки без рабочих данных.
    # monkeypatch — запрещает скрытые обращения к среде, сети, процессам и SQLite.
    # mode — проверяем, что отключённый компонент не требует своего файла.

    path, value = deployment
    if mode != "both":
        disabled = "commands" if mode == "notifications" else "notifications"
        (path.parent / value.pop(disabled)["config"]).unlink()
    path.write_text(json.dumps(value), encoding="utf-8")
    before = {item.relative_to(path.parent): item.read_bytes()
              for item in path.parent.rglob("*") if item.is_file()}

    with monkeypatch.context() as patch:
        patch.chdir(path.parent.parent)
        patch.setattr(os, "environ", None)
        patch.setattr(socket, "socket", Mock(side_effect=AssertionError("network opened")))
        patch.setattr(socket, "getaddrinfo", Mock(side_effect=AssertionError("DNS queried")))
        patch.setattr(sqlite3, "connect", Mock(side_effect=AssertionError("journal opened")))
        patch.setattr(subprocess, "Popen", Mock(side_effect=AssertionError("process launched")))
        config = load_deployment_config(path)

    assert config.control_dir == path.parent / "control"
    for name in ("notifications", "commands"):
        component = getattr(config, name)
        if name in value:
            assert component.config == (path.parent / value[name]["config"]).resolve()
        else:
            assert component is None
    after = {item.relative_to(path.parent): item.read_bytes() for item in path.parent.rglob("*") if item.is_file()}
    assert before == after
    assert not config.control_dir.exists()
    assert not config.log_dir.exists()
    assert not list(path.parent.rglob("data"))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ при противоречиях и неверных типах
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("section", "replacement"), [
    ("schema_version", True), ("schema_version", 2), ("secret-unknown", "secret-value"),
    ("endpoint", "http://lv:8443"), ("endpoint", "https://token@lv:8443"),
    ("endpoint", "https://lv:8443/path"), ("endpoint", "https://lv:8443?token=secret-value"),
    ("endpoint", "https://lv:0"), ("endpoint", "https://lv:"), ("endpoint", "https://bad_host"),
    ("endpoint", "https://lv:8765"), ("endpoint", "https://lv\n"),
    ("endpoint", "https://lv:8443#secret-value"), ("endpoint", 42),
    ("control_dir", "logs/control"), ("control_dir", "logs"), ("control_dir", ""),
    ("notifications", None), ("notifications", {}),
    ("notifications", {"config": "missing", "port": True}),
    ("notifications", {"config": "missing", "port": 65536}),
    ("notifications", {"config": "missing", "port": 8766}),
    ("notifications", {"config": "missing", "port": 8765, "host": "0.0.0.0"}),
    ("notifications", {"config": "missing", "port": 8765, "host": "localhost"}),
    ("caddy", {"mode": "external", "config": "secret-value"}), ("caddy", {"mode": "managed"}),
    ("caddy", {"mode": "unknown"}), ("restart", {"max_restarts": 101}),
    ("restart", {"window": 1}), ("restart", {"initial_delay": True}),
    ("restart", {"max_delay": 1e300}), ("restart", {"max_restarts": -1}),
    ("lifecycle", {"heartbeat_timeout": 2}), ("lifecycle", {"heartbeat_interval": 0.01}),
    ("lifecycle", {"startup_timeout": 2}), ("lifecycle", {"kill_timeout": 0}),
])
def test_invalid_deployment(
    deployment: tuple[Path, dict[str, Any]],
    section: str,
    replacement: object,
) -> None:

    """Reject conflicting settings without revealing private values in diagnostics.

    :param deployment: Synthetic deployment fixture.
    :type deployment: tuple[Path, dict[str, Any]]

    :param section: Root field to replace or add.
    :type section: str

    :param replacement: Invalid value for the selected field.
    :type replacement: object
    """

    # deployment — исходно допустимые настройки.
    # section — изменяемое поле схемы.
    # replacement — ошибка, которая не должна попасть в текст исключения.

    path, value = deployment
    value[section] = replacement
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DeploymentConfigError) as caught:
        load_deployment_config(path)
    assert "secret-value" not in str(caught.value)
    assert "secret-unknown" not in str(caught.value)
    assert str(path) not in str(caught.value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Строгий ограниченный JSON и приватные сообщения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("content", "code"), [
    (None, "config_read"), (b"\xff", "config_json"), (b"{", "config_json"),
    (b'{"schema_version":1,"schema_version":1}', "config_json"),
    (b'{"restart":{"window":NaN}}', "config_json"), (b" " * (1024 * 1024 + 1), "config_size"),
    (b"[1,2]", "config_value"), (b"[" * 1100, "config_json"),
], ids=["absent", "encoding", "syntax", "duplicate", "nan", "oversized", "array", "depth"])
def test_json_failures(
    tmp_path: Path,
    content: bytes | None,
    code: str,
) -> None:

    """Bound the read and reject malformed JSON with fixed diagnostic codes.

    :param tmp_path: Isolated file directory.
    :type tmp_path: Path

    :param content: File bytes, or None to leave the file absent.
    :type content: bytes | None

    :param code: Expected public error code.
    :type code: str
    """

    # tmp_path — отдельный каталог.
    # content — ошибочные байты, без реальных конфигов.
    # code — ожидаемая безопасная категория отказа.

    path = tmp_path / "secret-path.json"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(DeploymentConfigError) as caught:
        load_deployment_config(path)
    assert caught.value.code == code
    assert "secret-path" not in str(caught.value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Явные пути собственного Caddy без исполнения binary
#------------------------------------------------------------------------------------------------------------------
def test_managed_caddy(deployment: tuple[Path, dict[str, Any]]) -> None:

    """Check managed paths without executing Caddy or replacing existing CA data.

    :param deployment: Synthetic deployment fixture.
    :type deployment: tuple[Path, dict[str, Any]]
    """

    # deployment — пустой binary намеренно не может пройти настоящий запуск.

    path, value = deployment
    (path.parent / "caddy.exe").write_bytes(b"")
    (path.parent / "Caddyfile").write_text("synthetic-invalid-caddyfile", encoding="utf-8")
    data = path.parent / "existing-ca"
    data.mkdir()
    marker = data / "synthetic-marker"
    marker.write_bytes(b"keep-existing-ca")
    value["caddy"] = {"mode": "managed", "executable": "caddy.exe", "config": "Caddyfile",
                      "data_dir": "existing-ca"}
    path.write_text(json.dumps(value), encoding="utf-8-sig")
    config = load_deployment_config(path)
    assert config.caddy.data_dir == data
    assert marker.read_bytes() == b"keep-existing-ca"
    assert "existing-ca" not in repr(config)

    (path.parent / "caddy.exe").unlink()
    with pytest.raises(DeploymentConfigError) as caught:
        load_deployment_config(path)
    assert (caught.value.code, caught.value.field) == ("path_unavailable", "caddy")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка вложенного конфига и безопасная подсказка
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("component", ["notifications", "commands"])
def test_component_failure(
    deployment: tuple[Path, dict[str, Any]],
    component: str,
) -> None:

    """Report which existing component configuration needs its dedicated validator.

    :param deployment: Synthetic deployment fixture.
    :type deployment: tuple[Path, dict[str, Any]]

    :param component: Enabled component whose JSON is invalidated.
    :type component: str
    """

    # deployment — общая схема остаётся допустимой.
    # component — только этот связанный файл содержит подставную ошибку.

    path, value = deployment
    path.write_text(json.dumps(value), encoding="utf-8")
    (path.parent / value[component]["config"]).write_text('{"secret-value":123}', encoding="utf-8")
    with pytest.raises(DeploymentConfigError) as caught:
        load_deployment_config(path)
    assert (caught.value.code, caught.value.field) == ("component_config", component)
    assert "secret-value" not in str(caught.value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Успех, отказ и обязательность явного режима проверки
#------------------------------------------------------------------------------------------------------------------
def test_cli(
    deployment: tuple[Path, dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:

    """Check honest CLI outcomes and private parse errors without process startup.

    :param deployment: Synthetic deployment fixture.
    :type deployment: tuple[Path, dict[str, Any]]

    :param capsys: Captured console output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # deployment — только подставные файлы.
    # capsys — проверка понятного результата и отсутствия приватных аргументов.

    path, value = deployment
    path.write_text(json.dumps(value), encoding="utf-8")
    assert main(["--config", str(path), "--check-config"]) == 0
    assert "Процессы не запускались" in capsys.readouterr().out

    for argv in (["--config", str(path)], ["--config", str(path), "--check-config", "--secret-value"]):
        with pytest.raises(SystemExit) as caught:
            main(argv)
        assert caught.value.code == 2
        output = capsys.readouterr()
        assert "secret-value" not in output.err
        assert str(path) not in output.err

    path.unlink()
    assert main(["--config", str(path), "--check-config"]) == 1
    assert "code=config_read field=config" in capsys.readouterr().out
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничения Python API и приватность repr
#------------------------------------------------------------------------------------------------------------------
def test_typed_models(deployment: tuple[Path, dict[str, Any]]) -> None:

    """Apply essential invariants to Python construction as well as JSON parsing.

    :param deployment: Synthetic deployment fixture.
    :type deployment: tuple[Path, dict[str, Any]]
    """

    # deployment — источник корректной typed-модели для независимых изменений.

    path, value = deployment
    path.write_text(json.dumps(value), encoding="utf-8")
    config = load_deployment_config(path)
    assert "lv.example.test" not in repr(config)
    assert str(path.parent) not in repr(config)
    with pytest.raises(ValueError):
        replace(config, notifications=None, commands=None)
    with pytest.raises(ValueError):
        ComponentConfig(config=Path("relative"), port=8765)
    with pytest.raises(ValueError):
        CaddyConfig(mode="managed")
    with pytest.raises(ValueError):
        LifecycleConfig(heartbeat_interval=float("nan"))
    with pytest.raises(ValueError):
        replace(config, caddy=CaddyConfig(mode="managed", executable=path, config=path,
                                          data_dir=config.log_dir / "ca"))

    error = DeploymentConfigError("secret-value", "secret-field")
    assert (error.code, error.field) == ("config_value", "config")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль тестов не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
