# Проверки моделей, сериализации и результатов команд без сети.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Тесты:
# -> request(): Детерминированный запрос с вымышленными аргументами.
# -> test_command_wire_roundtrip(): Передача всех моделей через строгий JSON.
# -> test_command_wire_rejects(): Отказ на неоднозначных и слишком больших данных.
# -> test_command_interval_bounds(): Пределы относительных сроков команд.
# -> test_command_snapshots_and_metadata(): Копирование данных и сохранение callbacks только в приложении.
# -> test_registration_bounds(): Границы и однозначность списка команд.
# -> test_command_correlation(): Связь разрешения и квитанции со всем содержимым запроса.
# -> test_callback_result_contract(): Различение завершения callback и неверного результата.
# -> test_callback_result_never_stringifies(): Запрет неявного исполнения __str__.
# -> test_semantic_rejection(): Отклонение противоречивых описаний и результатов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from functools import partial

import pytest

from remote_watch import CommandRegistry, CommandSpec, Identity
from remote_watch.commands.protocol import (
    MAX_COMMAND_BYTES,
    CommandCapability,
    CommandClaim,
    CommandGrant,
    CommandOutcome,
    CommandReason,
    CommandReceipt,
    CommandRef,
    CommandRegistration,
    CommandRequest,
    CommandResult,
    CommandSession,
    callback_result,
    decode_command,
    describe_commands,
    encode_command,
    message_digest,
)


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Детерминированный запрос с вымышленными аргументами
#------------------------------------------------------------------------------------------------------------------
def request(identity: Identity) -> CommandRequest:

    """Build a deterministic command with synthetic private arguments.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :return: Synthetic command bound to the test identity and session.
    :rtype: CommandRequest
    """

    # identity — явно заданные сведения о тестовом приложении.

    return CommandRequest(ref=CommandRef(identity=identity, session_id="1" * 32,
        hub_epoch="2" * 32, command_id="3" * 32), source_id="telegram-main", source_event_id="1001", actor_id="42",
        conversation_id="-123", name="suspend_load", arguments={"value": "synthetic_private_value"})
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Передача всех моделей через строгий JSON
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["registration", "session", "request", "claim", "grant", "result", "receipt"])
def test_command_wire_roundtrip(
    identity: Identity,
    kind: str,
) -> None:

    """Round-trip each closed wire shape without optional network dependencies.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param kind: Selected protocol message kind.
    :type kind: str
    """

    # identity — явно заданные сведения о тестовом приложении.
    # kind — проверяемый вид сообщения.

    event = request(identity)
    claim = CommandClaim(ref=event.ref, claim_id="4" * 32, request_digest=message_digest(event))
    result = callback_result(event.ref, claim.claim_id, "Запрос передан приложению")
    messages = {
        "registration": CommandRegistration(identity=identity, session_id=event.ref.session_id,
            capabilities=(CommandCapability(name=event.name, required_scope="load:write"),)),
        "session": CommandSession(identity=identity, session_id=event.ref.session_id,
            hub_epoch=event.ref.hub_epoch, remaining_ttl=30),
        "request": event, "claim": claim,
        "grant": CommandGrant(request=event, claim_id=claim.claim_id, remaining_ttl=30, execution_timeout=10),
        "result": result,
        "receipt": CommandReceipt(ref=event.ref, claim_id=claim.claim_id, result_digest=message_digest(result)),
    }
    message = messages[kind]
    encoded = encode_command(message)
    assert decode_command(encoded) == message
    assert encode_command(decode_command(encoded)) == encoded
    assert json.loads(encoded)["kind"] == kind
    assert len(encoded) <= MAX_COMMAND_BYTES
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ на неоднозначных и слишком больших данных
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["version", "bool_version", "unknown_kind", "extra", "missing",
    "identity_extra", "duplicate", "nested_duplicate", "nan", "infinity", "overflow", "list",
    "invalid_utf8", "too_large", "invalid_name", "arguments", "arguments_count", "arguments_bytes",
    "bad_epoch", "bad_identity", "array_kind", "bool_payload"])
