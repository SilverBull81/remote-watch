# Проверки JSON-настроек и управления двумя приложениями через один hub.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-102445
#
# Классы:
# -> FakeTime: Подставное время без внешней сети.
#    Интерфейс:
#    -> sample(): Подставной интервал времени на локальных монотонных часах.
#
# Тесты:
# -> settings(): Подставные настройки двух приложений и общего чата.
# -> write_config(): Запись только синтетических настроек теста.
# -> test_command_config(): Строгие настройки без сети и раскрытия значений.
# -> test_gateway_two_applications(): Команды двум экземплярам в одном чате через настоящий HTTP.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from test_command_dispatcher import until
from test_command_sources import FakeProvider

from remote_watch import CommandRegistry, RemoteWatcher, WatcherConfig
from remote_watch.commands.source_protocol import SourceEvent
from remote_watch.commands.time import TimeSample
from remote_watch.diagnostics.command_smoke import SmokeApplication
from remote_watch.gateway.command_config import (
    CommandConfigError,
    check_command_gateway,
    load_command_client,
    load_command_gateway,
)
from remote_watch.gateway.command_service import CommandGateway
from remote_watch.gateway.commands import main


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подставные настройки двух приложений и общего чата
#------------------------------------------------------------------------------------------------------------------
def settings() -> dict[str, Any]:

    """Create explicit synthetic credentials, two targets and a shared chat policy.

    :return: Validated configuration, synthetic provider object or aggregate service counters.
    :rtype: dict[str, Any]
    """

    identity = {"service": "smoke", "environment": "test", "region": "ru", "host": "vm", "instance_id": "one"}
    scopes = ["command:" + name for name in ("status", "check_load", "resume_load", "suspend_load")]
    return {"schema_version": 1, "state_dir": "state", "targets": {"one": identity,
        "two": {**identity, "instance_id": "two"}}, "principals": [
            {"name": "one", "token": "a" * 40, "targets": ["one"], "scopes": scopes},
            {"name": "two", "token": "b" * 40, "targets": ["two"], "scopes": scopes}],
        "sources": [{"source_id": "telegram", "provider": "telegram",
            "settings": {"token": "12345:" + "x" * 35},
            "access": [{"actor_id": "42", "conversation_id": "-123",
                        "targets": ["one", "two"], "scopes": scopes}]}],
        "hub": {"poll_timeout": 0.1}}
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запись только синтетических настроек теста
#------------------------------------------------------------------------------------------------------------------
def write_config(
    path: Path,
    value: dict[str, Any],
) -> Path:

    """Write a synthetic local JSON fixture, never a real credential file.

    :param path: Explicit local data path or fixed HTTP operation path.
    :type path: Path

    :param value: Untrusted configuration or provider value under validation.
    :type value: dict[str, Any]

    :return: Resolved local path relative to its configuration file.
    :rtype: Path
    """

    # path — явный путь локальных данных либо фиксированный путь HTTP-операции.
    # value — проверяемое значение настроек либо ответа провайдера.

    path.write_text(json.dumps(value), encoding="utf-8")
    return path
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Строгие настройки без сети и раскрытия значений
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["literal", "env_check", "unknown", "duplicate", "missing", "old_schema",
                                 "bad_actor", "same_bot", "private_ntfy"])
def test_command_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    kind: str,
) -> None:

    """Validate strict command-only schemas and fixed private diagnostics without provider access.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param monkeypatch: Scoped pytest replacement of process-local settings.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured test output for private diagnostic assertions.
    :type capsys: pytest.CaptureFixture

    :param kind: Selected synthetic failure or provider scenario.
    :type kind: str
    """

    # tmp_path — отдельный временный каталог теста.
    # monkeypatch — временная подмена локальных настроек средствами pytest.
    # capsys — перехват вывода для проверки безопасной диагностики.
    # kind — выбранный подставной сценарий провайдера или отказа.

    value = settings()

    if kind == "env_check":
        value["principals"][0].pop("token")
        value["principals"][0]["token_env"] = "REMOTE_WATCH_MISSING_TEST_SECRET"
        monkeypatch.delenv("REMOTE_WATCH_MISSING_TEST_SECRET", raising=False)
    elif kind == "unknown":
        value["hidden-secret-value"] = "must-not-appear"
    elif kind == "old_schema":
        value["schema_version"] = True
    elif kind == "bad_actor":
        value["sources"][0]["access"][0]["actor_id"] = "display-name"
    elif kind == "same_bot":
        value["sources"].append({**value["sources"][0], "source_id": "second"})
    elif kind == "private_ntfy":
        source = value["sources"][0]
        source.update(provider="ntfy", settings={
            "topic": "commands", "reply_topic": "replies", "actor_id": "owner",
                      "private_topic_confirmed": True, "token": "tk_" + "x" * 29})
        source["access"][0].update(actor_id="owner", conversation_id="commands")
    path = write_config(tmp_path / "commands.local.json", value)

    if kind == "duplicate":
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    if kind == "missing":
        path = tmp_path / "missing.json"
    success = kind in ("literal", "env_check", "private_ntfy")
    assert main(["--config", str(path), "--check-config"]) == (0 if success else 1)
    output = capsys.readouterr().out
    assert "must-not-appear" not in output and "a" * 40 not in output and "display-name" not in output
    assert not (tmp_path / "state").exists()

    if kind == "env_check":
        with pytest.raises(CommandConfigError, match="principals"):
            load_command_gateway(path)
    elif success:
        config = load_command_gateway(path)
        assert config.state_dir == tmp_path / "state"
        check_command_gateway(path)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Подставное время без внешней сети
