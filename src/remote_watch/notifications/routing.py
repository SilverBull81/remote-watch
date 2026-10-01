# Выбор получателей уведомления по уровню, меткам и сведениям о приложении.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Классы:
# -> PolicyRouter: Выбор получателей по настроенным правилам.
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта без запуска фоновой работы.
#    Интерфейс:
#    -> select(): Применение условий и исключение повторных получателей.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from remote_watch.config import Route
from remote_watch.events import Notification

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Выбор получателей по настроенным правилам
#------------------------------------------------------------------------------------------------------------------
class PolicyRouter:
    """Select unique destinations in rule order without performing delivery."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        routes: tuple[Route, ...],
    ) -> None:

        """Copy validated routing rules.

        :param routes: Ordered routing rules.
        :type routes: tuple[Route, ...]
        """

        # routes - правила, проверенные при создании настроек приложения.

        if not isinstance(routes, (list, tuple)) or any(not isinstance(route, Route) for route in routes):
            raise TypeError("routes must contain Route values")

        self._routes = tuple(routes)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Применение условий и исключение повторных получателей
    #--------------------------------------------------------------------------------------------------------------
    def select(
        self,
        notification: Notification,
    ) -> tuple[str, ...]:

        """Apply AND within each rule and union across matching rules.

        :param notification: Validated notification with trusted identity.
        :type notification: Notification

        :return: Unique destination identifiers in first-match order.
        :rtype: tuple[str, ...]
        """

        # notification - подготовленные данные уведомления; исходный LogRecord здесь не нужен.

        if not isinstance(notification, Notification):
            raise TypeError("notification must be Notification")

        if notification.notify is False:
            return ()

        selected: dict[str, None] = {}

        for route in self._routes:
            # Явное notify=True снимает только порог важности, остальные условия остаются обязательными.
            if notification.notify is not True and notification.level_no < route.min_level:
                continue

            if route.topic is not None and route.topic != notification.topic:
                continue

            if not set(route.required_tags).issubset(notification.tags):
                continue

            if any(
                getattr(route, name) is not None
                and getattr(route, name) != getattr(notification.identity, name)
                for name in ("service", "environment", "region", "host", "instance_id")
            ):
                continue

            # Словарь сохраняет порядок и убирает повторения одного получателя в разных правилах.
            selected.update(dict.fromkeys(route.destination_ids))

        return tuple(selected)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.notifications.routing не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
