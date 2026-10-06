# Режимы ответов команд, английские служебные тексты и совместимость хешей.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-102445
#
# Тесты:
# -> test_legacy_digests(): Неизменность хешей старых сообщений и сохранённых квитанций.
# -> test_command_display_wire_rejects(): Строгость новой схемы представления.
# -> test_command_result_display(): Результат без изменения протокола, исхода и текста приложения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_command_protocol import request

from remote_watch import Identity
from remote_watch.commands.protocol import (
    CommandCapability,
    CommandGrant,
    CommandOutcome,
    CommandReason,
    CommandRegistration,
    CommandResult,
    callback_result,
    decode_command,
    encode_command,
    message_digest,
)
from remote_watch.commands.source_protocol import source_result_text


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Неизменность хешей старых сообщений и сохранённых квитанций
#------------------------------------------------------------------------------------------------------------------
def test_legacy_digests(identity: Identity) -> None:

    """Compare canonical hashes captured from the dev8 protocol implementation.

    :param identity: Fixed synthetic identity used for the legacy fingerprints.
    :type identity: Identity
    """

    # Константы получены из HEAD dev8 до изменения codec, а не новым сериализатором.
    event = request(identity)
    registration = CommandRegistration(identity=identity, session_id=event.ref.session_id,
        capabilities=(CommandCapability(name=event.name, required_scope="load:write"),))
    grant = CommandGrant(request=event, claim_id="4" * 32, remaining_ttl=30, execution_timeout=10)
    result = callback_result(event.ref, "4" * 32, "ok")
    expected = (
        "854f780b47a96380be44debb856b1a5426468ab6a497d149c8d60b6632447c41",
        "4d37e9761405accd251a301d31db6bc5331f4ad81322edc65dadba9111eb1433",
        "fdd492e4587bbeb9a861cd2febc34aeb7802bcdd7e2d3b3d5d99fba86fa1a8a2",
        "220cbf0d781840ea61c2bb65c68894797c171344013d4bdc9174d9c1c231a844",
    )
    for model, digest in zip((registration, event, grant, result), expected):
        assert message_digest(model) == digest
        assert message_digest(decode_command(encode_command(model))) == digest
    assert message_digest(replace(event, command_display_mode="text")) != expected[1]
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Строгость новой схемы представления
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["null", "unknown", "boolean", "missing", "extra", "downgrade", "duplicate"])
def test_command_display_wire_rejects(
    identity: Identity,
    case: str,
) -> None:

    """Reject ambiguous presentation extensions rather than silently dropping the selected mode.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param case: Deliberately malformed command envelope.
    :type case: str
    """

    # Новая схема не принимает null и не может быть переименована в старую.
    body = json.loads(encode_command(replace(request(identity), command_display_mode="text")))
    if case in {"null", "unknown", "boolean"}:
        body["payload"]["command_display_mode"] = {"null": None, "unknown": "PRIVATE", "boolean": True}[case]
    elif case == "missing":
        del body["payload"]["command_display_mode"]
    elif case == "extra":
        body["payload"]["display_mode"] = "text"
    elif case == "downgrade":
        body["schema_version"] = 1
    raw = json.dumps(body).encode()
    if case == "duplicate":
        raw = raw.replace(b'"command_display_mode": "text"',
                          b'"command_display_mode":"text","command_display_mode":"full"')
    with pytest.raises(ValueError, match="^invalid command message$"):
        decode_command(raw)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Результат без изменения протокола, исхода и текста приложения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["full", "compact", "text"])
@pytest.mark.parametrize("outcome", ["body", "empty", "unknown", "rejected", "expired", "long"])
def test_command_result_display(
    identity: Identity,
    mode: str,
    outcome: str,
) -> None:

    """Retain explicit failures, source distinction and Unicode within bounded provider messages.

    :param identity: Explicit synthetic identity.
    :type identity: Identity

    :param mode: Selected client display mode.
    :type mode: str

    :param outcome: Successful body, empty callback or fixed failure classification.
    :type outcome: str
    """

    # Даже режим text не превращает UNKNOWN/отказ в пустой ответ или успех.
    event = replace(request(identity), command_display_mode=mode)
    if outcome in {"body", "empty", "long"}:
        body = {"body": "Приложение ответило", "empty": None, "long": "я😀" * 680}[outcome]
        result = callback_result(event.ref, "4" * 32, body)
    else:
        reason = {"unknown": CommandReason.TIMEOUT, "rejected": CommandReason.DENIED,
                  "expired": CommandReason.EXPIRED}[outcome]
        result = CommandResult(ref=event.ref, claim_id="4" * 32, outcome=CommandOutcome(outcome), reason=reason)
    digest = message_digest(result)
    text = source_result_text(event, result, mode="text" if mode != "text" else "full")
    assert text and len(text.encode("utf-8")) <= 3800
    assert message_digest(result) == digest
    if mode == "full":
        assert "session=" in text and "command=" in text and event.ref.command_id in text
    else:
        assert event.ref.command_id not in text and "session=" not in text
    if mode == "compact":
        assert text.startswith('["quotes","test","test-region","test-host","one"]')
        for field in ("service", "environment", "region", "host", "instance_id"):
            changed = replace(event, ref=replace(event.ref, identity=replace(identity, **{field: "other"})))
            assert source_result_text(changed, result) != text
    if outcome == "body":
        assert text.endswith("Приложение ответило")
        if mode == "text":
            assert text == "Приложение ответило"
    elif outcome == "empty":
        assert text.endswith("Handler completed.")
    elif outcome == "long":
        assert text.endswith("[response truncated]")
    else:
        assert outcome in text and result.reason.value in text
        assert text.isascii()
    default = replace(event, command_display_mode=None)
    assert source_result_text(default, result, mode=mode) == text
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_display не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
