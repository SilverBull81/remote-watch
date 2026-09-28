# Проверки неизменяемости, сериализации и границ размера уведомлений.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-121352
#
# Тесты:
#
# -> test_snapshot_is_detached(): Защита от изменения исходных и сериализованных данных.
#
# -> test_json_round_trip(): Полный round-trip и нормализация UTC.
#
# -> test_payload_rejects_invalid_schema(): Версия и неизвестные поля.
#
# -> test_snapshot_rejects_invalid_fields(): Типы, даты и метаданные.
#
# -> test_utf8_and_serialized_limits(): Лимиты байтов и JSON escaping.
#
# -> test_identity_errors_do_not_echo_values(): Безопасное сообщение ошибки.
#
# -> test_invalid_limits(): Невалидные лимиты.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from remote_watch import Identity, Notification, SnapshotLimits

#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Снимок не меняется через исходный список или сериализованные данные
#------------------------------------------------------------------------------------------------------------------
def test_snapshot_is_detached(
    notification: Notification,
    ) -> None:

    """Keep notification data immutable across caller and payload mutations.

    :param notification: Valid snapshot fixture.
    :type notification: Notification
    """

    # notification - исходный снимок.

    tags = ["ops"]
    snapshot = replace(notification, tags=tags)
    tags.append("changed")
    payload = snapshot.to_dict()
    payload["tags"].append("wire-change")
    payload["identity"]["service"] = "changed"
    assert snapshot.tags == ("ops",)
    assert snapshot.identity.service == "quotes"

    with pytest.raises(FrozenInstanceError):
        snapshot.message = "changed"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : JSON round-trip без потери временной зоны и кириллицы
#------------------------------------------------------------------------------------------------------------------
def test_json_round_trip(
    notification: Notification,
    ) -> None:

    """Round-trip complete optional metadata and normalize timestamps to UTC.

    :param notification: Valid snapshot fixture.
    :type notification: Notification
    """

    # notification - исходный снимок.

    moscow = timezone(timedelta(hours=3))
    original = replace(
        notification, created_at=notification.created_at.astimezone(moscow),
        exception="Ошибка соединения", topic="operations", tags=("ops",),
        correlation_id="request-1", trace_id="trace-1", notify=True, truncated_fields=("exception",),
    )
    payload = json.loads(json.dumps(original.to_dict(), ensure_ascii=False))
    restored = Notification.from_dict(payload)
    assert restored == original
    assert restored.created_at.tzinfo is timezone.utc
    assert "limits" not in payload
    assert "Источник" not in repr(restored)
    assert "Ошибка" not in repr(restored)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибочная версия или неизвестные поля не принимаются
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"schema_version": True}, {"schema_version": "1"},
    {"limits": {}}, {"unknown": "value"}, {"identity": {"service": "incomplete"}},
    {"created_at": "not-a-date"}, {"created_at": "2026-09-28T10:00:00"},
])
def test_payload_rejects_invalid_schema(
    notification: Notification,
    changes: dict[str, object],
    ) -> None:

    """Reject untrusted schema extensions and malformed payload fields.

    :param notification: Valid snapshot fixture.
    :type notification: Notification

    :param changes: Invalid payload modifications.
    :type changes: dict[str, object]
    """

    # notification - исходный снимок.
    # changes - недопустимые поля входящего payload.

    with pytest.raises(ValueError):
        Notification.from_dict(notification.to_dict() | changes)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Типы и временные границы проверяются при создании
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("changes", [
    {"level_no": True}, {"notify": 1}, {"tags": "ops"}, {"tags": ["ops", "ops"]},
    {"message": object()}, {"identity": {}}, {"topic": " "}, {"message": "\ud800"},
    {"truncated_fields": ("identity",)}, {"created_at": datetime(2026, 9, 28)},
    {"expires_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
])
def test_snapshot_rejects_invalid_fields(
    notification: Notification,
    changes: dict[str, object],
    ) -> None:

    """Reject mutable objects, invalid Unicode and ambiguous metadata.

    :param notification: Valid snapshot fixture.
    :type notification: Notification

    :param changes: Invalid constructor modifications.
    :type changes: dict[str, object]
    """

    # notification - исходный снимок.
    # changes - недопустимые аргументы конструктора.

    with pytest.raises((TypeError, ValueError)):
        replace(notification, **changes)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Размер ограничен в байтах, включая сериализованное представление
#------------------------------------------------------------------------------------------------------------------
def test_utf8_and_serialized_limits(
    notification: Notification,
    ) -> None:

    """Enforce text, metadata and full-payload byte limits independently.

    :param notification: Valid snapshot fixture.
    :type notification: Notification
    """

    # notification - исходный снимок.

    with pytest.raises(ValueError, match="message"):
        replace(notification, message="я" * 4097)

    with pytest.raises(ValueError, match="metadata"):
        replace(notification, tags=tuple(f"tag-{index}" + "x" * 200 for index in range(20)))

    limits = SnapshotLimits(event_max_bytes=1024, message_max_bytes=500,
                            exception_max_bytes=100, metadata_max_bytes=900)

    with pytest.raises(ValueError, match="event_max_bytes"):
        replace(notification, message="\0" * 400, limits=limits)

    payload = notification.to_dict()
    del payload["event_id"]

    with pytest.raises(ValueError, match="invalid notification"):
        Notification.from_dict(payload)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибка не раскрывает неправильное входное значение
#------------------------------------------------------------------------------------------------------------------
def test_identity_errors_do_not_echo_values() -> None:

    """Avoid echoing a supplied value in validation diagnostics."""

    value = "sensitive-test-marker" * 30

    with pytest.raises(ValueError) as error:
        Identity(service=value, environment="test", region="test", host="test", instance_id="one")

    assert "sensitive-test-marker" not in str(error.value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Нельзя задать некорректные размеры снимка
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("changes", [{"message_max_bytes": True}, {"event_max_bytes": 0},
                                     {"message_max_bytes": 20000}])
def test_invalid_limits(
    changes: dict[str, object],
    ) -> None:

    """Reject noninteger, nonpositive and contradictory size budgets.

    :param changes: Invalid limit fields.
    :type changes: dict[str, object]
    """

    # changes - недопустимые лимиты.

    with pytest.raises((TypeError, ValueError)):
        SnapshotLimits(**changes)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль tests/unit/test_events.py не предназначен для прямого запуска. Используйте pytest.',
    )
#------------------------------------------------------------------------------------------------------------------
