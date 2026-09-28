# Проверки простых значений и сигнатур без вызова пользовательского кода.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-123443
#
# Функции:
# -> require_text(): Проверка текстового значения и размера текста в байтах UTF-8.
# -> require_int(): Проверка целого без неявного принятия bool.
# -> require_number(): Проверка конечного числового значения.
# -> text_tuple(): Защитная копия последовательности строк.
# -> require_callback(): Проверка вызываемости и допустимой сигнатуры.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import inspect
import math

#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка текста без вывода его содержимого в ошибке
#------------------------------------------------------------------------------------------------------------------
def require_text(
    value: object,
    name: str,
    max_bytes: int = 256,
    allow_empty: bool = False,
    ) -> None:

    """Validate a bounded UTF-8 string without disclosing its contents.

    :param value: Candidate string.
    :type value: object

    :param name: Safe field label.
    :type name: str

    :param max_bytes: Maximum encoded size.
    :type max_bytes: int

    :param allow_empty: Whether blank strings are permitted.
    :type allow_empty: bool
    """

    # value - проверяемое значение.
    # name - безопасное имя поля.
    # max_bytes - предельный размер UTF-8.
    # allow_empty - разрешение пустой строки.

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    if not allow_empty and not value.strip():
        raise ValueError(f"{name} must not be blank")

    # Считаем байты, а не символы: кириллица и другие символы занимают разный объём в UTF-8.
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ValueError(f"{name} must contain valid Unicode") from None

    if size > max_bytes:
        raise ValueError(f"{name} exceeds its UTF-8 byte limit")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка целого значения
#------------------------------------------------------------------------------------------------------------------
def require_int(
    value: object,
    name: str,
    minimum: int = 1,
    ) -> None:

    """Validate an integer, excluding bool.

    :param value: Candidate integer.
    :type value: object

    :param name: Safe field label.
    :type name: str

    :param minimum: Inclusive lower bound.
    :type minimum: int
    """

    # value - проверяемое значение.
    # name - имя поля.
    # minimum - допустимый минимум.

    # bool наследуется от int, но True и False не являются допустимыми значениями этого параметра.
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")

    if value < minimum:
        raise ValueError(f"{name} is below its minimum")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка конечного числа
#------------------------------------------------------------------------------------------------------------------
def require_number(
    value: object,
    name: str,
    allow_zero: bool = False,
    ) -> None:

    """Validate a finite positive numeric value, excluding bool.

    :param value: Candidate number.
    :type value: object

    :param name: Safe field label.
    :type name: str

    :param allow_zero: Whether zero is permitted.
    :type allow_zero: bool
    """

    # value - проверяемое значение.
    # name - имя поля.
    # allow_zero - разрешение нуля.

    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a number")

    # NaN, бесконечность и слишком большое целое нельзя использовать как задержку или срок ожидания.
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False

    if not finite or value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if allow_zero else 'positive'}")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Фиксация ограниченного набора строк
#------------------------------------------------------------------------------------------------------------------
def text_tuple(
    value: object,
    name: str,
    ) -> tuple[str, ...]:

    """Copy a list or tuple of unique bounded strings.

    :param value: Candidate finite sequence.
    :type value: object

    :param name: Safe field label.
    :type name: str

    :return: Immutable copy preserving order.
    :rtype: tuple[str, ...]
    """

    # value - список или кортеж строк.
    # name - имя поля.

    # Принимаем только конечные коллекции, чтобы проверка не запускала неизвестный или бесконечный итератор.
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be a list or tuple")

    if len(value) > 128:
        raise ValueError(f"{name} has too many entries")

    for item in value:
        require_text(item, name)

    if len(set(value)) != len(value):
        raise ValueError(f"{name} contains duplicate entries")

    # Кортеж отделяет результат от изменяемого исходного списка и сохраняет порядок элементов.
    return tuple(value)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка аргументов обработчика без его вызова
#------------------------------------------------------------------------------------------------------------------
def require_callback(
    callback: object,
    positional_count: int,
    name: str,
    allow_async: bool = True,
    ) -> None:

    """Check a callable signature without invoking the callable.

    :param callback: Function, partial, bound method or callable object.
    :type callback: object

    :param positional_count: Number of positional arguments supplied by the caller.
    :type positional_count: int

    :param name: Safe field label.
    :type name: str

    :param allow_async: Whether coroutine functions are accepted.
    :type allow_async: bool
    """

    # callback - локальный обработчик или фабрика.
    # positional_count - число передаваемых позиционных аргументов.
    # name - имя поля.
    # allow_async - допустимость асинхронной функции.

    if not callable(callback) or inspect.isclass(callback):
        raise TypeError(f"{name} must be a callback, not a class")

    # У вызываемого объекта проверяем также __call__: он тоже может оказаться генератором или корутиной.
    target = callback if inspect.isroutine(callback) else getattr(callback, "__call__", callback)

    for candidate in (callback, target):
        if inspect.isgeneratorfunction(candidate) or inspect.isasyncgenfunction(candidate):
            raise TypeError(f"{name} must not be a generator")

        if not allow_async and inspect.iscoroutinefunction(candidate):
            raise TypeError(f"{name} must be synchronous")

    # bind проверяет лишь совместимость аргументов, не вызывая сам обработчик.
    # Его действия — остановка загрузки, установка Event и другие изменения состояния — здесь не выполняются.
    try:
        inspect.signature(callback).bind(*([None] * positional_count))
    except (TypeError, ValueError):
        raise ValueError(f"{name} has no compatible inspectable signature; use an explicit wrapper") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch._validation не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
