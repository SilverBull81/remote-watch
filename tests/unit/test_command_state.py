# Проверки повторов, рестартов, сроков и свежести команд без сети.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260930-193650
#
# Состав модуля:
# -> scenario(): Данные сценария с фиксированными сроками.
# -> test_single_start_and_result_replay(): Одно начало исполнения при повторных запросах.
# -> test_fencing_before_execution(): Защита от чужой цели и подмены аргументов.
# -> test_expiry_and_restart(): Запрет восстановления срока после истечения или рестарта.
# -> test_relative_clock_budget(): Независимость от начала отсчёта часов клиента.
# -> test_no_wall_clock_dependency(): Отсутствие зависимости от UTC машин.
# -> test_invalid_transitions(): Отклонение невозможных переходов и записей.
# -> test_confirmation_freshness(): Связь свежего подтверждения с actor и точным намерением.
# -> test_deadline_validation(): Отказ при неверных показаниях монотонных часов.
# -> test_rejection_and_late_result(): Различение отказа до исполнения и позднего результата.


#******************************************************************************************************************
# ИМПОРТ И ОПРЕДЕЛЕНИЯ
#******************************************************************************************************************
from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pytest

from remote_watch import Identity
from remote_watch.command_freshness import CommandChallenge, confirm_command
from remote_watch.command_protocol import (
    CommandClaim,
    CommandGrant,
    CommandOutcome,
    CommandReason,
    CommandRef,
    CommandRequest,
    CommandSession,
    callback_result,
    message_digest,
)
from remote_watch.command_state import (
    CommandAction,
    CommandDeadline,
    CommandPhase,
    CommandRecord,
    advance_command,
    recover_command,
)


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Данные сценария с фиксированными сроками
#------------------------------------------------------------------------------------------------------------------
def scenario(identity: Identity) -> tuple[CommandRecord, dict[str, Any]]:

    """Prepare an immutable intent and deterministic relative deadlines.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :return: Prepare an immutable intent and deterministic relative deadlines.
    :rtype: tuple[CommandRecord, dict[str, Any]]
    """

    ref = CommandRef(identity=identity, session_id="1" * 32, hub_epoch="2" * 32, command_id="3" * 32)
    event = CommandRequest(ref=ref, source_id="telegram", source_event_id="1001", actor_id="42", conversation_id="-123",
                           name="resume_load")
    claim = CommandClaim(ref=ref, claim_id="4" * 32, request_digest=message_digest(event))
    options = {
        "session": CommandSession(identity=identity, session_id=ref.session_id,
                                  hub_epoch=ref.hub_epoch, remaining_ttl=30),
        "claim": claim,
        "command_deadline": CommandDeadline.from_response(10, 100, 101, ref.hub_epoch),
        "session_deadline": CommandDeadline.from_response(30, 95, 96, ref.hub_epoch),
        "grant": CommandGrant(request=event, claim_id=claim.claim_id, remaining_ttl=20, execution_timeout=10),
        "now": 102,
    }
    return CommandRecord(request=event), options
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Одно начало исполнения при повторных запросах
#------------------------------------------------------------------------------------------------------------------
def test_single_start_and_result_replay(identity: Identity) -> None:

    """Exercise resume and suspend semantics with persist-before-effect ordering.

    :param identity: Synthetic application identity.
    :type identity: Identity
    """

    initial, options = scenario(identity)
    current = advance_command(initial, CommandAction.CLAIM, **options).record
    calls = 0
    for _ in range(3):
        # В этом тесте присваивание моделирует успешный commit. Настоящий atomic
        # compare-and-swap и crash-safe журнал появятся на этапе 0.3.2.
        transition = advance_command(current, CommandAction.START, **options)
        current = transition.record
        calls += int(transition.start_callback)
    assert calls == 1 and current.phase is CommandPhase.STARTED
    result = callback_result(initial.request.ref, options["claim"].claim_id, None)
    current = advance_command(current, CommandAction.FINISH, result=result, **options).record
    repeated = advance_command(current, CommandAction.FINISH, result=result, **options)
    assert repeated.record == current and not repeated.start_callback
    assert not advance_command(current, CommandAction.START, **options).start_callback
    with pytest.raises(ValueError, match="conflicting"):
        advance_command(current, CommandAction.FINISH, result=replace(result, text="changed"), **options)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Защита от чужой цели и подмены аргументов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["service", "environment", "region", "host", "instance_id",
    "session_id", "hub_epoch", "claim_id", "arguments", "grant_claim", "grant_request"])
