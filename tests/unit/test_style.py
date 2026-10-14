# Проверка защиты единых правил оформления от повторных расхождений.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Тесты:
# -> test_style_regressions(): Нарушения согласованных правил обнаруживаются без исполнения файла
#
# -> test_package_guard(): Пакет не содержит служебного запуска


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

import runpy
from pathlib import Path

import pytest

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Нарушения согласованных правил обнаруживаются без исполнения файла
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change, expected", [
    ("signature", "signature_closing_indent"),
    ("single_argument", "signature_columns"),
    ("docstring", "docstring_opening_line"),
    ("class_docstring", "docstring_opening_line"),
    ("class_docstring_spacing", "class_docstring_spacing"),
    ("fields", "dataclass_field_documentation:config"),
    ("sections", "missing_section:КЛАССЫ"),
    ("inventory", "missing_inventory:ServiceSpec"),
    ("method_group", "method_inventory_group:arguments"),
    ("section_spacing", "section_spacing"),
    ("block_spacing", "opening_block_spacing"),
    ("parameter_spacing", "docstring_parameter_spacing"),
    ("closing_order", "closing_divider_order"),
    ("internal_heading", "internal_function_heading"),
])
def test_style_regressions(
    tmp_path: Path,
    change: str,
    expected: str,
    ) -> None:

    """
    Detect representative former style gaps by mutating a valid source copy.

    :param tmp_path: Isolated source directory without production settings.
    :type tmp_path: pathlib.Path

    :param change: Deliberate formatting regression in the copied source.
    :type change: str

    :param expected: Fixed checker diagnostic that must identify the regression.
    :type expected: str
    """

    # tmp_path — изменяется только временная копия, без импорта проверяемого модуля.
    # change — нарушение одного из правил, выбранных владельцем для обоих проектов.
    # expected — конкретный код; другие замечания не подменяют проверяемый отказ.

    root = Path(__file__).resolve().parents[2]
    check = runpy.run_path(str(root / "tools/check_style.py"))["_check_file"]
    source = root / "src/remote_watch/gateway_server/service.py"
    if change == "internal_heading":
        source = root / "tools/check_style.py"
    assert not check(source)
    text = source.read_text(encoding="utf-8-sig")

    # Каждая подмена оставляет файл синтаксически корректным. Проверка не должна
    # полагаться на запуск службы, Windows API или доступность её зависимостей.
    if change == "signature":
        text = text.replace("    ) -> int:", ") -> int:", 1)
    elif change == "single_argument":
        text = text.replace("    def __post_init__(\n        self,\n        ) -> None:",
                            "    def __post_init__(self) -> None:", 1)
    elif change == "docstring":
        text = text.replace('        """\n        Build ', '        """Build ', 1)
    elif change == "class_docstring":
        text = text.replace('    """\n    Bind ', '    """Bind ', 1)
    elif change == "class_docstring_spacing":
        text = text.replace('class ServiceSpec:\n', 'class ServiceSpec:\n\n', 1)
    elif change == "fields":
        text = text.replace(":param config:", ":parameter config:", 1)
    elif change == "sections":
        text = text.replace("# КЛАССЫ", "# ГРУППА", 1)
    elif change == "inventory":
        text = text.replace("# -> ServiceSpec:", "# -> MissingSpec:", 1)
    elif change == "method_group":
        text = text.replace("#    Интерфейс:", "#    Служебные методы:", 1)
    elif change == "section_spacing":
        marker = "\n\n\n#" + "*" * 114 + "\n# КЛАССЫ"
        text = text.replace(marker, marker[1:], 1)
    elif change == "block_spacing":
        marker = "\n\n    #" + "-" * 110 + "\n    # ИНТЕРФЕЙС"
        text = text.replace(marker, "\n" + marker, 1)
    elif change == "parameter_spacing":
        text = text.replace(":type action: str\n\n", ":type action: str\n", 1)
    elif change == "closing_order":
        marker = "    #" + "-" * 110 + "\n#" + "-" * 114
        text = text.replace(marker, "#" + "-" * 114 + "\n    #" + "-" * 110, 1)
    elif change == "internal_heading":
        text = text.replace("# СЛУЖЕБНАЯ ФУНКЦИЯ :", "# ФУНКЦИЯ :", 1)

    assert text != source.read_text(encoding="utf-8-sig"), change
    candidate = tmp_path / "candidate.py"
    candidate.write_text(text, encoding="utf-8-sig", newline="\r\n")
    assert expected in {rule for _, rule in check(candidate)}
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Пакет не содержит служебного запуска
#------------------------------------------------------------------------------------------------------------------
def test_package_guard(
    tmp_path: Path,
    ) -> None:

    """
    Reject an executable package guard while accepting the original exports.

    :param tmp_path: Isolated directory for an artificial package initializer.
    :type tmp_path: pathlib.Path
    """

    # tmp_path — настоящий __init__.py библиотеки остаётся неизменным.

    root = Path(__file__).resolve().parents[2]
    check = runpy.run_path(str(root / "tools/check_style.py"))["_check_file"]
    source = root / "src/remote_watch/__init__.py"
    assert not check(source)
    candidate = tmp_path / "__init__.py"
    candidate.write_text(source.read_text(encoding="utf-8-sig") +
                         '\nif __name__ == "__main__":\n    print("unexpected")\n',
                         encoding="utf-8-sig", newline="\r\n")
    assert "package_main_guard" in {rule for _, rule in check(candidate)}
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Тестовый модуль не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
