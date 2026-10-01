# Подготовка независимых данных уведомления и ограничение размера текста.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Функции:
# -> truncate_text(): Усечение текста по размеру UTF-8 с видимым маркером.
# -> prepare_notification(): Подготовка текста и метаданных без изменения исходной записи.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from remote_watch._validation import require_text, text_tuple
from remote_watch.events import Identity, Notification, SnapshotLimits

#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Усечение текста по размеру UTF-8 с видимым маркером
#------------------------------------------------------------------------------------------------------------------
def truncate_text(
    text: str,
    limit: int,
) -> tuple[str, bool]:

    """Truncate UTF-8 text with a visible ASCII marker.

    :param text: Rendered and redacted text.
    :type text: str

    :param limit: Maximum encoded size in bytes.
    :type limit: int

    :return: Bounded text and a truncation flag.
    :rtype: tuple[str, bool]
    """

    # text - готовый текст после удаления чувствительных данных.
    # limit - допустимый размер поля в байтах UTF-8.

    encoded = text.encode("utf-8")

    if len(encoded) <= limit:
        return text, False

    # Даже при очень маленьком лимите остаётся видимый маркер. Неполный символ UTF-8 отбрасываем.
    marker = "..."[:limit]
    prefix = encoded[:limit - len(marker)].decode("utf-8", errors="ignore")
    return prefix + marker, True
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка текста и метаданных без изменения исходной записи
#------------------------------------------------------------------------------------------------------------------
def prepare_notification(
    record: logging.LogRecord,
    *,
    identity: Identity,
    session_id: str,
    limits: SnapshotLimits,
    ttl: float,
    now: datetime,
    formatter: logging.Formatter,
    redactor: Callable[[str], str] | None,
) -> Notification:

    """Render a detached notification without mutating the input record.

    :param record: Source logging record.
    :type record: logging.LogRecord

    :param identity: Runtime-owned application identity.
    :type identity: Identity

    :param session_id: Runtime-owned session identifier.
    :type session_id: str

    :param limits: Local byte limits.
    :type limits: SnapshotLimits

    :param ttl: Maximum lifetime in seconds.
    :type ttl: float

    :param now: Aware admission timestamp supplied by the runtime clock.
    :type now: datetime

    :param formatter: Message formatter; exception text is stored separately.
    :type formatter: logging.Formatter

    :param redactor: Optional synchronous text redactor.
    :type redactor: Callable[[str], str] | None

    :return: Validated notification holding no record or traceback references.
    :rtype: Notification
    """

    # record - исходная запись, которую также читают локальные обработчики logging.
    # identity, session_id - сведения runtime; одноимённые поля extra их не заменяют.
    # limits, ttl, now - ограничения размера и срока отправки.
    # formatter, redactor - синхронная подготовка текста до помещения в очередь.

    # Сначала проверяем метаданные. Ошибочный тип не должен расширять условия отправки.
    notify = getattr(record, "notify", None)

    if notify is not None and type(notify) is not bool:
        raise TypeError("notify must be bool or None")

    metadata = {name: getattr(record, name, None) for name in ("topic", "correlation_id", "trace_id")}

    for name, value in metadata.items():
        if value is not None:
            require_text(value, name)

    tags = text_tuple(getattr(record, "tags", ()), "tags")

    # Formatter записывает message, asctime и exc_text в LogRecord. Даём ему только отдельную копию.
    # Текст исключения формируем отдельно, чтобы он не дублировался внутри сообщения.
    copied = copy.copy(record)
    copied.exc_info = None
    copied.exc_text = None
    copied.stack_info = None
    message = formatter.format(copied)
    exception = formatter.formatException(record.exc_info) if record.exc_info else record.exc_text

    if record.stack_info:
        exception = "\n".join(part for part in (exception, record.stack_info) if part)

    # Пользовательский редактор работает до усечения и очереди. Ошибка редактора отменяет уведомление.
    if redactor is not None:
        message = redactor(message)

        if exception is not None:
            exception = redactor(exception)

    if not isinstance(message, str) or (exception is not None and not isinstance(exception, str)):
        raise TypeError("rendered notification fields must be strings")

    message, shortened = truncate_text(message, limits.message_max_bytes)
    truncated_fields = ["message"] if shortened else []

    if exception is not None:
        exception, shortened = truncate_text(exception, limits.exception_max_bytes)

        if shortened:
            truncated_fields.append("exception")

    # Конструктор проверяет полный JSON и общий размер метаданных. Идентификаторы не сокращаем.
    return Notification(
        event_id=uuid4().hex,
        session_id=session_id,
        identity=identity,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
        level_no=record.levelno,
        level_name=record.levelname,
        logger_name=record.name,
        message=message,
        exception=exception,
        tags=tags,
        notify=notify,
        truncated_fields=tuple(truncated_fields),
        limits=limits,
        **metadata,
    )
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.notifications.normalization не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
