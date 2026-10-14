# Проверки doctor без доступа к провайдерам и изменения рабочих сессий.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Функции:
# -> configuration(): Отдельный закрытый JSON для проверки клиентского доступа
#
# Тесты:
# -> test_offline(): Offline не читает environment credentials и не создаёт сеть
#
# -> test_invalid_config(): Ошибки конфигурации не раскрывают значения
#
# -> test_ca(): Различимые ошибки чтения и формата CA
#
# -> test_reports(): Безопасный JSON и запрет перезаписи отчёта
#
# -> test_arguments(): Ограничение времени и отсутствие echo ошибочных аргументов
#
# -> test_dns_deadline(): Поздний DNS не начинает соединение после истечения срока
#    Вложенные объявления:
#    -> delayed(): Искусственная задержка операции для проверки ограничений времени
#
# -> test_deploy_doctor(): Согласованность новых ресурсов и прав из шаблонов


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import json
import socket
import ssl
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from remote_watch.diagnostics._doctor_network import network_checks
from remote_watch.diagnostics.gateway_doctor import client_checks, load_targets, main
from remote_watch.gateway_server.deployment import DeployOptions, deploy

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Отдельный закрытый JSON для проверки клиентского доступа
#------------------------------------------------------------------------------------------------------------------
def configuration(
    path: Path,
    ) -> Path:

    """
    Write synthetic doctor settings independent of any running application.

    :param path: Temporary test folder.
    :type path: Path

    :return: Path to the private synthetic JSON.
    :rtype: Path
    """

    # path — никаких рабочих конфигов или настоящих токенов в тесте нет.
    result = path / "doctor.json"
    result.write_text(json.dumps({"schema_version": 1, "endpoint": "https://private.invalid:8443",
        "identity": {"service": "private-service", "environment": "test", "region": "ru",
                     "host": "private-host", "instance_id": "one"},
        "commands": {"token_env": "PRIVATE_DOCTOR_TOKEN", "scopes": ["control"]}}), encoding="utf-8")
    return result
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Offline не читает environment credentials и не создаёт сеть
#------------------------------------------------------------------------------------------------------------------
def test_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """
    Keep the default mode independent of credentials, DNS, journals and sessions.

    :param tmp_path: Temporary file directory.
    :type tmp_path: Path

    :param monkeypatch: Scoped replacement fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.
    # monkeypatch — временная подмена окружения или зависимостей только на время теста.


    # Сеть и разрешение секретов запрещены явно: offline не должен до них дойти.
    path = configuration(tmp_path)
    forbidden = Mock(side_effect=AssertionError("unexpected I/O"))
    monkeypatch.setattr("remote_watch.diagnostics.gateway_doctor.resolve_token", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    targets = load_targets(path)
    rows = client_checks(path, False, 1)
    assert [row["status"] for row in rows] == ["ok", "ok", "skipped"]
    assert "private" not in repr(targets).lower()
    assert "PRIVATE_DOCTOR_TOKEN" not in repr(targets)
    forbidden.assert_not_called()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["doctor.json"]
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибки конфигурации не раскрывают значения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change", ["unknown", "duplicate", "empty", "schema", "http", "permission", "shared"])
def test_invalid_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    change: str,
    ) -> None:

    """
    Reject malformed inputs without reproducing their keys or values.

    :param tmp_path: Temporary file directory.
    :type tmp_path: Path

    :param capsys: Captured CLI output.
    :type capsys: pytest.CaptureFixture

    :param change: Selected invalid configuration mutation.
    :type change: str
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.
    # capsys — перехват вывода для проверки отсутствия приватных данных.
    # change — изменение исходных данных для проверки отказа.


    # Варианты включают неизвестный секретный ключ и повторяющийся JSON ключ.
    path = configuration(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if change == "unknown":
        value["private-secret-key"] = "private-secret-value"
    elif change == "empty":
        value.pop("commands")
    elif change == "schema":
        value["schema_version"] = True
    elif change == "http":
        value["endpoint"] = "http://private.invalid"
    elif change == "permission":
        value["commands"]["scopes"] = ["control", "control"]
    elif change == "shared":
        value["notifications"] = {"token_env": "PRIVATE_DOCTOR_TOKEN", "aliases": ["phone"]}
    data = json.dumps(value)
    if change == "duplicate":
        data = data.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    path.write_text(data, encoding="utf-8")
    assert main(["--mode", "client", "--config", str(path), "--json"]) == 1
    output = capsys.readouterr().out
    assert "private" not in output.lower()
    assert not json.loads(output)["passed"]
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Различимые ошибки чтения и формата CA
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("exists,code", [(False, "ca_read"), (True, "ca_invalid")])
def test_ca(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    exists: bool,
    code: str,
    ) -> None:

    """
    Classify CA failures without reporting filesystem paths or PEM content.

    :param tmp_path: Temporary directory.
    :type tmp_path: Path

    :param capsys: Captured output.
    :type capsys: pytest.CaptureFixture

    :param exists: Whether a malformed CA file exists.
    :type exists: bool

    :param code: Expected fixed failure category.
    :type code: str
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.
    # capsys — перехват вывода для проверки отсутствия приватных данных.
    # exists — должен ли проверяемый путь существовать до выполнения операции.
    # code — фиксированный код проверяемого отказа.


    # Относительный CA ищется от JSON, а не от текущей рабочей папки.
    path = configuration(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["ca_file"] = "private-root.crt"
    path.write_text(json.dumps(value), encoding="utf-8")
    if exists:
        (tmp_path / "private-root.crt").write_text("private-content", encoding="utf-8")
    assert main(["--mode", "client", "--config", str(path), "--json"]) == 1
    output = capsys.readouterr().out
    assert code in output and "private" not in output.lower()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасный JSON и запрет перезаписи отчёта
#------------------------------------------------------------------------------------------------------------------
def test_reports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    ) -> None:

    """
    Create a shareable new report while preserving existing files.

    :param tmp_path: Temporary directory.
    :type tmp_path: Path

    :param capsys: Captured output.
    :type capsys: pytest.CaptureFixture
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.
    # capsys — перехват вывода для проверки отсутствия приватных данных.


    # Значения конфигурации не должны попасть ни в консоль, ни в файл отчёта.
    path = configuration(tmp_path)
    report = tmp_path / "report.json"
    args = ["--mode", "client", "--config", str(path), "--json", "--report", str(report)]
    assert main(args) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == json.loads(report.read_text(encoding="utf-8"))
    original = report.read_bytes()
    assert "private" not in output.lower()
    assert main(args) == 2
    assert report.read_bytes() == original
    assert "report_write" in capsys.readouterr().err
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничение времени и отсутствие echo ошибочных аргументов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "11", "private-secret"])
def test_arguments(
    capsys: pytest.CaptureFixture,
    timeout: str,
    ) -> None:

    """
    Reject unbounded CLI timeouts with a sanitized usage error.

    :param capsys: Captured output.
    :type capsys: pytest.CaptureFixture

    :param timeout: Invalid CLI timeout string.
    :type timeout: str
    """

    # capsys — перехват вывода для проверки отсутствия приватных данных.
    # timeout — допустимое время ожидания операции в секундах.


    # argparse не должен отражать произвольные значения в сообщении об ошибке.
    with pytest.raises(SystemExit) as error:
        main(["--config", "private-path", "--timeout", timeout])
    assert error.value.code == 2
    assert "private" not in capsys.readouterr().err
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Поздний DNS не начинает соединение после истечения срока
#------------------------------------------------------------------------------------------------------------------
def test_dns_deadline(
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """
    Return on DNS timeout and prevent a late resolver from sending credentials.

    :param monkeypatch: Scoped network replacements.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # monkeypatch — временная подмена окружения или зависимостей только на время теста.


    # Управляемый DNS завершается только после того, как doctor уже вернул timeout.
    release, finished = threading.Event(), threading.Event()

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Искусственная задержка операции для проверки ограничений времени
    #--------------------------------------------------------------------------------------------------------------
    def delayed(
        *args: object,
        **kwargs: object,
        ) -> list:

        """
        Wait for test permission before returning a synthetic address.

        :param args: Ignored resolver positional arguments.
        :type args: object

        :param kwargs: Ignored resolver keyword arguments.
        :type kwargs: object

        :return: One synthetic loopback address.
        :rtype: list
        """

        # args — позиционные аргументы тестируемого вызова.
        # kwargs — именованные аргументы тестируемого вызова.


        # Аргументы не журналируются; никакого настоящего DNS нет.
        release.wait(3)
        finished.set()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1))]
    #--------------------------------------------------------------------------------------------------------------

    forbidden = Mock(side_effect=AssertionError("late connection"))
    monkeypatch.setattr(socket, "getaddrinfo", delayed)
    monkeypatch.setattr(socket, "socket", forbidden)
    rows = network_checks("https://private.invalid", ssl.create_default_context(), "commands", "secret", b"{}", .1)
    assert rows[-1]["code"] == "dns_timeout"
    release.set()
    assert finished.wait(2)
    # Поток после DNS проверяет отмену до создания сокета.
    for thread in threading.enumerate():
        if thread.name == "gateway-doctor":
            thread.join(2)
    forbidden.assert_not_called()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Согласованность новых ресурсов и прав из шаблонов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("components", ["both", "notifications", "commands"])
def test_deploy_doctor(
    tmp_path: Path,
    components: str,
    ) -> None:

    """
    Ship offline launchers and client probes matching generated principals.

    :param tmp_path: Temporary directory.
    :type tmp_path: Path

    :param components: Selected deployment channels.
    :type components: str
    """

    # tmp_path — отдельный временный каталог теста, без рабочих конфигов и журналов.
    # components — набор включённых компонентов сервера.


    # Deploy остаётся offline: шаблон содержит ссылки на секреты, а не их значения.
    root = tmp_path / "server"
    deploy(DeployOptions(directory=root, endpoint="https://private.invalid:8443", components=components))
    targets = load_targets(root / "clients/gateway_doctor.client.example.json")
    assert len(targets) == (2 if components == "both" else 1)
    assert all(item.identity.service == "app1" and item.identity.host == "ru-server-1" for item in targets)
    for item in targets:
        if item.component == "commands":
            assert all(scope.startswith("command:") for scope in item.permissions)
    assert "gateway_doctor" in (root / "doctor.ps1").read_text(encoding="utf-8-sig")
    assert "@args" in (root / "doctor.ps1").read_text(encoding="utf-8-sig")
    assert '"$@"' in (root / "doctor.sh").read_text(encoding="utf-8")
    caddy = (root / "Caddyfile.example").read_text(encoding="utf-8")
    assert ("/v1/notifications/probe" in caddy) == (components != "commands")
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Запуск проверок через pytest
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
#------------------------------------------------------------------------------------------------------------------
