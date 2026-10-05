# Проверки JSON-конфигурации gateway и выбора способа запуска.
#
# Version 1.0.4
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-210047
#
# Тесты:
# -> document(): Синтетический файл настроек без секретов.
# -> test_json_valid(): Создание настроек обоих провайдеров без токенов.
# -> test_json_rejects(): Отклонение неверных и неоднозначных настроек.
# -> test_json_cli(): Проверка обоих способов настройки и запуска.
# -> test_json_cli_exclusive(): Выбор единственного источника настроек.
# -> test_json_cli_private_error(): Отсутствие локальных данных в сообщении об ошибке.
# -> test_json_example(): Проверка примера для четырёх приложений.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from remote_watch import DeliveryMode
from remote_watch.gateway.json_config import MAX_CONFIG_BYTES, load_gateway_config
from remote_watch.gateway.server import main


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Синтетический файл настроек без секретов
#------------------------------------------------------------------------------------------------------------------
def document() -> dict[str, Any]:

    """Build a small configuration containing no real credentials.

    :return: Mutable synthetic JSON configuration with placeholders for credentials.
    :rtype: dict[str, Any]
    """

    return {
        "schema_version": 1,
        "destinations": [{"alias": "phone", "provider": "telegram", "settings": {
            "token_env": "RW_JSON_FAKE_TG", "chat_id": 123456789,
        }}],
        "principals": [{"name": "app", "token_env": "RW_JSON_FAKE_APP", "aliases": ["phone"],
            "identity": {"service": "app", "environment": "test", "region": "test", "host": "host",
                         "instance_id": "one"}}],
    }
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Создание настроек обоих провайдеров без токенов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
@pytest.mark.parametrize("display", ["full", "compact", "text"])
def test_json_valid(
    tmp_path: Path,
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
    display: str,
) -> None:

    """Create typed destinations without reading provider or service credentials.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param provider: Provider selected by the test.
    :type provider: str

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param display: Destination display mode parsed from JSON.
    :type display: str
    """

    # tmp_path - временный каталог теста.
    # provider - сервис, выбранный для проверки.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # display — режим именно серверного provider adapter, а не настройки relay-клиента.

    monkeypatch.delenv("RW_JSON_FAKE_TG", raising=False)
    monkeypatch.delenv("RW_JSON_FAKE_APP", raising=False)
    value = document()
    destination = value["destinations"][0]
    destination["provider"] = provider
    destination["timeouts"] = {"connect_timeout": 2, "attempt_timeout": 4, "ttl": 60}
    if provider == "ntfy":
        destination["settings"] = {"topic": "synthetic-topic", "token_env": None, "tags": ["warning"]}
    destination["settings"]["display_mode"] = display
    if display == "compact":
        destination["settings"]["display_fields"] = ["identity", "level"]
    value["gateway"] = {"capacity": 10, "clock_skew_tolerance": 600}
    path = tmp_path / "config.json"
    # BOM принимается для файлов, сохранённых стандартными средствами Windows.
    path.write_text(json.dumps(value), encoding="utf-8-sig")
    config = load_gateway_config(path)
    assert config.capacity == 10
    assert config.clock_skew_tolerance == 600
    assert config.principals[0].aliases == ("phone",)
    assert config.destinations[0].provider == provider
    assert config.destinations[0].mode is DeliveryMode.DIRECT
    assert config.destinations[0].retry.max_attempts == 1
    assert config.destinations[0].retry.ttl == 60
    provider_config = config.destinations[0].channel_factory.args[0]
    assert provider_config.display_mode == display
    assert provider_config.display_fields == (("identity", "level") if display == "compact" else None)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение неверных и неоднозначных настроек
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", [
    "unknown", "version", "bool_version", "empty", "too_many", "unknown_provider", "python_factory",
    "secret", "identity", "unknown_alias", "duplicate_alias", "duplicate_principal", "timeouts",
    "retries", "nan", "infinity", "duplicate", "nested_duplicate", "array", "invalid_utf8", "oversize", "missing",
])
def test_json_rejects(
    tmp_path: Path,
    case: str,
) -> None:

    """Reject ambiguous, unsafe or oversized documents without echoing their contents.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param case: Selected rejection scenario.
    :type case: str
    """

    # tmp_path - временный каталог теста.
    # case - выбранный сценарий отказа.

    value = document()
    destination = value["destinations"][0]
    if case == "unknown":
        value["PRIVATE_VALUE"] = True
    elif case == "version":
        value["schema_version"] = 2
    elif case == "bool_version":
        value["schema_version"] = True
    elif case == "empty":
        value["principals"] = []
    elif case == "too_many":
        value["destinations"] *= 65
    elif case == "unknown_provider":
        destination["provider"] = "PRIVATE_VALUE"
    elif case == "python_factory":
        destination["factory"] = "PRIVATE_VALUE:run"
    elif case == "secret":
        destination["settings"]["token"] = "PRIVATE_VALUE"
    elif case == "identity":
        del value["principals"][0]["identity"]["host"]
    elif case == "unknown_alias":
        value["principals"][0]["aliases"] = ["PRIVATE_VALUE"]
    elif case == "duplicate_alias":
        value["destinations"] *= 2
    elif case == "duplicate_principal":
        value["principals"] *= 2
    elif case == "timeouts":
        destination["timeouts"] = {"attempt_timeout": 1}
    elif case == "retries":
        destination["timeouts"] = {"max_attempts": 2}
    elif case == "nan":
        value["gateway"] = {"body_timeout": float("nan")}
    elif case == "infinity":
        value["gateway"] = {"body_timeout": float("inf")}

    data = json.dumps(value).encode()
    if case == "duplicate":
        data = data.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    elif case == "nested_duplicate":
        data = data.replace(b'"host": "host"', b'"host": "host", "host": "PRIVATE_VALUE"')
    elif case == "array":
        data = b"[]"
    elif case == "invalid_utf8":
        data = b"\xffPRIVATE_VALUE"
    elif case == "oversize":
        data = b" " * (MAX_CONFIG_BYTES + 1)

    path = tmp_path / "config.json"
    if case != "missing":
        path.write_bytes(data)
    with pytest.raises(ValueError, match="Invalid gateway JSON configuration") as error:
        load_gateway_config(path)
    assert "PRIVATE_VALUE" not in str(error.value)
    assert error.value.__suppress_context__
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка обоих способов настройки и запуска
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("factory", [False, True])
def test_json_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:

    """Preserve Python factories and validate JSON without starting the server.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param factory: Whether to use the Python factory.
    :type factory: bool

    :param capsys: Captured command output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # tmp_path - временный каталог теста.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # factory - выбор Python-фабрики вместо JSON.
    # capsys - перехват вывода команды.

    path = tmp_path / "config.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    module = ModuleType("synthetic_gateway_settings")
    module.build_config = lambda: load_gateway_config(path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    arguments = ["synthetic_gateway_settings:build_config"] if factory else ["--config", str(path)]
    assert main([*arguments, "--check-config"]) == 0
    assert "корректны" in capsys.readouterr().out

    # Без --check-config оба пути передают одинаковую модель серверному циклу.
    calls = []

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Перехват запуска сервера без открытия порта
    #--------------------------------------------------------------------------------------------------------------
    async def serve(
        config: Any,
        host: str,
        port: int,
        context: Any,
    ) -> None:

        """Capture the configuration instead of opening a socket.

        :param config: Validated gateway configuration.
        :type config: Any

        :param host: Explicit bind address.
        :type host: str

        :param port: Listening port.
        :type port: int

        :param context: Optional TLS context.
        :type context: Any
        """

        # config - проверенные настройки сервера.
        # host - адрес прослушивания.
        # port - порт сервера.
        # context - необязательные настройки TLS.

        calls.append((config, host, port, context))
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr("remote_watch.gateway.server._serve", serve)
    assert main(arguments) == 0
    assert calls[0][0].principals[0].name == "app"
    assert calls[0][1:] == ("127.0.0.1", 8765, None)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Выбор единственного источника настроек
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("arguments", [[], ["module:factory", "--config", "config.json"]])
def test_json_cli_exclusive(arguments: list[str]) -> None:

    """Require exactly one explicit configuration source.

    :param arguments: Explicit CLI arguments.
    :type arguments: list[str]
    """

    # arguments - аргументы командной строки.

    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отсутствие локальных данных в сообщении об ошибке
#------------------------------------------------------------------------------------------------------------------
def test_json_cli_private_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:

    """Report invalid local configuration without leaking field values.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param capsys: Captured command output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # tmp_path - временный каталог теста.
    # capsys - перехват вывода команды.

    path = tmp_path / "config.json"
    path.write_text('{"token": "PRIVATE_VALUE"}', encoding="utf-8")
    assert main(["--config", str(path)]) == 1
    assert "PRIVATE_VALUE" not in capsys.readouterr().out
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка примера для четырёх приложений
#------------------------------------------------------------------------------------------------------------------
def test_json_example() -> None:

    """Validate the distributed four-application example as executable configuration."""

    config = load_gateway_config(Path(__file__).parents[2] / "docs/examples/gateway_config.example.json")
    assert len(config.principals) == len(config.destinations) == 4
    assert len({item.token_env for item in config.principals}) == 4
    for principal, destination in zip(config.principals, config.destinations):
        assert principal.aliases == (destination.destination_id,)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.unit.test_gateway_json не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
