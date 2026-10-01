# Проверки токенов из JSON: совместимость, безопасные ошибки и настоящая локальная доставка.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Тесты:
# -> document(): Конфигурация с вымышленными токенами.
# -> test_inline_check(): Проверка JSON без чтения окружения и раскрытия токенов.
# -> test_credential_errors(): Точное поле ошибки без приватных значений.
# -> test_safe_error_labels(): Отклонение произвольных полей и объяснений ошибки.
# -> test_inline_delivery(): Полный путь gateway к двум локальным провайдерам.
# -> test_duplicate_credentials(): Запрет одинаковых сервисных токенов из разных источников.
# -> test_typed_sources(): Конфликт источников в публичных Python-настройках.
# -> test_inline_example(): Проверка поставляемого примера без реальных токенов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from remote_watch import Delivery, Notification
from remote_watch.adapters.ntfy import NtfyConfig
from remote_watch.adapters.telegram import TelegramConfig
from remote_watch.gateway.config import GatewayPrincipal
from remote_watch.gateway.json_config import GatewayConfigError, load_gateway_config
from remote_watch.gateway.server import Gateway, GatewayStartupError, main
from remote_watch.relay_protocol import RelayRequest


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Конфигурация с вымышленными токенами
#------------------------------------------------------------------------------------------------------------------
def document(notification: Notification) -> dict[str, Any]:

    """Create synthetic inline credentials and exact identity grants.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :return: JSON-compatible gateway configuration.
    :rtype: dict[str, Any]
    """

    # notification - уведомление с тестовой принадлежностью.

    return {
        "schema_version": 1,
        "destinations": [
            {"alias": "tg", "provider": "telegram", "settings": {
                "token": "12345:synthetic_inline_bot", "chat_id": 123456789}},
            {"alias": "ntfy", "provider": "ntfy", "settings": {
                "token": "tk_synthetic_inline", "topic": "synthetic-topic"}},
        ],
        "principals": [{"name": "app", "token": "synthetic_service_" + "x" * 32,
                        "identity": asdict(notification.identity), "aliases": ["tg", "ntfy"], "min_interval": 0}],
        "gateway": {"destination_interval": 0},
    }
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка JSON без чтения окружения и раскрытия токенов
#------------------------------------------------------------------------------------------------------------------
def test_inline_check(
    notification: Notification,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:

    """Keep check-config offline, environment-free and safe for object representations.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param tmp_path: Temporary directory.
    :type tmp_path: Path

    :param monkeypatch: Environment patch fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: CLI output capture.
    :type capsys: pytest.CaptureFixture[str]
    """

    # notification - тестовое уведомление.
    # tmp_path - каталог временного конфига.
    # monkeypatch - контроль отсутствия обращения к окружению.
    # capsys - перехваченный вывод CLI.

    raw = document(notification)
    path = tmp_path / "gateway.local.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = dict(os.environ)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Запрет чтения переменных при проверке конфига
    #--------------------------------------------------------------------------------------------------------------
    def forbidden(
        *args: Any,
        **kwargs: Any,
    ) -> None:

        """Fail on environment reads during configuration validation.

        :param args: Unexpected positional arguments.
        :type args: Any

        :param kwargs: Unexpected keyword arguments.
        :type kwargs: Any
        """

        # args — неожиданные позиционные аргументы подставного вызова.
        # kwargs — неожиданные именованные аргументы подставного вызова.

        raise AssertionError("check-config must not read environment secrets")
    #--------------------------------------------------------------------------------------------------------------

    with monkeypatch.context() as patch:
        patch.setattr("remote_watch._credentials.os", SimpleNamespace(environ=SimpleNamespace(get=forbidden)))
        config = load_gateway_config(path)
        assert main(["--config", str(path), "--check-config"]) == 0
    assert dict(os.environ) == before

    # repr включает вложенные фабрики каналов: token скрывается и внутри partial.
    text = repr(config) + capsys.readouterr().out
    for destination in config.destinations:
        text += repr(destination.channel_factory())
    for item in raw["destinations"]:
        assert item["settings"]["token"] not in text
    assert raw["principals"][0]["token"] not in text
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Точное поле ошибки без приватных значений
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("target", ["telegram", "ntfy", "principal"])
@pytest.mark.parametrize("case", ["both", "both_null", "missing", "null", "empty", "type", "newline",
                                  "oversize", "bad_env", "null_env"])
def test_credential_errors(
    target: str,
    case: str,
    notification: Notification,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:

    """Reject malformed sources without echoing any credential or variable name.

    :param target: Configuration entry being checked.
    :type target: str

    :param case: Invalid source variant.
    :type case: str

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param tmp_path: Temporary directory.
    :type tmp_path: Path

    :param capsys: CLI output capture.
    :type capsys: pytest.CaptureFixture[str]
    """

    # target - провайдер или права приложения.
    # case - вариант ошибки выбора или значения токена.
    # notification - тестовое уведомление.
    # tmp_path - каталог временного конфига.
    # capsys - вывод CLI для проверки безопасной диагностики.

    raw = document(notification)
    index = 0 if target == "telegram" else 1
    settings = raw["principals"][0] if target == "principal" else raw["destinations"][index]["settings"]
    prefix = "principals[0]" if target == "principal" else f"destinations[{index}].settings"
    field = "token"
    if case in ("both", "both_null"):
        settings["token_env"] = "PRIVATE_ENV" if case == "both" else None
    elif case in ("missing", "bad_env", "null_env"):
        del settings["token"]
        if case != "missing":
            settings["token_env"] = "123:PRIVATE_TOKEN" if case == "bad_env" else None
        if case == "bad_env":
            field = "token_env"
    else:
        settings["token"] = {"null": None, "empty": "", "type": 123, "newline": "PRIVATE\nTOKEN",
                             "oversize": "x" * 513}[case]
    path = tmp_path / "private.local.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    # Только явный token_env=null у ntfy сохраняет прежнюю анонимную отправку.
    if target == "ntfy" and case == "null_env":
        assert load_gateway_config(path).destinations[1].channel_factory()._config.token is None
        return
    with pytest.raises(GatewayConfigError) as captured:
        load_gateway_config(path)
    assert captured.value.field == f"{prefix}.{field}"
    assert main(["--config", str(path), "--check-config"]) == 1
    output = capsys.readouterr().out + repr(captured.value)
    assert f"field={prefix}.{field}" in output
    assert "PRIVATE" not in output and "synthetic_service" not in output
    if case == "bad_env":
        assert "Ожидается имя переменной" in output
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение произвольных полей и объяснений ошибки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["PRIVATE_TOKEN", "destinations[999].settings.token",
                                  "principals[0].settings.token", "destinations[0].token"])
def test_safe_error_labels(field: str) -> None:

    """Keep externally constructed diagnostic objects within a fixed vocabulary.

    :param field: Untrusted diagnostic label.
    :type field: str
    """

    # field - недопустимый путь, который нельзя копировать в вывод CLI.

    error = GatewayConfigError("PRIVATE_CODE", field, "PRIVATE_REASON")
    assert error.code == "config_value" and error.field == "config" and error.reason is None
    assert "PRIVATE" not in repr(error)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полный путь gateway к двум локальным провайдерам
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["literal", "environment", "mixed"])
def test_inline_delivery(
    source: str,
    notification: Notification,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:

    """Authenticate and deliver over real loopback HTTP with either credential source.

    :param source: Credential source arrangement.
    :type source: str

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param tmp_path: Temporary directory.
    :type tmp_path: Path

    :param monkeypatch: Synthetic environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param caplog: Captured logs at DEBUG level.
    :type caplog: pytest.LogCaptureFixture
    """

    # source - значения, переменные либо их сочетание между разными записями.
    # notification - тестовое уведомление с фиксированным временем.
    # tmp_path - каталог конфига.
    # monkeypatch - временные переменные с вымышленными токенами.
    # caplog - журнал для проверки отсутствия секретов даже на уровне DEBUG.

    pytest.importorskip("aiohttp")
    from aiohttp import ClientSession, web

    caplog.set_level(logging.DEBUG)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Запуск локальных серверов и отправка двух запросов
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise actual header and URL credentials without external network access."""

        raw = document(notification)
        service_token = raw["principals"][0]["token"]
        provider_tokens = [item["settings"]["token"] for item in raw["destinations"]]
        seen = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Проверка запроса к подставному провайдеру
        #----------------------------------------------------------------------------------------------------------
        async def receive(request: web.Request) -> web.Response:

            """Check the exact provider credential and acknowledge one delivery.

            :param request: Local HTTP request.
            :type request: web.Request

            :return: Synthetic provider receipt.
            :rtype: web.Response
            """

            # request - запрос настоящего адаптера к локальному серверу.

            body = await request.json()
            if request.path == "/":
                assert request.headers["Authorization"] == "Bearer " + provider_tokens[1]
                assert body["topic"] == "synthetic-topic"
                seen.append("ntfy")
                return web.json_response({"id": "synthetic-ntfy", "event": "message", "topic": body["topic"]})
            assert request.path == "/bot" + provider_tokens[0] + "/sendMessage"
            assert body["chat_id"] == 123456789
            seen.append("telegram")
            return web.json_response({"ok": True, "result": {"message_id": 123}})
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_post("/{path:.*}", receive)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        gateway = None
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            for index, item in enumerate(raw["destinations"]):
                item["settings"].update(endpoint=f"http://127.0.0.1:{port}", allow_http=True)
                if source == "environment" or source == "mixed" and index == 1:
                    name = f"RW_SYNTHETIC_PROVIDER_{index}"
                    monkeypatch.setenv(name, item["settings"].pop("token"))
                    item["settings"]["token_env"] = name
            if source in ("environment", "mixed"):
                monkeypatch.setenv("RW_SYNTHETIC_SERVICE", raw["principals"][0].pop("token"))
                raw["principals"][0]["token_env"] = "RW_SYNTHETIC_SERVICE"
            path = tmp_path / "gateway.local.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_gateway_config(path)
            gateway = Gateway(config, utc_now=lambda: notification.created_at)
            await gateway.start(port=0)
            async with ClientSession() as session:
                for alias in ("tg", "ntfy"):
                    payload = RelayRequest(delivery=Delivery(notification=notification, destination_id=alias,
                        delivery_id=alias), alias=alias, remaining_ttl=30, timeout=8).to_dict()
                    endpoint = f"http://127.0.0.1:{gateway.port}/v1/notifications"
                    async with session.post(endpoint, json=payload,
                        headers={"Authorization": "Bearer " + service_token}) as response:
                        assert response.status == 200
                        assert (await response.json())["result"]["status"] == "provider_accepted"
                    # Неверный сервисный токен не должен доходить до провайдеров.
                    async with session.post(endpoint, json=payload,
                        headers={"Authorization": "Bearer " + "z" * 32}) as response:
                        assert response.status == 401
            assert seen == ["telegram", "ntfy"]
            for token in provider_tokens + [service_token]:
                assert token not in repr(config) + repr(gateway) + caplog.text
        finally:
            if gateway is not None:
                await gateway.close()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет одинаковых сервисных токенов из разных источников
#------------------------------------------------------------------------------------------------------------------
def test_duplicate_credentials(
    notification: Notification,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Reject duplicate resolved credentials before opening any provider.

    :param notification: Synthetic notification fixture.
    :type notification: Notification

    :param tmp_path: Temporary directory.
    :type tmp_path: Path

    :param monkeypatch: Synthetic environment fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # notification - тестовое уведомление.
    # tmp_path - каталог конфига.
    # monkeypatch - переменная с дубликатом вымышленного токена.

    pytest.importorskip("aiohttp")
    path = tmp_path / "gateway.local.json"
    path.write_text(json.dumps(document(notification)), encoding="utf-8")
    config = load_gateway_config(path)
    principal = config.principals[0]
    monkeypatch.setenv("RW_DUPLICATE", principal.token)
    duplicate = replace(principal, name="other", token=None, token_env="RW_DUPLICATE")
    gateway = Gateway(replace(config, principals=(principal, duplicate)))

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка отката запуска без открытия провайдеров
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Keep duplicate-credential failures safe and close partially started state."""

        try:
            with pytest.raises(GatewayStartupError) as captured:
                await gateway.start(port=0)
            assert captured.value.stage == "credentials"
            assert principal.token not in repr(captured.value)
        finally:
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Конфликт источников в публичных Python-настройках
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["telegram", "ntfy", "gateway"])
def test_typed_sources(
    kind: str,
    notification: Notification,
) -> None:

    """Validate direct model usage independently of JSON parsing.

    :param kind: Public configuration model.
    :type kind: str

    :param notification: Synthetic identity source.
    :type notification: Notification
    """

    # kind - тип настроек провайдера или приложения.
    # notification - принадлежность вымышленного приложения.

    if kind == "telegram":
        config = TelegramConfig(chat_id=1, token="1:synthetic_typed")
    elif kind == "ntfy":
        config = NtfyConfig(topic="synthetic", token="synthetic_typed")
    else:
        config = GatewayPrincipal(name="app", identity=notification.identity, aliases=("phone",), token="x" * 32)
    with pytest.raises(ValueError):
        replace(config, token_env="RW_SYNTHETIC")
    assert config.token not in repr(config)

    if kind == "gateway":
        with pytest.raises(ValueError):
            replace(config, token="x" * 31)
        assert replace(config, token="x" * 512).token == "x" * 512
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка поставляемого примера без реальных токенов
#------------------------------------------------------------------------------------------------------------------
def test_inline_example() -> None:

    """Load the shipped inline example without accessing environment credentials."""

    path = Path(__file__).resolve().parents[2] / "docs/examples/gateway_config.inline.example.json"
    config = load_gateway_config(path)
    assert len(config.destinations) == 2 and len(config.principals) == 1
    assert config.principals[0].token_env is None
    assert "REPLACE_WITH" not in repr(config)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.contract.test_gateway_credentials не предназначен для прямого запуска. "
          "Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