def test_fencing_before_execution(
    identity: Identity,
    field: str,
) -> None:

    """Reject all forms of cross-application, stale-session and payload substitution.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param field: Selected field for a substitution attempt.
    :type field: str
    """

    record, options = scenario(identity)
    record = advance_command(record, CommandAction.CLAIM, **options).record
    if field in ("service", "environment", "region", "host", "instance_id"):
        options["session"] = replace(options["session"], identity=replace(identity, **{field: "other"}))
    elif field in ("session_id", "hub_epoch"):
        options["session"] = replace(options["session"], **{field: "9" * 32})
    elif field == "claim_id":
        options["claim"] = replace(options["claim"], claim_id="9" * 32)
    elif field == "arguments":
        record = replace(record, request=replace(record.request, arguments={"x": "changed"}))
    elif field == "grant_claim":
        options["grant"] = replace(options["grant"], claim_id="9" * 32)
    else:
        options["grant"] = replace(options["grant"], request=replace(record.request, name="suspend_load"))
    with pytest.raises(ValueError, match="mismatch|another claim"):
        advance_command(record, CommandAction.START, **options)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет восстановления срока после истечения или рестарта
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("phase", [CommandPhase.READY, CommandPhase.CLAIMED, CommandPhase.STARTED])
@pytest.mark.parametrize("case", ["expired", "session_expired", "rollback", "restart"])
def test_expiry_and_restart(
    identity: Identity,
    phase: CommandPhase,
    case: str,
) -> None:

    """Never revive a command after an expired budget, clock reset or restart.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param phase: Journal phase before expiry or restart.
    :type phase: CommandPhase

    :param case: Selected boundary or failure scenario.
    :type case: str
    """

    record, options = scenario(identity)
    if phase is not CommandPhase.READY:
        record = advance_command(record, CommandAction.CLAIM, **options).record
    if phase is CommandPhase.STARTED:
        record = advance_command(record, CommandAction.START, **options).record
    if case == "restart":
        terminal = recover_command(record)
    else:
        options["now"] = 110 if case == "expired" else 100 if case == "rollback" else 102
        if case == "session_expired":
            options["session_deadline"] = CommandDeadline.from_response(5, 95, 96, record.request.ref.hub_epoch)
        transition = advance_command(record, CommandAction.START, **options)
        assert not transition.start_callback
        terminal = transition.record
    expected = CommandPhase.UNKNOWN if phase is CommandPhase.STARTED else CommandPhase.EXPIRED
    assert terminal.phase is expected
    assert recover_command(terminal) == terminal
    # Даже свежий локальный срок не возвращает терминальную запись к исполнению.
    _, fresh = scenario(identity)
    assert not advance_command(terminal, CommandAction.START, **fresh).start_callback
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимость от начала отсчёта часов клиента
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("origin", [0, 1000, 1_000_000_000])
@pytest.mark.parametrize("rtt", [0, 2, 10, 15])
def test_relative_clock_budget(
    origin: float,
    rtt: float,
) -> None:

    """Conserve a remote ten-second budget regardless of local monotonic origin.

    :param origin: Arbitrary local monotonic clock origin.
    :type origin: float

    :param rtt: Complete request/response duration in seconds.
    :type rtt: float
    """

    deadline = CommandDeadline.from_response(10, origin, origin + rtt, "2" * 32)
    assert deadline.remaining(origin + rtt, "2" * 32) == max(0, 10 - rtt)
    assert deadline.remaining(origin + rtt + 5, "2" * 32) == max(0, 5 - rtt)
    assert deadline.remaining(origin + rtt, "9" * 32) == 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отсутствие зависимости от UTC машин
#------------------------------------------------------------------------------------------------------------------
def test_no_wall_clock_dependency(
    identity: Identity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Exclude local UTC from the command state and deadline contract.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param monkeypatch: Pytest dependency patch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Запрет обращения к UTC в проверке временного контракта
    #--------------------------------------------------------------------------------------------------------------
    def wall_clock() -> float:

        """Fail if command processing tries to consult either machine's UTC.

        :return: Fail if command processing tries to consult either machine's UTC.
        :rtype: float
        """

        raise AssertionError("wall clock must not participate")
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(time, "time", wall_clock)
    record, options = scenario(identity)
    claimed = advance_command(record, CommandAction.CLAIM, **options).record
    assert advance_command(claimed, CommandAction.START, **options).start_callback
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение невозможных переходов и записей
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["start_without_claim", "start_without_grant", "finish_before_start",
    "oversized_deadline", "terminal_without_result", "result_before_terminal", "claimed_without_id",
    "ready_with_id", "other_result"])
