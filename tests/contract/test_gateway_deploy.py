# Проверки комплекта установки, сохранения рабочих данных и безопасного CLI.
#
# Version 1.0.4
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261103-090725
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
#
# -> test_legacy_layout(): Отказ от смешивания старой и новой структуры до записи
#
# -> test_nested_config_link(): Защита вложенной папки примеров от записи через ссылку


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
    options = DeployOptions(directory=root, endpoint="https://gateway.example.test:8443", components=components)
    with monkeypatch.context() as patch:
        patch.setattr(socket, "socket", Mock(side_effect=AssertionError("network")))
        patch.setattr(sqlite3, "connect", Mock(side_effect=AssertionError("storage")))
        patch.setattr(subprocess, "Popen", Mock(side_effect=AssertionError("process")))
        patch.setattr(os, "getenv", Mock(side_effect=AssertionError("environment")))
        assert all(state == "create" for _, state in deploy(options))
        config = load_deployment_config(root / "config/gateway_server.local.json")

    assert config.endpoint == options.endpoint and config.caddy.mode == "external"
    assert config.control_dir == root / "control"
    assert config.log_dir == root / "logs/gateway_server"
    assert config.application.icon_path == root / "logo" / "remote_watch.ico"
    assert config.application.icon_path.read_bytes() == default_icon().read_bytes()
    for component in ("notifications", "commands"):
        assert (getattr(config, component) is not None) == (components in ("both", component))
        if getattr(config, component) is not None:
            assert getattr(config, component).config.parent == root / "config"

    if config.commands is not None:
        from remote_watch.gateway.command_config import _gateway
        assert _gateway(config.commands.config, False).state_dir == root / "data/commands"

    assert {p.name for p in root.glob("*.json")} == {"deploy-manifest.json"}
    assert not (root / "clients").exists()
    assert (root / "config/examples/gateway_server.example.json").is_file()

    caddy = (root / "config/examples/Caddyfile.example").read_text(encoding="utf-8")
    assert "https://gateway.example.test:8443" in caddy and "handle_path" not in caddy
    assert ("127.0.0.1:8766" in caddy) == (components in ("both", "commands"))
    assert ("127.0.0.1:8765" in caddy) == (components in ("both", "notifications"))
    for client in (root / "config/examples").glob("*client*.json"):
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
    options = DeployOptions(directory=root, endpoint="https://gateway.example.test:8443",
                            notifications_config=previous / "notifications.json",
                            commands_config=previous / "commands.json")
    deploy(options)
    config = json.loads((root / "config/gateway_server.local.json").read_text())
    assert config["commands"]["config"] == str(previous / "commands.json")
    assert not (root / "config/command_gateway.local.json").exists()
    (root / "config/gateway_server.local.json").write_bytes(b"PRIVATE-operator-change")
    (root / "logo" / "remote_watch.ico").write_bytes(b"operator-custom-icon")
    report = dict(deploy(options))
    assert report["config/gateway_server.local.json"] == "preserved"
    assert report["logo/remote_watch.ico"] == "preserved"
    assert (root / "logo" / "remote_watch.ico").read_bytes() == b"operator-custom-icon"
    assert (root / "config/gateway_server.local.json").read_bytes() == b"PRIVATE-operator-change"
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
    options = DeployOptions(directory=root, endpoint="https://gateway.example.test:8443")
    deploy(options, dry_run=True)
    assert not root.exists()
    deploy(options)
    old = (root / "config/gateway_server.local.json").read_bytes()
    newer = replace(options, endpoint="https://gateway.example.test:9443")
    report = dict(deploy(newer, update_templates=True, dry_run=True))
    assert report["config/gateway_server.local.json"] == "current_preserved"
    assert not (root / "templates").exists()
    deploy(newer, update_templates=True)
    assert (root / "config/gateway_server.local.json").read_bytes() == old
    proposal = root / "templates" / version("remote-watch") / "config/gateway_server.local.json"
    assert json.loads(proposal.read_text())["endpoint"] == newer.endpoint
    assert all(state == "identical" for _, state in deploy(options))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полная проверка аргументов до записи
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change", [
    {"endpoint": "http://gateway.example.test"}, {"endpoint": "https://user:PRIVATE@gateway.example.test"},
    {"components": "PRIVATE"}, {"notification_port": True}, {"command_port": 8765},
    {"endpoint": "https://gateway.example.test:8765"}, {"caddy_mode": "managed"},
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
    options = DeployOptions(directory=root, endpoint="https://gateway.example.test:8443")
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
        deploy(DeployOptions(directory=link, endpoint="https://gateway.example.test"))

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
@pytest.mark.parametrize("existing", [True, False])
def test_managed(
    tmp_path: Path,
    existing: bool,
    ) -> None:

    """
    Retain existing Caddy storage/config paths without executing the binary.

    :param tmp_path: Isolated fake Caddy and output directory.
    :type tmp_path: Path

    :param existing: Retain an external config or create a new working Caddyfile under config.
    :type existing: bool
    """

    # tmp_path/existing — вместо бинарника обычный файл; попытка запуска была бы ошибкой.

    executable = tmp_path / "caddy.exe"
    executable.write_bytes(b"not executable")
    original = tmp_path / "OldCaddyfile"
    original.write_bytes(b"PRIVATE custom config")
    options = DeployOptions(directory=tmp_path / "new", endpoint="https://gateway.example.test:8443",
                            caddy_mode="managed", caddy_executable=executable,
                            caddy_config=original if existing else None,
                            caddy_data=tmp_path / "old CA")
    deploy(options)
    config = load_deployment_config(options.directory / "config/gateway_server.local.json")
    assert config.caddy.config == (original if existing else options.directory / "config/Caddyfile")
    assert config.caddy.data_dir == options.caddy_data
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
    args = ["--directory", str(root), "--endpoint", "https://gateway.example.test:8443"]
    assert main(args + ["--dry-run"]) == 0 and not root.exists()
    assert main(args) == 0
    (root / "config/command_gateway.local.json").write_bytes(b"PRIVATE")
    assert main(args + ["--update-templates"]) == 0
    assert main(["--directory", str(root), "--endpoint", "https://u:PRIVATE@gateway.example.test"]) == 1
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
    deploy(DeployOptions(directory=root, endpoint="https://gateway.example.test:8443"))
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
    scripts = prepare_files(DeployOptions(directory=root, endpoint="https://gateway.example.test:8443"))
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
        deploy(DeployOptions(directory=tmp_path, endpoint="https://gateway.example.test"))

    assert [item.name for item in tmp_path.iterdir()] == ["START_HERE.md"]
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ от смешивания старой и новой структуры до записи
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("dry_run", [True, False])
def test_legacy_layout(
    tmp_path: Path,
    dry_run: bool,
    ) -> None:

    """
    Refuse a second operational config tree while allowing separate migration proposals.

    :param tmp_path: Synthetic installation with an intentionally unreadable legacy JSON body.
    :type tmp_path: Path

    :param dry_run: Preview must reject the same ambiguous operational layout as deployment.
    :type dry_run: bool
    """

    # tmp_path/dry_run — старый файл не разбирается и не меняется; SQLite/CA не создаются.

    legacy = tmp_path / "gateway_server.local.json"
    legacy.write_bytes(b"PRIVATE-legacy-config")
    options = DeployOptions(directory=tmp_path, endpoint="https://gateway.example.test:8443")
    with pytest.raises(DeployError, match="layout_migration_required"):
        deploy(options, dry_run=dry_run)

    assert list(tmp_path.iterdir()) == [legacy]
    deploy(options, update_templates=True)
    assert legacy.read_bytes() == b"PRIVATE-legacy-config"
    assert not (tmp_path / "config").exists()
    assert (tmp_path / "templates" / version("remote-watch") / "config/gateway_server.local.json").is_file()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Защита вложенной папки примеров от записи через ссылку
#------------------------------------------------------------------------------------------------------------------
def test_nested_config_link(
    tmp_path: Path,
    ) -> None:

    """
    Reject a linked examples directory before creating any other deployment output.

    :param tmp_path: Synthetic installation and link target, with no production data.
    :type tmp_path: Path
    """

    # tmp_path — проверяется новая вложенность config/examples, а не только корень установки.

    target, root = tmp_path / "foreign", tmp_path / "kit"
    target.mkdir()
    (root / "config").mkdir(parents=True)
    link = root / "config/examples"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("symlink privilege unavailable")
        import _winapi
        _winapi.CreateJunction(str(target), str(link))

    with pytest.raises(DeployError, match="linked_path"):
        deploy(DeployOptions(directory=root, endpoint="https://gateway.example.test:8443"))

    assert list(target.iterdir()) == []
    assert list(root.iterdir()) == [root / "config"]
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_gateway_deploy не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