def test_command_wire_rejects(
    identity: Identity,
    case: str,
) -> None:

    """Reject ambiguous and oversized wire data without revealing its contents.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param case: Selected boundary or failure scenario.
    :type case: str
    """

    # identity — явно заданные сведения о тестовом приложении.
    # case — выбранный сценарий проверки.

    body = json.loads(encode_command(request(identity)))
    payload = body["payload"]
    if case == "version":
        body["schema_version"] = 2
    elif case == "bool_version":
        body["schema_version"] = True
    elif case == "unknown_kind":
        body["kind"] = "synthetic_private_value"
    elif case == "extra":
        payload["token"] = "synthetic_private_value"
    elif case == "missing":
        del payload["arguments"]
    elif case == "identity_extra":
        payload["ref"]["identity"]["token"] = "synthetic_private_value"
    elif case == "invalid_name":
        payload["name"] = "../eval"
    elif case == "arguments":
        payload["arguments"] = {"n": 1}
    elif case == "arguments_count":
        payload["arguments"] = {str(n): "x" for n in range(33)}
    elif case == "arguments_bytes":
        payload["arguments"] = {str(n): "я" * 512 for n in range(5)}
    elif case == "bad_epoch":
        payload["ref"]["hub_epoch"] = "short"
    elif case == "bad_identity":
        payload["ref"]["identity"]["host"] = False
    elif case == "array_kind":
        body["kind"] = []
    elif case == "bool_payload":
        body["payload"] = True
    raw = json.dumps(body).encode()
    if case == "duplicate":
        raw = raw.replace(b'"schema_version": 1', b'"schema_version": 1,"schema_version": 1')
    elif case == "nested_duplicate":
        raw = raw.replace(b'"actor_id": "42"', b'"actor_id": "42","actor_id":"42"')
    elif case in ("nan", "infinity", "overflow"):
        raw = raw.replace(b'"actor_id": "42"', b'"actor_id": ' + {
            "nan": b"NaN", "infinity": b"Infinity", "overflow": b"1e999"}[case])
    elif case == "list":
        raw = b"[]"
    elif case == "invalid_utf8":
        raw = b"\xff"
    elif case == "too_large":
        raw = b" " * (MAX_COMMAND_BYTES + 1)
    with pytest.raises(ValueError, match="^invalid command message$"):
        decode_command(raw)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Пределы относительных сроков команд
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [-1, 0, 301, True, None, "30", float("nan"), float("inf")])
def test_command_interval_bounds(
    identity: Identity,
    value: object,
) -> None:

    """Reject unsafe remotely supplied time budgets.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param value: Value to validate or normalize.
    :type value: object
    """

    # identity — явно заданные сведения о тестовом приложении.
    # value — проверяемое недопустимое значение срока.

    with pytest.raises((ValueError, TypeError)):
        CommandSession(identity=identity, session_id="1" * 32, hub_epoch="2" * 32, remaining_ttl=value)
    with pytest.raises((ValueError, TypeError)):
        CommandCapability(name="status", required_scope="load:read", timeout=value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Копирование данных и сохранение callbacks только в приложении
#------------------------------------------------------------------------------------------------------------------
def test_command_snapshots_and_metadata(identity: Identity) -> None:

    """Preserve partial callbacks locally and export detached capability metadata.

    :param identity: Synthetic application identity.
    :type identity: Identity
    """

    # identity — явно заданные сведения о тестовом приложении.

    calls: list[str] = []
    registry = CommandRegistry.from_callbacks({"suspend_load": partial(calls.append, "suspend")})
    capabilities = list(describe_commands(registry))
    registration = CommandRegistration(identity=identity, session_id="1" * 32, capabilities=capabilities)
    capabilities.clear()
    assert len(registration.capabilities) == 1 and not calls
    assert not registration.capabilities[0].read_only
    assert b"callback" not in encode_command(registration)

    arguments = {"value": "before"}
    event = replace(request(identity), arguments=arguments)
    arguments["value"] = "after"
    assert event.arguments["value"] == "before"
    with pytest.raises(TypeError):
        event.arguments["value"] = "changed"
    with pytest.raises(FrozenInstanceError):
        event.name = "resume_load"
    assert "synthetic_private_value" not in repr(request(identity))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Границы и однозначность списка команд
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["empty", "too_many", "duplicate", "bad_type"])
def test_registration_bounds(
    identity: Identity,
    case: str,
) -> None:

    """Keep command registration finite and unambiguous.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param case: Selected boundary or failure scenario.
    :type case: str
    """

    # identity — явно заданные сведения о тестовом приложении.
    # case — выбранный сценарий проверки.

    capability = CommandCapability(name="status", required_scope="read")
    values = {"empty": [], "too_many": [capability] * 65,
              "duplicate": [capability, capability], "bad_type": [object()]}
    with pytest.raises((ValueError, TypeError)):
        CommandRegistration(identity=identity, session_id="1" * 32, capabilities=values[case])
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Связь разрешения и квитанции со всем содержимым запроса
#------------------------------------------------------------------------------------------------------------------
def test_command_correlation(identity: Identity) -> None:

    """Bind grants and receipts to complete canonical payloads, including arguments.

    :param identity: Synthetic application identity.
    :type identity: Identity
    """

    # identity — явно заданные сведения о тестовом приложении.

    event = request(identity)
    claim = CommandClaim(ref=event.ref, claim_id="4" * 32, request_digest=message_digest(event))
    grant = CommandGrant(request=event, claim_id=claim.claim_id, remaining_ttl=30, execution_timeout=10)
    assert grant.matches(claim)
    assert not replace(grant, request=replace(event, arguments={"value": "forged"})).matches(claim)
    assert not replace(grant, claim_id="5" * 32).matches(claim)
    assert message_digest(replace(event, arguments={"a": "1", "b": "2"})) == message_digest(
        replace(event, arguments={"b": "2", "a": "1"}))
    result = callback_result(event.ref, claim.claim_id, "done")
    receipt = CommandReceipt(ref=event.ref, claim_id=claim.claim_id, result_digest=message_digest(result))
    assert receipt.matches(result)
    assert not receipt.matches(replace(result, text="another result"))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Различение завершения callback и неверного результата
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "", "готово", "я" * 2048, "я" * 2049, "\ud800", 1, False, {}])
def test_callback_result_contract(
    identity: Identity,
    value: object,
) -> None:

    """Distinguish valid completion text from an unknown post-callback outcome.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param value: Value to validate or normalize.
    :type value: object
    """

    # identity — явно заданные сведения о тестовом приложении.
    # value — возвращаемое обработчиком значение.

    result = callback_result(request(identity).ref, "4" * 32, value)
    valid = value is None or type(value) is str and value != "\ud800" and len(value) <= 2048
    assert result.outcome is (CommandOutcome.COMPLETED if valid else CommandOutcome.UNKNOWN)
    if valid:
        assert result.text == value and result.reason is None
    else:
        assert result.text is None and result.reason is CommandReason.INVALID_RESULT
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет неявного исполнения __str__
#------------------------------------------------------------------------------------------------------------------
def test_callback_result_never_stringifies(identity: Identity) -> None:

    """Do not invoke arbitrary user code while formatting a callback result.

    :param identity: Synthetic application identity.
    :type identity: Identity
    """

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Обнаружение нежелательного вызова пользовательского __str__
    #--------------------------------------------------------------------------------------------------------------
    class PrivateResult:
        """Detect accidental implicit conversion of an application-owned object."""

        #----------------------------------------------------------------------------------------------------------
        # СПЕЦИАЛЬНЫЙ МЕТОД : Обнаружение неявного преобразования результата в строку
        #----------------------------------------------------------------------------------------------------------
        def __str__(self) -> str:

            """Fail if normalization attempts user-defined string conversion.

            :return: String representation used to exercise result validation.
            :rtype: str
            """

            raise AssertionError("must not call __str__")
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    result = callback_result(request(identity).ref, "4" * 32, PrivateResult())
    assert result.outcome is CommandOutcome.UNKNOWN
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение противоречивых описаний и результатов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["bool_flag", "result_without_claim", "bad_reason", "text_on_failure",
    "bad_result_digest", "grant_timeout", "bad_scope", "unbounded_spec"])