#------------------------------------------------------------------------------------------------------------------
class FakeTime:
    """Provide an explicit finite UTC interval without using VM wall time or the internet."""


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подставной интервал времени на локальных монотонных часах
    #--------------------------------------------------------------------------------------------------------------
    async def sample(self) -> TimeSample:

        """Anchor the test hub to synthetic provider time on its own monotonic clock.

        :return: Explicit UTC interval with its local monotonic observation.
        :rtype: TimeSample
        """

        return TimeSample(lower_utc=1000, upper_utc=1000.1, observed_at=monotonic())
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Команды двум экземплярам в одном чате через настоящий HTTP
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("client_modes", [False, True])
def test_gateway_two_applications(
    tmp_path: Path,
    client_modes: bool,
) -> None:

    """Route shared-chat commands through HTTP to two real dispatchers and separate smoke states.

    :param tmp_path: Isolated temporary test directory.
    :type tmp_path: Path

    :param client_modes: Whether two clients independently override the source's full display.
    :type client_modes: bool
    """

    # tmp_path — отдельный временный каталог теста.

    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Изолированный асинхронный сценарий проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Execute status, suspend and resume without crossing target or journal ownership."""

        raw = settings()
        # Проверяем доставку, а не скорость диска CI: длинный poll будится новой командой.
        raw["hub"]["poll_timeout"] = 5.0
        config = load_command_gateway(write_config(tmp_path / "gateway.local.json", raw))
        gateway = CommandGateway(config, time_source=FakeTime())
        provider = FakeProvider()
        gateway.sources[0].provider = provider
        watchers = []
        applications = []

        try:
            await gateway.start(port=0, allow_loopback_http=True)
            for index, alias in enumerate(("one", "two")):
                application = SmokeApplication()
                registry = CommandRegistry.from_callbacks({"status": application.status,
                    "resume_load": application.resume_load, "suspend_load": application.suspend_load})
                client_config = {"schema_version": 1, "identity": raw["targets"][alias],
                    "endpoint": f"http://127.0.0.1:{gateway.server.port}", "allow_loopback_http": True,
                    "state_file": alias + ".sqlite", "owner_id": alias, "token": raw["principals"][index]["token"]}
                if client_modes:
                    client_config["command_display_mode"] = "text" if index == 0 else "compact"
                client = load_command_client(
                    write_config(tmp_path / (alias + ".local.json"), client_config), registry)
                watcher = RemoteWatcher(WatcherConfig(identity=client.registration.identity, commands=registry),
                                        command_client=client)
                await watcher.astart()
                watchers.append(watcher)
                applications.append(application)
            # Считаем ответы с подтверждённым hub ACK. Повтор отправки после busy
            # допустим и не означает, что следующая команда уже исполнилась.
            for number, name in enumerate(("status", "suspend_load", "status", "resume_load"), 1):
                provider.events.append(SourceEvent(event_id=str(number), message_date=1000, actor_id="42",
                    conversation_id="-123", text=f"/rw one {name}"))
                try:
                    await until(lambda: gateway.sources[0].stats.replies >= number, timeout=20)
                except AssertionError:
                    raise AssertionError((gateway.stats(), [watcher.command_stats for watcher in watchers],
                                          [application.report() for application in applications])) from None
            assert applications[0].report()["sequence_verified"] is True
            assert applications[1].report()["callback_count"] == 0
            provider.events.append(SourceEvent(event_id="5", message_date=1000, actor_id="42",
                conversation_id="-123", text="/rw two suspend_load"))
            await until(lambda: gateway.sources[0].stats.replies >= 5, timeout=20)
            assert applications[1].paused.is_set() and not applications[0].paused.is_set()
            if client_modes:
                assert all("id=" not in text and "session=" not in text for _, text in provider.replies)
                assert provider.replies[-1][1].startswith('["smoke","test","ru","vm","two"]')
                assert not provider.replies[0][1].startswith('["smoke"')
            else:
                assert all("id=" in text and "session=" in text for _, text in provider.replies)
            # Второй gateway обязан отказаться до открытия hub с новым поколением.
            rival = CommandGateway(config, time_source=FakeTime())
            with pytest.raises(Exception):
                await rival.start(port=0, allow_loopback_http=True)
            assert not gateway.stats()["closed"]
        finally:
            for watcher in watchers:
                await watcher.astop()
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_gateway_config не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
