# Подключение стандартного logging к очереди уведомлений без сетевых операций.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Классы:
# -> NotificationHandler: Обработчик logging для очереди уведомлений.
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта без запуска фоновой работы.
#    Интерфейс:
#    -> emit(): Подготовка уведомления и попытка помещения в очередь.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from remote_watch._validation import require_callback
from remote_watch.notifications._context import delivery_context
from remote_watch.notifications.normalization import prepare_notification

if TYPE_CHECKING:
    from remote_watch.notifications.runtime import NotificationRuntime


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Обработчик logging для очереди уведомлений
#------------------------------------------------------------------------------------------------------------------
class NotificationHandler(logging.Handler):
    """Copy records into a runtime without network I/O or queue-space waits."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        runtime: NotificationRuntime,
        *,
        redactor: Callable[[str], str] | None = None,
        ) -> None:

        """Bind one runtime and an optional text redactor.

        :param runtime: Runtime owning identity, queues and lifecycle.
        :type runtime: NotificationRuntime

        :param redactor: Synchronous message/exception redactor.
        :type redactor: Callable[[str], str] | None
        """

        # runtime - владелец очереди; обработчик не запускает и не останавливает его автоматически.
        # redactor - функция удаления секретов из текста перед отправкой.

        if redactor is not None:
            require_callback(redactor, 1, "redactor", allow_async=False)

        super().__init__(level=logging.NOTSET)
        self._runtime = runtime
        self._redactor = redactor
        self._default_formatter = logging.Formatter()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка уведомления и попытка помещения в очередь
    #--------------------------------------------------------------------------------------------------------------
    def emit(
        self,
        record: logging.LogRecord,
        ) -> None:

        """Normalize and submit a record, containing failures inside the handler.

        :param record: Record already accepted by standard logging filters.
        :type record: logging.LogRecord
        """

        # record - запись после стандартных уровней и фильтров logging.

        # Контекст запрещает повторную отправку и при логировании из formatter/redactor.
        # Отдельно исключаем внутреннее пространство имён даже вне рабочего потока.
        internal_name = isinstance(record.name, str) and (
            record.name == "remote_watch.internal" or record.name.startswith("remote_watch.internal.")
        )

        if delivery_context.get() or internal_name:
            self._runtime._count("suppressed")
            return

        if getattr(record, "notify", None) is False:
            self._runtime._count("suppressed")
            return

        if not self._runtime._accepting():
            self._runtime._count("not_running")
            return

        token = delivery_context.set(True)

        try:
            received_at = self._runtime._delivery_now()
            notification = prepare_notification(
                record,
                identity=self._runtime.config.identity,
                session_id=self._runtime.session_id,
                limits=self._runtime.config.runtime.snapshot_limits,
                ttl=self._runtime.notification_ttl,
                now=self._runtime._utc_now(),
                formatter=self.formatter or self._default_formatter,
                redactor=self._redactor,
            )
            self._runtime._submit(notification, received_at)
        except Exception:
            # Не используем handleError: он может напечатать исходный текст вместе с секретом.
            # Безопасный счётчик доступен приложению и не порождает новых записей logging.
            self._runtime._count("normalization_failed")
        finally:
            delivery_context.reset(token)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.notifications.handler не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