def test_semantic_rejection(
    identity: Identity,
    case: str,
) -> None:

    """Reject contradictory policies and impossible execution results.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param case: Selected boundary or failure scenario.
    :type case: str
    """

    # identity — явно заданные сведения о тестовом приложении.
    # case — выбранный сценарий проверки.

    event = request(identity)
    with pytest.raises((ValueError, TypeError)):
        if case == "bool_flag":
            CommandCapability(name="status", required_scope="read", read_only=1)
        elif case == "result_without_claim":
            CommandResult(ref=event.ref, claim_id=None, outcome=CommandOutcome.COMPLETED)
        elif case == "bad_reason":
            CommandResult(ref=event.ref, claim_id="4" * 32, outcome=CommandOutcome.COMPLETED,
                          reason=CommandReason.TIMEOUT)
        elif case == "text_on_failure":
            CommandResult(ref=event.ref, claim_id="4" * 32, outcome=CommandOutcome.UNKNOWN,
                          reason=CommandReason.TIMEOUT, text="synthetic_private_value")
        elif case == "bad_result_digest":
            CommandReceipt(ref=event.ref, claim_id=None, result_digest="x")
        elif case == "grant_timeout":
            CommandGrant(request=event, claim_id="4" * 32, remaining_ttl=1, execution_timeout=10)
        elif case == "bad_scope":
            CommandCapability(name="status", required_scope="read" * 129)
        else:
            describe_commands(CommandRegistry(specs=(CommandSpec(name="status", callback=lambda: None,
                                                                timeout=301),)))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.unit.test_command_protocol не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
