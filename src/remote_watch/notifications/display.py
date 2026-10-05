# Общая проверка режима отображения для клиента, протокола и провайдера.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-221259
#
# Функции:
# -> validate_display(): Проверка режима и выбранных групп полей отображения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка режима и выбранных групп полей отображения
#------------------------------------------------------------------------------------------------------------------
def validate_display(
    mode: str,
    fields: tuple[str, ...] | None,
) -> tuple[str, ...] | None:

    """Validate a presentation policy without permitting removal of compact source identity.

    :param mode: Requested display mode.
    :type mode: str

    :param fields: Optional field groups, normalized to an immutable tuple.
    :type fields: tuple[str, ...] | None

    :return: Validated field selection, or None for mode defaults.
    :rtype: tuple[str, ...] | None
    """

    # mode/fields — фиксированные имена, без пользовательского форматирующего кода.
    if mode not in ("full", "compact", "text"):
        raise ValueError("invalid display mode")
    if fields is None:
        return None
    if not isinstance(fields, (tuple, list)) or len(fields) > 5:
        raise ValueError("invalid display fields")
    fields = tuple(fields)
    if (any(type(field) is not str or field not in {"identity", "level", "logger", "time", "ids"}
            for field in fields) or len(set(fields)) != len(fields)):
        raise ValueError("invalid display fields")
    if mode == "text" or mode == "compact" and "identity" not in fields:
        raise ValueError("display fields conflict with mode")
    return fields
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль display не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
