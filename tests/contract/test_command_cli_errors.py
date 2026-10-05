# Понятные ошибки командного CLI без раскрытия секретов и приватных путей.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-144006
#
# Тесты:
# -> test_shape_errors(): Точное поле при ошибке структуры связанного JSON.
# -> test_startup_errors(): Полезные причины отказа без исходного текста исключения.
# -> test_unexpected_config_error(): Категория неожиданного сбоя на этапе загрузки.
# -> test_cli_version_and_tls_pair(): Версия и неполная пара аргументов TLS без запуска.
# -> test_real_port_conflict(): Занятый loopback-порт и штатное закрытие ресурсов.
# -> test_json_location(): Причина и координаты синтаксической ошибки без фрагмента JSON.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import errno
import socket
import ssl
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_command_gateway_config import FakeTime, settings, write_config

from remote_watch.commands.transport import CommandError
from remote_watch.gateway import commands as cli
from remote_watch.gateway.command_config import CommandConfigError, load_command_gateway
from remote_watch.gateway.command_service import CommandGateway


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Точное поле при ошибке структуры связанного JSON
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case,code,field", [
    ("root_missing", "config_missing", "config.state_dir"),
    ("root_unknown", "config_unknown", "config"),
    ("targets_list", "config_object", "targets"),
    ("identity_missing", "config_missing", "targets[0].host"),
    ("identity_unknown", "config_unknown", "targets[0]"),
    ("principal_missing", "config_missing", "principals[0].scopes"),
    ("scopes_list", "config_list", "principals[0].scopes"),
    ("source_missing", "config_missing", "sources[0].provider"),
    ("settings_list", "config_object", "sources[0].settings"),
    ("access_missing", "config_missing", "sources[0].access[0].actor_id"),
    ("access_list", "config_list", "sources[0].access"),
    ("hub_unknown", "config_unknown", "hub"),
])
def test_shape_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    code: str,
    field: str,
) -> None:

    """Report only declared schema names, never target aliases or unknown JSON keys.

    :param tmp_path: Isolated synthetic configuration directory.
    :type tmp_path: Path

    :param capsys: Captured console output.
    :type capsys: pytest.CaptureFixture[str]

    :param case: Selected malformed configuration scenario.
    :type case: str

    :param code: Expected safe diagnostic category.
    :type code: str

    :param field: Expected schema path without user-defined names.
    :type field: str
    """

    # tmp_path — отдельный каталог без рабочих журналов и настоящих токенов.
    # capsys — проверка сообщения, которое увидит оператор.
    # case — способ испортить заведомо подставной конфиг.
    # code — фиксированная причина отказа.
    # field — допустимый путь схемы; alias приложения сюда не попадает.

    value = settings()
    source = value["sources"][0]
    if case == "root_missing":
        del value["state_dir"]
    elif case == "root_unknown":
        value["private-secret-key"] = "private-secret-value"
    elif case == "targets_list":
        value["targets"] = []
    elif case == "identity_missing":
        del value["targets"]["one"]["host"]
    elif case == "identity_unknown":
        value["targets"]["one"]["private-secret-key"] = "private-secret-value"
    elif case == "principal_missing":
        del value["principals"][0]["scopes"]
    elif case == "scopes_list":
        value["principals"][0]["scopes"] = "command:status"
    elif case == "source_missing":
        del source["provider"]
    elif case == "settings_list":
        source["settings"] = []
    elif case == "access_missing":
        del source["access"][0]["actor_id"]
    elif case == "access_list":
        source["access"] = {}
    else:
        value["hub"]["private-secret-key"] = "private-secret-value"

    path = write_config(tmp_path / "private-file.json", value)
    with pytest.raises(CommandConfigError) as caught:
        load_command_gateway(path)
    assert (caught.value.code, caught.value.field) == (code, field)
    assert cli.main(["--config", str(path), "--check-config"]) == 1
    output = capsys.readouterr().out
    assert f"code={code} field={field}" in output
    assert "private-" not in output and "a" * 40 not in output
    assert not (tmp_path / "state").exists()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полезные причины отказа без исходного текста исключения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("error,code", [
    (OSError(errno.EADDRINUSE, "private-address"), "address_in_use"),
    (OSError(errno.EADDRNOTAVAIL, "private-address"), "address_unavailable"),
    (PermissionError("private-directory"), "permission_denied"),
    (FileNotFoundError("private-certificate"), "file_missing"),
    (ssl.SSLError("private-certificate"), "tls_invalid"),
    (ImportError("private-import"), "dependency_missing"),
    (CommandError("conflict"), "command_conflict"),
    (CommandError("capacity"), "command_capacity"),
    (CommandError("unavailable"), "command_unavailable"),
    (CommandError("busy"), "command_busy"),
    (CommandError("denied"), "command_denied"),
])
def test_startup_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    code: str,
) -> None:

    """Exercise the CLI boundary with typed failures without starting provider traffic.

    :param tmp_path: Isolated synthetic configuration directory.
    :type tmp_path: Path

    :param monkeypatch: Scoped replacement of gateway construction.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured safe console output.
    :type capsys: pytest.CaptureFixture[str]

    :param error: Injected private exception.
    :type error: Exception

    :param code: Expected safe error code.
    :type code: str
    """

    # tmp_path — только подставные файлы.
    # monkeypatch — останавливает выполнение до открытия provider или журнала.
    # capsys — перехват текста CLI.
    # error — текст исключения намеренно содержит приватный маркер.
    # code — ожидаемая классификация по типу/числовому коду, не по тексту.

    path = write_config(tmp_path / "config.json", settings())
    monkeypatch.setattr(cli, "CommandGateway", Mock(side_effect=error))
    assert cli.main(["--config", str(path), "--allow-loopback-http"]) == 1
    output = capsys.readouterr().out
    assert f"code={code} field=startup" in output
    assert "Версия remote-watch:" in output
    assert "private-" not in output and "a" * 40 not in output
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Категория неожиданного сбоя на этапе загрузки
#------------------------------------------------------------------------------------------------------------------
def test_unexpected_config_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:

    """Keep an unexpected loader bug distinguishable without printing its secret payload.

    :param monkeypatch: Scoped replacement of the loader.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured CLI output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # monkeypatch — воспроизводит именно ветку operation_failed field=config.
    # capsys — проверяем полезный тип сбоя и отсутствие исходного текста.

    monkeypatch.setattr(cli, "load_command_gateway", Mock(side_effect=KeyError("private-secret")))
    assert cli.main(["--config", "private-path"]) == 1
    output = capsys.readouterr().out
    assert "code=operation_failed field=config" in output
    assert "KeyError" in output and "--check-config" in output
    assert "private-" not in output
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Версия и неполная пара аргументов TLS без запуска
#------------------------------------------------------------------------------------------------------------------
def test_cli_version_and_tls_pair(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:

    """Allow version inspection without a config and reject an incomplete TLS pair early.

    :param tmp_path: Synthetic configuration directory.
    :type tmp_path: Path

    :param capsys: Captured console output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # tmp_path — отдельный каталог теста.
    # capsys — подтверждение отсутствия путей и понятного сообщения.

    with pytest.raises(SystemExit) as caught:
        cli.main(["--version"])
    assert caught.value.code == 0
    assert "remote-watch " in capsys.readouterr().out

    path = write_config(tmp_path / "config.json", settings())
    assert cli.main(["--config", str(path), "--cert", "private-cert.pem"]) == 1
    output = capsys.readouterr().out
    assert "code=tls_pair field=tls" in output
    assert "private-cert" not in output
    assert not (tmp_path / "state").exists()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Занятый loopback-порт и штатное закрытие ресурсов
