# Проверки регистрации команд, partial и неизменяемого контекста.
# Функции:
# -> set_stop(): Callback приложения с привязанным Event.
# -> context_status(): Callback с контекстом.
# -> async_status(): Асинхронный callback.
# -> validate_count(): Пример валидации именованных аргументов.
# Тесты:
# -> test_partial_callbacks_are_not_invoked_by_registration(): Пользовательский сценарий.
# -> test_specs_and_async_callbacks(): Расширенный контракт и async.
# -> test_registry_copies_specs_and_rejects_duplicates(): Неизменяемость реестра.
# -> test_invalid_command_specs(): Неправильные имена, сигнатуры и политики.
# -> test_context_copies_arguments(): Контекст не зависит от исходного mapping.
# -> test_context_rejects_unbounded_arguments(): Типы и размеры аргументов.
#
# Version 1.0.0
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
# Дата и время последнего изменения: 260928-110519

#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
import asyncio
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from functools import partial
from threading import Event

import pytest

from remote_watch import CommandContext, CommandRegistry, CommandSpec, Identity, WatcherConfig

#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Пример обработчика приложения
#------------------------------------------------------------------------------------------------------------------
def set_stop(
    *,
    load_stop_event: Event,
    ) -> None:

    """Set application-owned state when explicitly called.

    :param load_stop_event: Event owned by the application.
    :type load_stop_event: Event
    """

    # load_stop_event - флаг остановки приложения.

    load_stop_event.set()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Обработчик с явным контекстом
#------------------------------------------------------------------------------------------------------------------
def context_status(
    context: CommandContext,
    ) -> str:

    """Return the addressed instance name.

    :param context: Authorized request context supplied by a future dispatcher.
    :type context: CommandContext

    :return: Logical instance identifier.
    :rtype: str
    """

    # context - контекст запроса.

    return context.identity.instance_id
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Асинхронный пользовательский callback
#------------------------------------------------------------------------------------------------------------------
async def async_status() -> str:

    """Return a deterministic asynchronous result.

    :return: Status text.
    :rtype: str
    """

    return "ready"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Валидатор аргументов команды
#------------------------------------------------------------------------------------------------------------------
def validate_count(
    arguments: Mapping[str, str],
    ) -> None:

    """Require one positive integer argument.

    :param arguments: Named textual command arguments.
    :type arguments: Mapping[str, str]
    """

    # arguments - именованные аргументы команды.

    if set(arguments) != {"count"} or int(arguments["count"]) <= 0:
        raise ValueError("positive count is required")
#------------------------------------------------------------------------------------------------------------------

#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Регистрация словаря не вызывает partial и копирует набор команд
#------------------------------------------------------------------------------------------------------------------
def test_partial_callbacks_are_not_invoked_by_registration(
    identity: Identity,
    ) -> None:

    """Preserve the user's partial/Event pattern without registration side effects.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - идентичность приложения.

    stop_event = Event()
    callbacks = {"suspend_load": partial(set_stop, load_stop_event=stop_event), "resume_load": stop_event.clear}
    config = WatcherConfig(identity=identity, commands=callbacks)
    callbacks.clear()
    assert isinstance(config.commands, CommandRegistry)
    assert list(config.commands) == ["suspend_load", "resume_load"]
    assert not stop_event.is_set()
    spec = config.commands["suspend_load"]
    assert spec.required_scope == "command:suspend_load"
    assert not spec.read_only and not spec.idempotent
    spec.callback()  # Явный локальный вызов приложения, не удалённый dispatcher.
    assert stop_event.is_set()
    config.commands["resume_load"].callback()
    assert not stop_event.is_set()
    assert "load_stop_event" not in repr(spec)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Явные спецификации и async-функции регистрируются вместе
#------------------------------------------------------------------------------------------------------------------
def test_specs_and_async_callbacks() -> None:

    """Support contextual, validated and asynchronous user handlers."""

    spec = CommandSpec(name="check_load", callback=context_status, takes_context=True,
                       required_scope="load:read", read_only=True, idempotent=True,
                       validate_arguments=validate_count)
    registry = CommandRegistry(specs=(spec, CommandSpec(name="async_status", callback=partial(async_status))))
    assert registry["check_load"].validate_arguments is validate_count
    assert asyncio.run(registry["async_status"].callback()) == "ready"
    assert registry["check_load"].required_scope == "load:read"
    assert registry.get("absent") is None
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Реестр фиксирует список и запрещает неоднозначные имена
#------------------------------------------------------------------------------------------------------------------
def test_registry_copies_specs_and_rejects_duplicates() -> None:

    """Freeze the registry and reject ambiguous duplicate registrations."""

    spec = CommandSpec(name="check", callback=async_status)
    specs = [spec]
    registry = CommandRegistry(specs=specs)
    specs.clear()
    assert len(registry) == 1
    with pytest.raises(ValueError, match="duplicate"):
        CommandRegistry(specs=(spec, spec))
    with pytest.raises(FrozenInstanceError):
        registry.specs = ()
    with pytest.raises(KeyError):
        registry["missing"]
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Неправильный callback или политика отклоняются до исполнения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("changes", [
    {"name": "/start"}, {"name": "UPPER"}, {"name": "a b"}, {"name": ""},
    {"callback": None}, {"callback": set_stop}, {"callback": context_status},
    {"takes_context": True}, {"timeout": float("nan")}, {"timeout": True},
    {"required_scope": ""}, {"read_only": "yes"}, {"validate_arguments": validate_count},
    {"takes_context": True, "callback": context_status, "validate_arguments": async_status},
])
def test_invalid_command_specs(
    changes: dict[str, object],
    ) -> None:

    """Fail registration for invalid command declarations.

    :param changes: Invalid specification fields.
    :type changes: dict[str, object]
    """

    # changes - недопустимые параметры команды.

    with pytest.raises((TypeError, ValueError)):
        CommandSpec(**({"name": "check", "callback": async_status} | changes))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Аргументы контекста копируются и не выводятся в repr
#------------------------------------------------------------------------------------------------------------------
def test_context_copies_arguments(
    identity: Identity,
    ) -> None:

    """Detach request arguments from caller-owned mutable state.

    :param identity: Application identity fixture.
    :type identity: Identity
    """

    # identity - идентичность приложения.

    arguments = {"count": "5"}
    context = CommandContext(command_id="c1", actor_id="user-1", conversation_id="chat-1",
                             identity=identity, session_id="s1", arguments=arguments)
    arguments["count"] = "invalid"
    assert context.arguments["count"] == "5"
    with pytest.raises(TypeError):
        context.arguments["count"] = "7"
    assert "count" not in repr(context)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Контекст не удерживает произвольные объекты или неограниченные данные
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("arguments", [{"count": 5}, {"x": "a" * 1025},
                                       {str(index): "x" for index in range(33)},
                                       {str(index): "x" * 1000 for index in range(5)}])
def test_context_rejects_unbounded_arguments(
    identity: Identity,
    arguments: object,
    ) -> None:

    """Reject invalid types and both individual and aggregate argument overflow.

    :param identity: Application identity fixture.
    :type identity: Identity

    :param arguments: Invalid argument mapping.
    :type arguments: object
    """

    # identity - идентичность приложения.
    # arguments - недопустимые аргументы.

    with pytest.raises((TypeError, ValueError)):
        CommandContext(command_id="c1", actor_id="a1", conversation_id="chat",
                       identity=identity, session_id="s1", arguments=arguments)
#------------------------------------------------------------------------------------------------------------------
