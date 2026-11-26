# Безопасные координаты ошибок в локальных JSON-конфигах.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261126-232526
#
# Функции:
# -> json_syntax_reason(): Выбор фиксированной причины по ошибке стандартного парсера.
#
# -> json_syntax_location(): Координаты парсера и фактической лишней запятой.
#
# -> json_location(): Координаты известного поля или неизвестных ключей по их позициям.
#
# -> _entries(): Позиции непосредственных элементов объекта или списка.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import json
import re
from collections.abc import Iterator

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

JSON_HINTS = {
    "escape": "Недопустимая escape-последовательность. В JSON-пути используйте / либо \\\\.",
    "trailing_comma": "Лишняя запятая перед ] или }. Уберите запятую после последнего элемента.",
    "delimiter": "Ожидается запятая или разделитель между элементами JSON.",
    "property": "Ключ объекта должен быть в двойных кавычках; уберите запятую перед }.",
    "value": "Ожидается значение JSON; проверьте кавычки, запятые и отсутствие комментариев.",
    "extra": "После завершённого JSON есть лишние данные; в файле нужен один объект.",
    "control": "В JSON-строке есть неэкранированный управляющий символ.",
    "encoding": "Файл должен быть сохранён в UTF-8; кодировка UTF-16 не поддерживается.",
    "duplicate": "В JSON повторяется ключ. У каждого объекта должны быть уникальные ключи.",
    "constant": "NaN и Infinity не допускаются в JSON-настройках.",
    "object": "На верхнем уровне файла нужен один JSON-объект {...}.",
}
_SPACE = re.compile(r"[ \t\r\n]*")
_PATH_ITEM = re.compile(r"([a-z_]+)|\[([0-9]+)\]")

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Выбор фиксированной причины по ошибке стандартного парсера
#------------------------------------------------------------------------------------------------------------------
def json_syntax_reason(
    error: json.JSONDecodeError,
    ) -> str:

    """
    Classify syntax errors using fixed decoder messages without returning input text.

    :param error: Error from the standard decoder.
    :type error: json.JSONDecodeError

    :return: Fixed explanation code.
    :rtype: str
    """

    # error — строка с секретом может находиться в doc; смотрим только разделители.

    if error.doc[error.pos:error.pos + 1] in ("}", "]") and error.doc[:error.pos].rstrip().endswith(","):
        return "trailing_comma"

    reasons = {"Invalid \\escape": "escape", "Invalid \\uXXXX escape": "escape",
               "Expecting ',' delimiter": "delimiter", "Expecting ':' delimiter": "delimiter",
               "Expecting property name enclosed in double quotes": "property",
               "Expecting value": "value", "Extra data": "extra", "Invalid control character at": "control"}
    return reasons.get(error.msg, "syntax")
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Координаты парсера и фактической лишней запятой
#------------------------------------------------------------------------------------------------------------------
def json_syntax_location(
    error: json.JSONDecodeError,
    reason: str,
    ) -> str:

    """
    Locate a syntax error and identify the earlier comma when the decoder points at a closing bracket.

    :param error: Original standard decoder failure.
    :type error: json.JSONDecodeError

    :param reason: Fixed syntax category.
    :type reason: str

    :return: Coordinate suffix without JSON text.
    :rtype: str
    """

    # error — координаты парсера могут указывать на скобку, а не на причину отказа.
    # reason — категория из json_syntax_reason, без данных конфигурации.

    location = f" Строка {error.lineno}, столбец {error.colno}."
    if reason == "trailing_comma":
        comma = len(error.doc[:error.pos].rstrip()) - 1
        line = error.doc.count("\n", 0, comma) + 1
        column = comma - error.doc.rfind("\n", 0, comma)
        location += f" Запятая находится на строке {line}, столбец {column}."
    return location
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Координаты известного поля или неизвестных ключей по их позициям
#------------------------------------------------------------------------------------------------------------------
def json_location(
    document: str,
    field: str,
    *,
    entries: tuple[int, ...] = (),
    ) -> str:

    """
    Find a schema path in an already validated JSON document, without retaining or printing keys.

    :param document: Exact decoded text that was used for validation, or an empty string.
    :type document: str

    :param field: Sanitized dotted path with optional zero-based entry indices.
    :type field: str

    :param entries: Positions of unknown keys in the located object, capped at eight.
    :type entries: tuple[int, ...]

    :return: Safe hint suffix with line and column numbers, or an empty string.
    :rtype: str
    """

    # document — повторно файл не читаем, чтобы координаты относились к проверенной версии.
    # field — известные имена из схемы; для отсутствующего ключа указываем его объект.
    # entries — позиции неизвестных ключей; их текст может содержать секрет.

    if not document:
        return ""

    position = value_position = _SPACE.match(document).end()
    path = field.split(".")
    if path[0] in {"config", "client"}:
        path.pop(0)
    if path == ["schema"]:
        path = ["schema_version"]

    try:
        for part in _PATH_ITEM.finditer(".".join(path)):
            key, index = part.groups()
            for number, (name, start, child) in enumerate(_entries(document, value_position)):
                if (key is not None and name == key) or (index is not None and number == int(index)):
                    position, value_position = start, child
                    break
            else:
                # Отсутствующее поле нельзя указать точнее, чем начало его объекта.
                break

        positions = []
        if entries:
            selected = set(entries[:8])
            for index, (_, start, _) in enumerate(_entries(document, value_position)):
                if index in selected:
                    positions.append(start)
                    selected.remove(index)
                    if not selected:
                        break
    except (ValueError, RecursionError, IndexError):
        return ""

    return "".join(f" Строка {document.count(chr(10), 0, pos) + 1}, "
                   f"столбец {pos - document.rfind(chr(10), 0, pos)}." for pos in positions or [position])
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Позиции непосредственных элементов объекта или списка
#------------------------------------------------------------------------------------------------------------------
def _entries(
    document: str,
    position: int,
    ) -> Iterator[tuple[str | None, int, int]]:

    """
    Walk one container using the standard decoder to skip complete JSON values.

    :param document: Previously accepted JSON text.
    :type document: str

    :param position: Offset of the container token.
    :type position: int

    :return: Key or None for a list element, key or element offset, and value offset.
    :rtype: Iterator[tuple[str | None, int, int]]
    """

    # document — вложенные объекты и экранированные строки сканирует штатный decoder.
    # position — начало контейнера, найденного на предыдущем шаге.

    opening = document[position]
    if opening not in "{[":
        return

    decoder = json.JSONDecoder()
    position = _SPACE.match(document, position + 1).end()
    while document[position] not in "}]":
        start = position
        name = None
        if opening == "{":
            name, position = decoder.raw_decode(document, position)
            colon = _SPACE.match(document, position).end()
            position = _SPACE.match(document, colon + 1).end()

        yield name, start, position
        _, end = decoder.raw_decode(document, position)
        position = _SPACE.match(document, end).end()
        if document[position] == ",":
            position = _SPACE.match(document, position + 1).end()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch._config_diagnostics не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