def test_invalid_transitions(
    identity: Identity,
    case: str,
) -> None:

    """Reject illegal transitions and inconsistent journal snapshots.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param case: Selected boundary or failure scenario.
    :type case: str
    """

    record, options = scenario(identity)
    result = callback_result(record.request.ref, options["claim"].claim_id, "ok")
    with pytest.raises((ValueError, TypeError)):
        if case == "start_without_claim":
            advance_command(record, CommandAction.START, **options)
        elif case == "start_without_grant":
            record = advance_command(record, CommandAction.CLAIM, **options).record
            options["grant"] = None
            advance_command(record, CommandAction.START, **options)
        elif case == "finish_before_start":
            advance_command(record, CommandAction.FINISH, result=result, **options)
        elif case == "oversized_deadline":
            record = advance_command(record, CommandAction.CLAIM, **options).record
            options["command_deadline"] = CommandDeadline.from_response(20, 100, 101, "2" * 32)
            advance_command(record, CommandAction.START, **options)
        elif case == "terminal_without_result":
            replace(record, phase=CommandPhase.COMPLETED)
        elif case == "result_before_terminal":
            replace(record, result=result)
        elif case == "claimed_without_id":
            replace(record, phase=CommandPhase.CLAIMED)
        elif case == "ready_with_id":
            replace(record, claim_id="4" * 32)
        else:
            replace(record, phase=CommandPhase.COMPLETED, claim_id="5" * 32, result=result)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Связь свежего подтверждения с actor и точным намерением
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["actor", "conversation", "source", "nonce", "arguments", "name",
    "epoch", "expired", "late_initial_message", "replay", "valid"])
def test_confirmation_freshness(
    identity: Identity,
    case: str,
) -> None:

    """Require fresh actor-bound intent after arbitrary initial message delay.

    :param identity: Synthetic application identity.
    :type identity: Identity

    :param case: Selected boundary or failure scenario.
    :type case: str
    """

    record, _ = scenario(identity)
    # Намерение могло прийти через сутки. Hub начинает новое окно подтверждения
    # сейчас, но старое сообщение само по себе не даёт разрешения на действие.
    challenge = CommandChallenge(request=record.request, nonce="a" * 32,
        deadline=CommandDeadline.from_response(30, 100, 100, "2" * 32))
    options = dict(request=record.request, nonce="a" * 32, source_id="telegram", actor_id="42",
                   conversation_id="-123", hub_epoch="2" * 32, now=110)
    if case in ("actor", "conversation", "source", "nonce", "epoch"):
        key = {"actor": "actor_id", "conversation": "conversation_id", "source": "source_id",
               "nonce": "nonce", "epoch": "hub_epoch"}[case]
        options[key] = "b" * 32
    elif case in ("arguments", "name"):
        options["request"] = replace(record.request, **(
            {"arguments": {"x": "forged"}} if case == "arguments" else {"name": "suspend_load"}))
    elif case == "expired":
        options["now"] = 130
    elif case == "late_initial_message":
        options["nonce"] = ""
    elif case == "replay":
        challenge = confirm_command(challenge, **options).challenge
    decision = confirm_command(challenge, **options)
    assert decision.accepted is (case == "valid")
    if decision.accepted:
        assert decision.challenge.consumed
    assert "a" * 32 not in repr(challenge)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ при неверных показаниях монотонных часов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["interval", "backwards", "infinity", "negative", "zero", "bool"])
def test_deadline_validation(case: str) -> None:

    """Reject clock observations that cannot define a finite local deadline.

    :param case: Selected boundary or failure scenario.
    :type case: str
    """

    values = {"interval": (301, 0, 1), "backwards": (10, 2, 1), "infinity": (10, float("inf"), 1),
              "negative": (10, -1, 1), "zero": (0, 0, 1), "bool": (True, 0, 1)}
    with pytest.raises((ValueError, TypeError)):
        CommandDeadline.from_response(*values[case], "2" * 32)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Различение отказа до исполнения и позднего результата
#------------------------------------------------------------------------------------------------------------------
def test_rejection_and_late_result(identity: Identity) -> None:

    """Distinguish rejection before execution from a late known callback result.

    :param identity: Synthetic application identity.
    :type identity: Identity
    """

    record, options = scenario(identity)
    rejected = callback_result(record.request.ref, options["claim"].claim_id, None)
    rejected = replace(rejected, outcome=CommandOutcome.REJECTED, reason=CommandReason.DENIED)
    decision = advance_command(record, CommandAction.REJECT, result=rejected, **options)
    assert decision.record.phase is CommandPhase.REJECTED and not decision.start_callback

    current = advance_command(record, CommandAction.CLAIM, **options).record
    current = advance_command(current, CommandAction.START, **options).record
    options["now"] = 1000
    result = callback_result(record.request.ref, options["claim"].claim_id, "Callback завершился")
    completed = advance_command(current, CommandAction.FINISH, result=result, **options)
    assert completed.record.phase is CommandPhase.COMPLETED and not completed.start_callback
    unknown = recover_command(current)
    with pytest.raises(ValueError, match="conflicting"):
        advance_command(unknown, CommandAction.FINISH, result=result, **options)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.unit.test_command_state не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
