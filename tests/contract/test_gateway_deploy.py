# Проверки комплекта установки, сохранения рабочих данных и безопасного CLI.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Тесты:
# -> test_new_installation(): Согласованность новой установки и отсутствие побочных действий
#
# -> test_existing_installation(): Сохранение прежних конфигов и постоянного состояния
#
# -> test_dry_run_and_updates(): Preview и отдельные предложения обновления
#
# -> test_validation(): Полная проверка аргументов до записи
#
# -> test_output_links(): Запрет записи через ссылки
#
# -> test_exclusive_write(): Отказ от перезаписи при конкурентном создании
#
# -> test_managed(): Явное владение Caddy и сохранение его конфига
#
# -> test_cli(): Безопасный вывод ошибок и различий
#
# -> test_launchers(): Настоящий запуск PowerShell или POSIX shell из другой папки
#
# -> test_preflight_conflict(): Проверка всего плана перед первой записью


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from unittest.mock import Mock

import pytest

from remote_watch.gateway_server.config import load_deployment_config
from remote_watch.gateway_server.deploy import main
from remote_watch.gateway_server.deployment import DeployError, DeployOptions, _write_new, deploy, prepare_files
from remote_watch.gateway_server.tray import default_icon

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Согласованность новой установки и отсутствие побочных действий
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("components", ["both", "notifications", "commands"])
def test_new_installation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    components: str,
    ) -> None:

    """
    Render and validate all enabled components without network, subprocesses or SQLite.

    :param tmp_path: Isolated output directory.
    :type tmp_path: Path

    :param monkeypatch: Forbidden side-effect overrides.
    :type monkeypatch: pytest.MonkeyPatch

    :param components: Enabled component selection.
    :type components: str
    """

    # tmp_path/monkeypatch/components — реальные файлы разрешены только внутри теста.
    root = tmp_path / "Новая установка с пробелами"
    options = DeployOptions(directory=root, endpoint="https://lv.example.test:8443", components=components)
    with monkeypatch.context() as patch:
        patch.setattr(socket, "socket", Mock(side_effect=AssertionError("network")))
        patch.setattr(sqlite3, "connect", Mock(side_effect=AssertionError("storage")))
        patch.setattr(subprocess, "Popen", Mock(side_effect=AssertionError("process")))
        patch.setattr(os, "getenv", Mock(side_effect=AssertionError("environment")))
        assert all(state == "create" for _, state in deploy(options))
        config = load_deployment_config(root / "gateway_server.local.json")
    assert config.endpoint == options.endpoint and config.caddy.mode == "external"
    assert config.application.icon_path == root / "logo" / "remote_watch.ico"
    assert config.application.icon_path.read_bytes() == default_icon().read_bytes()
    for component in ("notifications", "commands"):
        assert (getattr(config, component) is not None) == (components in ("both", component))
    caddy = (root / "Caddyfile.example").read_text(encoding="utf-8")
    assert "https://lv.example.test:8443" in caddy and "handle_path" not in caddy
    assert ("127.0.0.1:8766" in caddy) == (components in ("both", "commands"))
    assert ("127.0.0.1:8765" in caddy) == (components in ("both", "notifications"))
    for client in (root / "clients").glob("*.json"):
        assert json.loads(client.read_text())["endpoint"] == options.endpoint
    assert not (root / "data").exists() and not (root / "caddy-data").exists()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение прежних конфигов и постоянного состояния
