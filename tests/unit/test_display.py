# Отображение уведомлений без изменения исходных данных и протокола доставки.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-210047
#
# Тесты:
# -> test_display_modes(): Прежний full, различимый compact и только текст с исключением.
# -> test_display_validation(): Проверка настроек назначения до открытия сети.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from remote_watch import Notification
from remote_watch.adapters._common import render
from remote_watch.adapters.ntfy import NtfyConfig
from remote_watch.adapters.telegram import TelegramConfig
from remote_watch.notifications.delivery import Delivery
from remote_watch.relay_protocol import RelayRequest


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Прежний full, различимый compact и только текст с исключением
#------------------------------------------------------------------------------------------------------------------
def test_display_modes(notification: Notification) -> None:

    """Preserve wire identity while changing only the provider-facing text.

    :param notification: Fixed synthetic notification.
    :type notification: Notification
    """

    # notification — неизменяемый источник с event/session/Identity, общими для всех режимов.
    event = replace(notification, exception="Подробности исключения")
    delivery = Delivery(notification=event, destination_id="phone", delivery_id="d1")
    request = RelayRequest(delivery=delivery, alias="phone", remaining_ttl=10, timeout=5)
    original = request.to_dict()
    full = render(delivery)
    assert full == ("service=quotes environment=test region=test-region\n"
        "host=test-host instance_id=one\n[ERROR] test.application 2026-09-28T10:00:00+00:00\n"
        "event_id=event-1 session_id=session-1\ndelivery_id=d1\n\n"
        "Источник недоступен\n\nПодробности исключения")
    assert render(delivery, "text") == "Источник недоступен\n\nПодробности исключения"
    compact = render(delivery, "compact")
    assert compact.startswith('["quotes","test","test-region","test-host","one"] [ERROR]\n\n')
    assert "event_id=" not in compact and "delivery_id=" not in compact
    assert render(delivery, "full", ("level",)) == "[ERROR]\n\nИсточник недоступен\n\nПодробности исключения"
    for field in ("service", "environment", "region", "host", "instance_id"):
        changed_identity = replace(event.identity, **{field: "other"})
        changed = replace(delivery, notification=replace(event, identity=changed_identity))
        assert render(changed, "compact") != compact
    assert request.to_dict() == original and event.event_id == "event-1" and event.session_id == "session-1"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка настроек назначения до открытия сети
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
@pytest.mark.parametrize(("mode", "fields", "valid"), [
    ("full", None, True), ("compact", None, True), ("text", None, True),
    ("compact", ["identity", "level"], True), ("full", [], True),
    ("full", ["ids", "time"], True), ("unknown", None, False),
    ("compact", ["level"], False), ("text", [], False),
    ("full", ["PRIVATE"], False), ("full", ["level", "level"], False),
    ("full", "level", False), ("full", [{}], False),
])
def test_display_validation(
    provider: str,
    mode: str,
    fields: Any,
    valid: bool,
) -> None:

    """Reject ambiguous display configuration before creating provider resources.

    :param provider: Adapter whose typed destination is constructed.
    :type provider: str

    :param mode: Requested display mode.
    :type mode: str

    :param fields: Valid or deliberately malformed field selection.
    :type fields: Any

    :param valid: Whether the configuration must be accepted.
    :type valid: bool
    """

    # provider/mode/fields — только синтетические настройки; valid — ожидаемый результат проверки.
    fields = list(fields) if isinstance(fields, list) else fields
    factory = TelegramConfig if provider == "telegram" else NtfyConfig
    arguments = {"token_env": "SYNTHETIC_TG", "chat_id": 123} if provider == "telegram" else {"topic": "synthetic"}
    if not valid:
        with pytest.raises(ValueError):
            factory(**arguments, display_mode=mode, display_fields=fields)
    else:
        config = factory(**arguments, display_mode=mode, display_fields=fields)
        assert config.display_fields == (None if fields is None else tuple(fields))
        if isinstance(fields, list):
            fields.append("PRIVATE")
            assert "PRIVATE" not in config.display_fields
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_display не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
