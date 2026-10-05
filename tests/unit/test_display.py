# Отображение уведомлений без изменения исходных данных и протокола доставки.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-221259
#
# Тесты:
# -> test_display_modes(): Прежний full, различимый compact и только текст с исключением.
# -> test_display_validation(): Проверка настроек назначения до открытия сети.
# -> test_relay_display_wire(): Строгое расширение wire и сохранение старых схем.
# -> test_relay_display_invalid(): Отказ повреждённым настройкам отображения.
# -> test_relay_display_configuration(): Выбор версии и приоритет клиентского режима.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from remote_watch import Notification
from remote_watch.adapters._common import render
from remote_watch.adapters.ntfy import NtfyConfig
from remote_watch.adapters.relay import RelayConfig
from remote_watch.adapters.telegram import TelegramConfig
from remote_watch.notifications.delivery import Delivery, DeliveryResult, DeliveryStatus
from remote_watch.relay_protocol import RelayRequest, decode_response, encode_response


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
# ТЕСТ : Строгое расширение wire и сохранение старых схем
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("schema", "mode"), [(1, None), (2, None), (3, None),
    (3, "full"), (3, "compact"), (3, "text")])
def test_relay_display_wire(
    notification: Notification,
    schema: int,
    mode: str | None,
) -> None:

    """Round-trip presentation independently of identity and delivery correlation.

    :param notification: Synthetic immutable event.
    :type notification: Notification

    :param schema: Explicit protocol version.
    :type schema: int

    :param mode: Optional client presentation override.
    :type mode: str | None
    """

    # schema/mode — новая настройка не добавляет поля в схемы старых клиентов.
    delivery = Delivery(notification=notification, destination_id="local", delivery_id="one",
                        display_mode=mode, display_fields=("identity", "time") if mode == "compact" else None)
    request = RelayRequest(delivery=delivery, alias="phone", remaining_ttl=10, timeout=5, schema_version=schema)
    payload = request.to_dict()
    assert ("display" in payload) is (schema == 3)
    restored = RelayRequest.from_bytes(json.dumps(payload).encode()).delivery
    assert restored.notification.to_dict() == notification.to_dict()
    assert restored.delivery_id == delivery.delivery_id and restored.attempt == delivery.attempt
    assert restored.display_mode == mode and restored.display_fields == delivery.display_fields

    outcome = DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, http_status=200,
                             provider_message_id="receipt")
    response = json.loads(encode_response(delivery, outcome, schema_version=schema))
    decoded = decode_response(response, delivery, schema_version=schema)
    assert decoded.http_status == (None if schema == 1 else 200)
    with pytest.raises(ValueError):
        decode_response(response, delivery, schema_version=2 if schema == 3 else 3)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ повреждённым настройкам отображения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("display", [True, "text", {}, {"mode": "text"},
    {"mode": None, "fields": None}, {"mode": "custom", "fields": None},
    {"mode": "text", "fields": []}, {"mode": "compact", "fields": ["level"]},
    {"mode": "full", "fields": "identity"}, {"mode": "full", "fields": ["unknown"]},
    {"mode": "full", "fields": ["level", "level"]},
    {"mode": "full", "fields": None, "chat_id": 999}])
def test_relay_display_invalid(
    notification: Notification,
    display: Any,
) -> None:

    """Reject malformed, partial or routing-like display payloads before provider dispatch.

    :param notification: Synthetic immutable event.
    :type notification: Notification

    :param display: Deliberately invalid JSON display value.
    :type display: Any
    """

    # display — произвольное поле запроса не превращается в provider-настройку.
    delivery = Delivery(notification=notification, destination_id="phone", delivery_id="one")
    payload = RelayRequest(delivery=delivery, alias="phone", remaining_ttl=10, timeout=5,
                           schema_version=3).to_dict()
    payload["display"] = display
    with pytest.raises(ValueError):
        RelayRequest.from_bytes(json.dumps(payload).encode())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Выбор версии и приоритет клиентского режима
#------------------------------------------------------------------------------------------------------------------
def test_relay_display_configuration(notification: Notification) -> None:

    """Require compatible wire and preserve per-delivery isolation without mutating server defaults.

    :param notification: Synthetic immutable event.
    :type notification: Notification
    """

    # Настройки принадлежат назначению, не общей Notification и не logging extra.
    base = {"endpoint": "https://gateway.invalid", "alias": "phone", "token": "x" * 32}
    assert RelayConfig(**base).schema_version == 1
    assert RelayConfig(**base, display_mode="text").schema_version == 3
    assert RelayConfig(**base, schema_version=3).display_mode is None
    for changes in ({"display_mode": "text", "schema_version": 1},
                    {"display_mode": "full", "schema_version": 2},
                    {"display_fields": []}, {"display_mode": "other"}, {"schema_version": 4}):
        with pytest.raises(ValueError):
            RelayConfig(**base, **changes)

    delivery = Delivery(notification=notification, destination_id="phone", delivery_id="one")
    fields = ["identity", "level"]
    compact = replace(delivery, display_mode="compact", display_fields=fields)
    fields.append("ids")
    assert compact.display_fields == ("identity", "level")
    assert render(compact, "text").startswith('["quotes",')
    assert render(replace(delivery, display_mode="text"), "full", ("ids",)) == notification.message
    assert render(delivery).startswith("service=") and delivery.display_mode is None
    for changes in ({"display_fields": []}, {"display_mode": "compact", "display_fields": ["ids"]}):
        with pytest.raises(ValueError):
            replace(delivery, **changes)
    for schema in (1, 2):
        with pytest.raises(ValueError):
            RelayRequest(delivery=compact, alias="phone", remaining_ttl=10, timeout=5, schema_version=schema)
        payload = RelayRequest(delivery=delivery, alias="phone", remaining_ttl=10, timeout=5,
                               schema_version=schema).to_dict()
        payload["display"] = None
        with pytest.raises(ValueError):
            RelayRequest.from_bytes(json.dumps(payload).encode())

    payload["schema_version"] = 3
    raw = json.dumps(payload).encode()
    with pytest.raises(ValueError):
        RelayRequest.from_bytes(b'{"display":null,' + raw[1:])
    del payload["display"]
    with pytest.raises(ValueError):
        RelayRequest.from_bytes(json.dumps(payload).encode())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_display не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