#------------------------------------------------------------------------------------------------------------------
def test_existing_installation(
    tmp_path: Path,
    ) -> None:

    """
    Preserve private configs byte-for-byte and retain their original relative-path base.

    :param tmp_path: Isolated existing installation and output root.
    :type tmp_path: Path
    """

    # tmp_path — содержимое намеренно не является корректным JSON: deploy его не разбирает.
    previous = tmp_path / "old"
    previous.mkdir()
    for name in ("notifications.json", "commands.json", "root.crt", "commands.sqlite", "Caddyfile"):
        (previous / name).write_bytes(b"PRIVATE-original-bytes")
    before = {item: item.read_bytes() for item in previous.iterdir()}
    root = tmp_path / "new"
    options = DeployOptions(directory=root, endpoint="https://lv.example.test:8443",
                            notifications_config=previous / "notifications.json",
                            commands_config=previous / "commands.json")
    deploy(options)
    config = json.loads((root / "gateway_server.local.json").read_text())
    assert config["commands"]["config"] == str(previous / "commands.json")
    assert not (root / "command_gateway.local.json").exists()
    (root / "gateway_server.local.json").write_bytes(b"PRIVATE-operator-change")
    (root / "logo" / "remote_watch.ico").write_bytes(b"operator-custom-icon")
    report = dict(deploy(options))
    assert report["gateway_server.local.json"] == "preserved"
    assert report["logo/remote_watch.ico"] == "preserved"
    assert (root / "logo" / "remote_watch.ico").read_bytes() == b"operator-custom-icon"
    assert (root / "gateway_server.local.json").read_bytes() == b"PRIVATE-operator-change"
    assert all(path.read_bytes() == data for path, data in before.items())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Preview и отдельные предложения обновления
#------------------------------------------------------------------------------------------------------------------
def test_dry_run_and_updates(
    tmp_path: Path,
    ) -> None:

    """
    Avoid all writes during preview and keep updates outside operational configuration.

    :param tmp_path: Isolated output parent.
    :type tmp_path: Path
    """

    # tmp_path — проверяем даже отсутствие каталога после dry-run.
    root = tmp_path / "new"
    options = DeployOptions(directory=root, endpoint="https://lv.example.test:8443")
    deploy(options, dry_run=True)
    assert not root.exists()
    deploy(options)
    old = (root / "gateway_server.local.json").read_bytes()
    newer = replace(options, endpoint="https://lv.example.test:9443")
    report = dict(deploy(newer, update_templates=True, dry_run=True))
    assert report["gateway_server.local.json"] == "current_preserved"
    assert not (root / "templates").exists()
    deploy(newer, update_templates=True)
    assert (root / "gateway_server.local.json").read_bytes() == old
    proposal = root / "templates" / version("remote-watch") / "gateway_server.local.json"
    assert json.loads(proposal.read_text())["endpoint"] == newer.endpoint
    assert all(state == "identical" for _, state in deploy(options))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полная проверка аргументов до записи
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change", [
    {"endpoint": "http://lv.example.test"}, {"endpoint": "https://user:PRIVATE@lv.example.test"},
    {"components": "PRIVATE"}, {"notification_port": True}, {"command_port": 8765},
    {"endpoint": "https://lv.example.test:8765"}, {"caddy_mode": "managed"},
    {"caddy_mode": "PRIVATE"}, {"ui": "PRIVATE"}, {"python": Path("missing-python")},
])
def test_validation(
    tmp_path: Path,
    change: dict,
    ) -> None:

    """
    Reject invalid choices before the installation directory exists.

    :param tmp_path: Isolated output parent.
    :type tmp_path: Path

    :param change: Invalid replacement option.
    :type change: dict
    """

    # tmp_path/change — заведомо ошибочные настройки не должны оставлять файлы.
    root = tmp_path / "new"
    options = DeployOptions(directory=root, endpoint="https://lv.example.test:8443")
    with pytest.raises((ValueError, OSError)):
        deploy(replace(options, **change))
    assert not root.exists()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет записи через ссылки
#------------------------------------------------------------------------------------------------------------------
def test_output_links(
    tmp_path: Path,
    ) -> None:

    """
    Refuse linked output directories without modifying the link destination.

    :param tmp_path: Isolated symlink and destination.
    :type tmp_path: Path
    """

    # tmp_path — Windows без права создания symlink пропускает только этот сценарий.
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("symlink privilege unavailable")
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    with pytest.raises(DeployError, match="linked_path"):
        deploy(DeployOptions(directory=link, endpoint="https://lv.example.test"))
    assert list(target.iterdir()) == []
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ от перезаписи при конкурентном создании
#------------------------------------------------------------------------------------------------------------------
def test_exclusive_write(
    tmp_path: Path,
    ) -> None:

    """
    Keep a file that appeared after the deployment preflight unchanged.

    :param tmp_path: Isolated output directory.
    :type tmp_path: Path
    """

    # tmp_path — O_EXCL должен сохранять чужое содержимое, а не обновлять его.
    path = tmp_path / "config.json"
    path.write_bytes(b"PRIVATE")
    with pytest.raises(FileExistsError):
        _write_new(path, b"replacement")
    assert path.read_bytes() == b"PRIVATE"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Явное владение Caddy и сохранение его конфига
