# Проверки именованных наборов прав без изменения границ авторизации.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261026-124820
#
# Функции:
# -> named_settings(): Синтетическая конфигурация с общим набором прав.
#
# Тесты:
# -> test_scope_equivalence(): Ссылки и списки дают одинаковые политики.
# -> test_scope_rejections(): Ошибочные определения и ссылки не дают разрешений.
# -> test_scope_bounds(): Конечные пределы и независимость снимка.
# -> test_scope_authorization(): Principal и отправитель сохраняют отдельные ограничения.
#    Вложенные объявления:
#    -> scenario(): Проверка регистрации и admission через настоящий hub.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_command_gateway_config import settings, write_config

from remote_watch.commands.hub import CommandHub
from remote_watch.commands.protocol import CommandCapability, CommandRef, CommandRegistration, CommandRequest
from remote_watch.commands.sqlite_store import SQLiteCommandStore
from remote_watch.commands.storage import StoreRole
from remote_watch.commands.time import TimeSample, TrustedClock
from remote_watch.commands.transport import CommandError
from remote_watch.gateway.command_config import CommandConfigError, check_command_gateway, load_command_gateway
from remote_watch.gateway.commands import main

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Синтетическая конфигурация с общим набором прав
#------------------------------------------------------------------------------------------------------------------
def named_settings() -> dict:

    """
    Replace repeated literal scopes with explicit references to one configuration-local definition.

    :return: Synthetic two-application config using only named references.
    :rtype: dict
    """

    value = settings()
    value["scope_sets"] = {"control": list(value["principals"][0]["scopes"])}
    for entry in [*value["principals"], *value["sources"][0]["access"]]:
        entry.pop("scopes")
        entry["scope_set"] = "control"
    return value
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ссылки и списки дают одинаковые политики
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("style", ["named", "mixed", "unused", "duplicates"])
def test_scope_equivalence(
    tmp_path: Path,
    style: str,
    ) -> None:

    """
    Preserve exact identities, scopes, provider settings and source ACL after reference expansion.

    :param tmp_path: Isolated synthetic JSON directory.
    :type tmp_path: Path

    :param style: Named, mixed inline/reference or unused valid definition variant.
    :type style: str
    """

    # tmp_path/style — старый файл остаётся исходной точкой сравнения, без сети и journal open.

    expected = load_command_gateway(write_config(tmp_path / "old.json", settings()))
    value = named_settings()
    if style == "mixed":
        entry = value["principals"][0]
        entry.pop("scope_set")
        entry["scopes"] = value["scope_sets"]["control"]
    if style == "unused":
        value["scope_sets"]["observe"] = ["command:status"]
    if style == "duplicates":
        value["scope_sets"]["control"] *= 2
    actual = load_command_gateway(write_config(tmp_path / "new.json", value))
    assert actual.hub.principals == expected.hub.principals
    assert actual.targets == expected.targets and actual.providers == expected.providers
    assert [(s.source_id, s.access) for s in actual.hub.sources] == [
        (s.source_id, s.access) for s in expected.hub.sources]
    check_command_gateway(tmp_path / "new.json")
    assert not (tmp_path / "state").exists()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибочные определения и ссылки не дают разрешений
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["unknown_principal", "unknown_actor", "both_principal", "both_actor",
    "both_null_principal", "both_null_actor", "missing_definitions", "invalid_member", "bad_reference_name",
    "missing_principal", "missing_actor", "null_reference", "list_reference", "sets_list", "sets_null",
    "too_many_sets", "bad_name", "empty_name", "empty_list", "not_list", "nested_reference",
    "bad_scope", "nested_scope", "too_many_scopes", "too_many_entries", "unused_bad", "duplicate_json"])