#------------------------------------------------------------------------------------------------------------------
def test_real_port_conflict(tmp_path: Path) -> None:

    """Keep a real local bind failure recognizable after gateway startup rollback.

    :param tmp_path: Isolated directory for synthetic journals.
    :type tmp_path: Path
    """

    # tmp_path — рабочие данные только этого теста; внешний provider не открывается.
    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Занятый порт до открытия источников
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Occupy a loopback port and verify both the error classification and cleanup."""

        config = load_command_gateway(write_config(tmp_path / "config.json", settings()))
        gateway = CommandGateway(config, time_source=FakeTime())
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            with pytest.raises(OSError) as caught:
                await gateway.start(host="127.0.0.1", port=blocker.getsockname()[1], allow_loopback_http=True)
            assert cli._failure_details(caught.value, "startup")[0] == "address_in_use"
        assert gateway.stats()["closed"]
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Причина и координаты синтаксической ошибки без фрагмента JSON
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("content,hint,located", [
    ('{"token":"private-token",\n}', "Лишняя запятая", True),
    ('{"items":["private-token",\n]}', "Лишняя запятая", True),
    (r'{"path":"C:\private-folder"}', "escape", True),
    ('{"private-key":1,"private-key":2}', "повторяется ключ", False),
    ('{"private-key":NaN}', "NaN и Infinity", False),
    ('{"private-key":1}{}', "лишние данные", True),
    ('["private-token"]', "верхнем уровне", False),
    ('{"private-key":1}\n//private-comment', "лишние данные", True),
])
def test_json_location(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str,
    hint: str,
    located: bool,
) -> None:

    """Expose fixed parser explanations and coordinates, never the source line or unknown key.

    :param tmp_path: Isolated directory for synthetic invalid JSON.
    :type tmp_path: Path

    :param capsys: Captured safe CLI output.
    :type capsys: pytest.CaptureFixture[str]

    :param content: Invalid JSON containing private marker values.
    :type content: str

    :param hint: Expected fixed explanation fragment.
    :type hint: str

    :param located: Whether the parser supplies exact line and column numbers.
    :type located: bool
    """

    # tmp_path — отдельный каталог теста.
    # capsys — проверяем фактическое сообщение CLI.
    # content — подставной JSON; маркеры не должны попасть в ответ.
    # hint — понятная причина, выбранная из фиксированной таблицы.
    # located — номера строки/столбца выводятся только когда они известны точно.

    path = tmp_path / "private-file.json"
    path.write_text(content, encoding="utf-8")
    assert cli.main(["--config", str(path), "--check-config"]) == 1
    output = capsys.readouterr().out
    assert "code=config_json field=config" in output
    assert hint in output and "private-" not in output
    assert ("Строка " in output and "столбец " in output) == located
    if "\n}" in content or "\n]}" in content:
        assert "Строка 2, столбец 1" in output
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль тестов не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