#------------------------------------------------------------------------------------------------------------------
def test_managed(
    tmp_path: Path,
    ) -> None:

    """
    Retain existing Caddy storage/config paths without executing the binary.

    :param tmp_path: Isolated fake Caddy and output directory.
    :type tmp_path: Path
    """

    # tmp_path — вместо бинарника обычный файл; попытка запуска была бы ошибкой.
    executable = tmp_path / "caddy.exe"
    executable.write_bytes(b"not executable")
    original = tmp_path / "OldCaddyfile"
    original.write_bytes(b"PRIVATE custom config")
    options = DeployOptions(directory=tmp_path / "new", endpoint="https://lv.example.test:8443",
                            caddy_mode="managed", caddy_executable=executable, caddy_config=original,
                            caddy_data=tmp_path / "old CA")
    deploy(options)
    config = load_deployment_config(options.directory / "gateway_server.local.json")
    assert config.caddy.config == original and config.caddy.data_dir == options.caddy_data
    assert original.read_bytes() == b"PRIVATE custom config"
    assert not options.caddy_data.exists() and not (options.directory / "Caddyfile").exists()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасный вывод ошибок и различий
#------------------------------------------------------------------------------------------------------------------
def test_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    ) -> None:

    """
    Print fixed filenames and codes without disclosing arguments or private file contents.

    :param tmp_path: Isolated deployment directory.
    :type tmp_path: Path

    :param capsys: Captured operator output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # tmp_path/capsys — секретоподобная строка не должна попадать в ошибку или diff.
    root = tmp_path / "new"
    args = ["--directory", str(root), "--endpoint", "https://lv.example.test:8443"]
    assert main(args + ["--dry-run"]) == 0 and not root.exists()
    assert main(args) == 0
    (root / "command_gateway.local.json").write_bytes(b"PRIVATE")
    assert main(args + ["--update-templates"]) == 0
    assert main(["--directory", str(root), "--endpoint", "https://u:PRIVATE@lv.example.test"]) == 1
    with pytest.raises(SystemExit):
        main(args + ["--PRIVATE"])
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err
    assert "current_preserved" in output.out and "deployment_failed" in output.out
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящий запуск PowerShell или POSIX shell из другой папки
#------------------------------------------------------------------------------------------------------------------
def test_launchers(
    tmp_path: Path,
    ) -> None:

    """
    Pass config and action as separate arguments through a real platform shell.

    :param tmp_path: Isolated generated kit containing spaces and a quote in its path.
    :type tmp_path: Path
    """

    # tmp_path — check-config работает без сети и не запускает компоненты.
    root = tmp_path / "Тест ' deploy"
    deploy(DeployOptions(directory=root, endpoint="https://lv.example.test:8443"))
    if os.name == "nt":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(root / "check-config.ps1")]
    else:
        command = [shutil.which("sh"), str(root / "check-config.sh")]
    # В source-тестах дочернему Python передаётся тот же src; wheel-тесты используют site-packages.
    import remote_watch
    environment = dict(os.environ, PYTHONPATH=str(Path(remote_watch.__file__).parent.parent))
    result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert b"Traceback" not in result.stdout
    scripts = prepare_files(DeployOptions(directory=root, endpoint="https://lv.example.test:8443"))
    assert str(Path(sys.executable)).replace("'", "''") in scripts["start.ps1"].decode("utf-8-sig")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка всего плана перед первой записью
#------------------------------------------------------------------------------------------------------------------
def test_preflight_conflict(
    tmp_path: Path,
    ) -> None:

    """
    Reject a directory occupying an output filename before creating other files.

    :param tmp_path: Existing installation directory.
    :type tmp_path: Path
    """

    # tmp_path — конфликт в конце плана не должен оставлять файлы из его начала.
    (tmp_path / "START_HERE.md").mkdir()
    with pytest.raises(DeployError, match="destination_invalid"):
        deploy(DeployOptions(directory=tmp_path, endpoint="https://lv.example.test"))
    assert [item.name for item in tmp_path.iterdir()] == ["START_HERE.md"]
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_gateway_deploy не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