def test_scope_rejections(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    case: str,
    ) -> None:

    """
    Reject ambiguity, unknown references, malformed unused sets and excessive definitions without exposing values.

    :param tmp_path: Synthetic configuration and journal-free test directory.
    :type tmp_path: Path

    :param capsys: Capture only sanitized CLI diagnostics.
    :type capsys: pytest.CaptureFixture

    :param case: Invalid definition or reference variant.
    :type case: str
    """

    # tmp_path/capsys/case — имена наборов и значения намеренно содержат маркер секрета.

    value = named_settings()
    principal = value["principals"][0]
    actor = value["sources"][0]["access"][0]
    secret = "private-config-marker"
    if case.startswith("unknown_"):
        (principal if case.endswith("principal") else actor)["scope_set"] = secret
    elif case.startswith("both_"):
        (principal if case.endswith("principal") else actor)["scopes"] = (
            None if "null" in case else ["command:status"])
    elif case == "missing_definitions":
        value.pop("scope_sets")
    elif case == "invalid_member":
        value["scope_sets"]["control"] = [True]
    elif case == "bad_reference_name":
        principal["scope_set"] = secret + "/token"
    elif case.startswith("missing_"):
        (principal if case.endswith("principal") else actor).pop("scope_set")
    elif case in {"null_reference", "list_reference"}:
        principal["scope_set"] = None if case == "null_reference" else [secret]
    elif case in {"sets_list", "sets_null"}:
        value["scope_sets"] = [] if case == "sets_list" else None
    elif case == "too_many_sets":
        value["scope_sets"].update({f"set_{i}": ["command:status"] for i in range(64)})
    elif case in {"bad_name", "empty_name"}:
        value["scope_sets"][secret + "/token" if case == "bad_name" else ""] = ["command:status"]
    elif case == "empty_list":
        value["scope_sets"]["control"] = []
    elif case == "not_list":
        value["scope_sets"]["control"] = secret
    elif case == "nested_reference":
        value["scope_sets"]["control"] = {"scope_set": secret}
    elif case == "bad_scope":
        value["scope_sets"]["control"] = [""]
    elif case == "nested_scope":
        value["scope_sets"]["control"] = [{"private": secret}]
    elif case == "too_many_scopes":
        value["scope_sets"]["control"] = [f"command:op{i}" for i in range(65)]
    elif case == "too_many_entries":
        value["scope_sets"]["control"] = ["command:status"] * 129
    elif case == "unused_bad":
        value["scope_sets"][secret] = []

    path = write_config(tmp_path / "invalid.json", value)
    if case == "duplicate_json":
        text = path.read_text(encoding="utf-8")
        text = text.replace('"scope_sets": {', '"scope_sets": {"control": ["private-config-marker"],', 1)
        path.write_text(text, encoding="utf-8")
    with pytest.raises(CommandConfigError) as captured:
        check_command_gateway(path)
    assert secret not in str(captured.value) and secret not in captured.value.field
    assert main(["--config", str(path), "--check-config"]) == 1
    output = capsys.readouterr().out
    assert secret not in output and "a" * 40 not in output and not (tmp_path / "state").exists()
    if case.startswith("unknown_"):
        assert captured.value.code == "unknown_scope_set" and captured.value.field.endswith(".scope_set")
    if case.startswith("both_"):
        assert captured.value.code == "scope_source"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Конечные пределы и независимость снимка
#------------------------------------------------------------------------------------------------------------------
def test_scope_bounds(
    tmp_path: Path,
    ) -> None:

    """
    Accept maximum finite definitions while keeping loaded immutable policies independent of later JSON edits.

    :param tmp_path: Synthetic JSON directory.
    :type tmp_path: Path
    """

    # tmp_path — пустой scope_sets допустим в старом формате; пустой набор прав запрещён.

    legacy = settings()
    legacy["scope_sets"] = {}
    check_command_gateway(write_config(tmp_path / "empty.json", legacy))
    value = named_settings()
    value["scope_sets"].update({f"set_{i}": [f"custom:{j}" for j in range(64)] for i in range(63)})
    path = write_config(tmp_path / "maximum.json", value)
    config = load_command_gateway(path)
    scopes = config.hub.principals[0].scopes
    value["scope_sets"]["control"].clear()
    write_config(path, value)
    assert config.hub.principals[0].scopes == scopes
    assert config.hub.sources[0].access[0].scopes == scopes and isinstance(scopes, frozenset)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Principal и отправитель сохраняют отдельные ограничения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("limited", ["principal", "actor"])
def test_scope_authorization(
    tmp_path: Path,
    limited: str,
    ) -> None:

    """
    Enforce registration and actor admission separately even when both use named sets.

    :param tmp_path: Isolated real SQLite and synthetic configuration directory.
    :type tmp_path: Path

    :param limited: Independent authorization boundary restricted to status only.
    :type limited: str
    """

    # tmp_path/limited — настоящий hub и SQLite; провайдеры и callbacks не запускаются.

    value = named_settings()
    value["scope_sets"]["observe"] = ["command:status"]
    entry = value["principals"][0] if limited == "principal" else value["sources"][0]["access"][0]
    entry["scope_set"] = "observe"
    config = load_command_gateway(write_config(tmp_path / "gateway.json", value))

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка регистрации и admission через настоящий hub
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Reject excessive registration or actor permission without granting a broader command by default.
        """

        clock = TrustedClock(clock=lambda: 100.0)
        clock.install(TimeSample(lower_utc=1000, upper_utc=1000.1, observed_at=100.0))
        store = SQLiteCommandStore(tmp_path / "hub.sqlite", owner_id="scope-test", generation="a" * 32,
                                   role=StoreRole.HUB, clock=lambda: 100.0)
        hub = CommandHub(config.hub, store, epoch="a" * 32, trusted_clock=clock, clock=lambda: 100.0)
        registration = CommandRegistration(identity=config.targets.aliases["one"], session_id="b" * 32,
            capabilities=tuple(CommandCapability(name=name, required_scope="command:" + name)
                               for name in ("status", "resume_load")))
        await hub.start()
        try:
            if limited == "principal":
                with pytest.raises(CommandError, match="denied"):
                    await hub.register(config.hub.principals[0].token, registration)
            else:
                session = await hub.register(config.hub.principals[0].token, registration)
                for index, name in enumerate(("resume_load", "status"), 1):
                    request = CommandRequest(ref=CommandRef(identity=registration.identity,
                        session_id=session.session_id, hub_epoch=session.hub_epoch, command_id=f"{index:032x}"),
                        source_id="telegram", source_event_id=str(index), actor_id="42", conversation_id="-123",
                        name=name)
                    cursor = await hub.source_cursor(config.hub.sources[0].token)
                    result = await hub.submit(config.hub.sources[0].token, request, position=cursor + 1,
                                              expected_cursor=cursor, message_date=1000)
                    assert (result.command is not None) is (name == "status")
        finally:
            await hub.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Запускайте тесты через pytest.")
#------------------------------------------------------------------------------------------------------------------
