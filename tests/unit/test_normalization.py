# Проверки независимости уведомления от LogRecord и усечения текста UTF-8.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-131628
#
# Тесты:
# -> test_detached_record(): Независимость уведомления от изменяемых данных и кеша Formatter.
# -> test_utf8_truncation(): Усечение без повреждения многобайтного символа.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
import logging
import sys
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from remote_watch import Identity, SnapshotLimits
from remote_watch.normalization import prepare_notification, truncate_text

#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимость уведомления от изменяемых данных и кеша Formatter
#------------------------------------------------------------------------------------------------------------------
def test_detached_record(
    identity: Identity,
    ) -> None:

    """Render mutable arguments and exception data without modifying the original record.

    :param identity: Runtime identity fixture.
    :type identity: Identity
    """

    # identity - доверенные сведения о приложении, не зависящие от extra.

    mutable = ["before"]

    try:
        raise ValueError("test exception")
    except ValueError:
        record = logging.LogRecord("app", 40, "", 0, "%s", (mutable,), sys.exc_info())

    record.tags = ["tag"]
    record.identity = replace(identity, instance_id="forged")
    record.session_id = "forged"
    before = record.__dict__.copy()
    event = prepare_notification(
        record, identity=identity, session_id="trusted", limits=SnapshotLimits(), ttl=5,
        now=datetime(2026, 9, 28, tzinfo=timezone.utc), formatter=logging.Formatter("%(asctime)s %(message)s"),
        redactor=None,
    )

    # Formatter не должен записать свой кеш в оригинал; последующая правка args/tags не меняет уведомление.
    assert record.__dict__ == before
    mutable.append("after")
    record.tags.append("later")
    assert "before" in event.message and "after" not in event.message
    assert "ValueError: test exception" in event.exception
    assert event.tags == ("tag",)
    assert event.identity is identity and event.session_id == "trusted"
    assert "exc_info" not in event.to_dict()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Усечение без повреждения многобайтного символа
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5, 6, 17])
def test_utf8_truncation(
    limit: int,
    ) -> None:

    """Keep a visible marker without splitting a multibyte character.

    :param limit: Small byte budget.
    :type limit: int
    """

    # limit - малый лимит, в том числе короче полного маркера усечения.

    text, shortened = truncate_text("Пример🙂" * 10, limit)
    assert shortened
    assert len(text.encode("utf-8")) <= limit
    assert text.endswith("..."[:limit])
    assert "\ufffd" not in text
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль tests/unit/test_normalization.py не предназначен для прямого запуска. Используйте pytest.',
    )
#------------------------------------------------------------------------------------------------------------------
