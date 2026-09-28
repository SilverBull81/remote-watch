# Проверки условий выбора получателей и объединения подходящих правил.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-131628
#
# Тесты:
# -> test_matching_rules(): Объединение правил и проверка обязательных условий.
# -> test_identity_conditions(): Условия по каждому полю сведений о приложении.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from dataclasses import replace

import pytest

from remote_watch import Notification, PolicyRouter, Route

#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Объединение правил и проверка обязательных условий
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, ("one", "two")),
        ({"notify": False}, ()),
        ({"level_no": 20}, ()),
        ({"level_no": 20, "notify": True}, ("one", "two")),
        ({"topic": "other", "notify": True}, ()),
        ({"tags": ("other",), "notify": True}, ()),
    ],
)
def test_matching_rules(
    notification: Notification,
    changes: dict[str, object],
    expected: tuple[str, ...],
    ) -> None:

    """Combine conditions and deduplicate matching destination identifiers.

    :param notification: Valid event fixture.
    :type notification: Notification

    :param changes: Event field overrides.
    :type changes: dict[str, object]

    :param expected: Selected destination identifiers.
    :type expected: tuple[str, ...]
    """

    # notification, changes, expected - исходные данные, условия сценария и ожидаемые получатели.

    rule = Route(destination_ids=("one", "two"), topic="quotes", required_tags=("important",))
    router = PolicyRouter((rule, rule, replace(rule, destination_ids=("two",), min_level=100)))
    event = replace(notification, topic="quotes", tags=("important", "extra"))
    assert router.select(replace(event, **changes)) == expected
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Условия по каждому полю сведений о приложении
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["service", "environment", "region", "host", "instance_id"])
def test_identity_conditions(
    notification: Notification,
    name: str,
    ) -> None:

    """Keep identity conditions mandatory even with notify=True.

    :param notification: Valid event fixture.
    :type notification: Notification

    :param name: Identity field under test.
    :type name: str
    """

    # notification, name - уведомление и проверяемое поле сведений о приложении.

    matching = Route(destination_ids=("one",), **{name: getattr(notification.identity, name)})
    wrong = Route(destination_ids=("two",), **{name: "different"})
    assert PolicyRouter((matching, wrong)).select(replace(notification, notify=True)) == ("one",)
    assert PolicyRouter(()).select(notification) == ()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль tests/unit/test_routing.py не предназначен для прямого запуска. Используйте pytest.',
    )
#------------------------------------------------------------------------------------------------------------------
