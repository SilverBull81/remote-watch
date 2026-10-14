# Проверка формальных правил оформления Python-файлов проекта без изменения исходников.
#
# Version 1.0.6
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Функции:
# -> main(): Проверка всех поддерживаемых Python-файлов репозитория
#
# -> _check_file(): Общие правила оформления одного модуля
#
# -> _check_function(): Проверка сигнатуры, docstring и пояснений функции
#
# -> _check_class(): Проверка полей dataclass и порядка методов класса
#
# -> _check_sections(): Проверка секций по реальному содержимому модуля
#
# -> _check_opening(): Открывающие границы классов, функций и вложенных методов


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import ast
import re
from pathlib import Path

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка всех поддерживаемых Python-файлов репозитория
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """
    Check repository Python formatting without importing application modules.

    :return: Zero for clean sources, otherwise one.
    :rtype: int
    """

    root = Path(__file__).resolve().parents[1]
    folders = ("src", "tests", "tools")
    files = sorted(path for folder in folders for path in (root / folder).rglob("*.py"))
    issues = []

    # Не читаем build, окружения или локальные credentials. Проверяем исходный код,
    # включая новые ещё не добавленные в Git файлы; импортировать его не требуется.
    for path in files:
        for line, rule in _check_file(path):
            issues.append(f"{path.relative_to(root).as_posix()}:{line}: {rule}")

    for issue in issues:
        print(issue)
    print(f"Style: {len(files)} files, {len(issues)} issues")
    return int(bool(issues))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Общие правила оформления одного модуля
#------------------------------------------------------------------------------------------------------------------
def _check_file(
    path: Path,
    ) -> list[tuple[int, str]]:

    """
    Check physical file format and delegate Python declaration checks.

    :param path: Source file within the repository code directories.
    :type path: Path

    :return: Source line numbers paired with fixed rule identifiers.
    :rtype: list[tuple[int, str]]
    """

    # path — проверяемый файл; содержимое не исполняется.

    issues = []
    data = path.read_bytes()
    if not data.startswith(b"\xef\xbb\xbf"):
        issues.append((1, "utf8_bom"))
    if b"\n" in data.replace(b"\r\n", b"") or b"\r" in data.replace(b"\r\n", b""):
        issues.append((1, "crlf"))

    try:
        source = data.decode("utf-8-sig")
        tree = ast.parse(source, feature_version=(3, 10))
    except (UnicodeError, SyntaxError):
        return issues + [(1, "utf8_python310_syntax")]

    lines = source.splitlines()
    header = source.split("#***", 1)[0]
    if not re.search(r"# Version [1-9]\d*\.\d\.\d\b", header):
        issues.append((1, "file_version"))
    if not re.search(r"Дата и время последнего изменения: \d{6}-\d{6}\b", header):
        issues.append((1, "timestamp"))
    has_guard = any(isinstance(node, ast.If) and "__name__" in ast.unparse(node.test) for node in tree.body)
    if path.name == "__init__.py" and has_guard:
        issues.append((len(lines), "package_main_guard"))
    elif path.name != "__init__.py" and not has_guard:
        issues.append((len(lines), "main_guard"))

    if "tests" in path.parts and path.name not in ("conftest.py", "__init__.py"):
        if "# Тесты:" not in header:
            issues.append((1, "test_inventory"))
        if "pytest" not in source[source.rfind("if __name__"):]:
            issues.append((len(lines), "test_guard_pytest"))

    for number, line in enumerate(lines, 1):
        if len(line) > 115:
            issues.append((number, "line_length"))
        if line.rstrip() != line:
            issues.append((number, "trailing_whitespace"))

    issues.extend(_check_sections(tree, lines, header))

    # AST отделяет объявления от строковых примеров и комментариев. Проверка
    # подтверждает наличие пояснений, но не оценивает качество русского текста.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            issues.extend(_check_function(node, lines))
        elif isinstance(node, ast.ClassDef):
            issues.extend(_check_class(node, lines))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            issues.extend(_check_opening(node, lines))

    # Несколько вложенных объявлений могут заканчиваться одной строкой AST.
    # В таком случае сначала закрывается метод, затем содержащий его класс.
    endings: dict[int, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            endings.setdefault(node.end_lineno, []).append(node.col_offset)
    for end, indents in endings.items():
        index = end
        for indent in sorted(indents, reverse=True):
            while index < len(lines) and not lines[index].strip():
                index += 1
            if index >= len(lines) or not re.fullmatch(" " * indent + r"#-{8,}", lines[index]):
                issues.append((index + 1, "closing_divider_order"))
            index += 1
    return issues
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка сигнатуры, docstring и пояснений функции
#------------------------------------------------------------------------------------------------------------------
def _check_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    ) -> list[tuple[int, str]]:

    """
    Check function declarations, including methods and nested test helpers.

    :param node: Parsed function declaration.
    :type node: ast.FunctionDef | ast.AsyncFunctionDef

    :param lines: Decoded source lines without newline characters.
    :type lines: list[str]

    :return: Function-related formatting violations.
    :rtype: list[tuple[int, str]]
    """

    # node — объявление функции или метода.
    # lines — исходные строки для проверки пробелов и русских пояснений.

    issues = []
    args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
    args += [arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None]
    explicit = [arg for arg in args if arg.arg not in ("self", "cls")]
    if node.returns is None or any(arg.annotation is None for arg in explicit):
        issues.append((node.lineno, "type_annotations"))
    if args and (args[0].lineno == node.lineno or len({arg.lineno for arg in args}) != len(args)):
        issues.append((node.lineno, "signature_columns"))

    doc = ast.get_docstring(node)
    if not doc:
        return issues + [(node.lineno, "docstring")]
    doc_lines = doc.splitlines()
    for index, line in enumerate(doc_lines[1:], 1):
        if line.startswith((":param ", ":return:")) and doc_lines[index - 1].startswith(":type "):
            issues.append((node.lineno, "docstring_parameter_spacing"))
    first = node.body[0]
    if lines[first.lineno - 1].strip() != '"""':
        issues.append((first.lineno, "docstring_opening_line"))
    if lines[first.lineno - 2].strip():
        issues.append((node.lineno, "blank_before_docstring"))
    for arg in explicit:
        if f":param {arg.arg}:" not in doc or f":type {arg.arg}:" not in doc:
            issues.append((node.lineno, "parameter_documentation:" + arg.arg))

    none_return = isinstance(node.returns, ast.Constant) and node.returns.value is None
    if node.returns is not None and not none_return and (":return:" not in doc or ":rtype:" not in doc):
        issues.append((node.lineno, "return_documentation"))
    if ":return: " + doc.splitlines()[0] in doc:
        issues.append((node.lineno, "repeated_return_description"))
    if "The value described by this operation." in doc or "Untrusted input to validate according" in doc:
        issues.append((node.lineno, "vague_return_description"))
    for index in range(node.lineno - 1, first.lineno - 1):
        if lines[index].lstrip().startswith(") -> "):
            indent = len(lines[index]) - len(lines[index].lstrip())
            if indent != node.col_offset + 4:
                issues.append((index + 1, "signature_closing_indent"))

    if explicit and len(node.body) > 1:
        comments = "\n".join(lines[first.end_lineno:node.body[1].lineno - 1])
        for arg in explicit:
            if not any(re.search(r"\b" + re.escape(arg.arg) + r"\b", comment)
                       and re.search("[А-Яа-яЁё]", comment) for comment in comments.splitlines()):
                issues.append((node.lineno, "russian_argument_comments:" + arg.arg))
    return issues
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка полей dataclass и порядка методов класса
#------------------------------------------------------------------------------------------------------------------
def _check_class(
    node: ast.ClassDef,
    lines: list[str],
    ) -> list[tuple[int, str]]:

    """
    Check dataclass field explanations and constructor/public/special/private ordering.

    :param node: Parsed class declaration.
    :type node: ast.ClassDef

    :param lines: Decoded source lines without newline characters.
    :type lines: list[str]

    :return: Class-related formatting violations.
    :rtype: list[tuple[int, str]]
    """

    # node — класс, методы которого проверяются независимо от вложенных классов.
    # lines — строки с пояснениями полей.

    issues = []
    groups = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = child.name
            group = 0 if name == "__init__" else 2 if name.startswith("__") and name.endswith("__") else (
                3 if name.startswith("_") else 1
            )
            groups.append(group)
    if groups != sorted(groups):
        issues.append((node.lineno, "method_group_order"))

    if not ast.get_docstring(node):
        issues.append((node.lineno, "class_docstring"))
    elif lines[node.body[0].lineno - 1].strip() != '"""':
        issues.append((node.body[0].lineno, "docstring_opening_line"))
    if ast.get_docstring(node) and node.body[0].lineno != node.lineno + 1:
        issues.append((node.lineno, "class_docstring_spacing"))

    if any("dataclass" in ast.unparse(decorator) for decorator in node.decorator_list):
        doc = ast.get_docstring(node) or ""
        for field in node.body:
            if isinstance(field, ast.AnnAssign):
                comment = lines[field.lineno - 1].partition("#")[2]
                if not re.search("[А-Яа-яЁё]", comment):
                    issues.append((field.lineno, "russian_field_comment"))
                if isinstance(field.target, ast.Name):
                    name = field.target.id
                    if f":param {name}:" not in doc or f":type {name}:" not in doc:
                        issues.append((field.lineno, "dataclass_field_documentation:" + name))
    return issues
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Проверка секций по реальному содержимому модуля
#------------------------------------------------------------------------------------------------------------------
def _check_sections(
    tree: ast.Module,
    lines: list[str],
    header: str,
    ) -> list[tuple[int, str]]:

    """
    Require meaningful section headers and reject headings for absent content.

    :param tree: Parsed module used to distinguish declarations from text examples.
    :type tree: ast.Module

    :param lines: Source lines used to locate section separators.
    :type lines: list[str]

    :param header: Leading comments containing the module inventory and metadata.
    :type header: str

    :return: Missing or empty section and inventory diagnostics.
    :rtype: list[tuple[int, str]]
    """

    # tree — фактические объявления модуля, без исполнения.
    # lines — расположение секций в исходнике.
    # header — оглавление не должно содержать пустые группы.

    present = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            present.add("ИМПОРТ")
        elif isinstance(node, ast.ClassDef):
            present.add("КЛАССЫ")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            present.add("ТЕСТЫ" if node.name.startswith("test_") else "ФУНКЦИИ")
        elif isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                present.add("ЭКСПОРТ")
            else:
                present.add("КОНСТАНТЫ")
        elif isinstance(node, ast.AnnAssign):
            present.add("КОНСТАНТЫ")

    known = {"ИМПОРТ", "КЛАССЫ", "ФУНКЦИИ", "ТЕСТЫ", "КОНСТАНТЫ", "ЭКСПОРТ"}
    sections = {line[2:] for line in lines if line.startswith("# ") and line[2:] in known}
    issues = [(1, "missing_section:" + name) for name in sorted(present - sections)]
    issues += [(1, "empty_section:" + name) for name in sorted(sections - present)]
    if "# Объявления:" in header:
        issues.append((1, "unstructured_inventory"))
    for line in header.splitlines():
        if "->" in line and not re.search(r"-> .*: .*[А-Яа-яЁё]", line):
            issues.append((1, "inventory_description"))

    # Оглавление должно перечислять и вложенные функции, и методы, а не только
    # содержать несколько корректно оформленных строк с произвольными именами.
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            name = node.name + ":"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name + "():"
        else:
            continue
        if "-> " + name not in header:
            issues.append((node.lineno, "missing_inventory:" + node.name))
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_")
                and not isinstance(parents.get(node), ast.ClassDef)):
            # Внутренняя функция отличается от метода: её заголовок не должен
            # обещать публичный интерфейс или попадать в группу методов класса.
            start = min([node.lineno] + [item.lineno for item in node.decorator_list]) - 1
            if "# СЛУЖЕБНАЯ ФУНКЦИЯ :" not in lines[start - 2]:
                issues.append((node.lineno, "internal_function_heading"))

    # Методы должны находиться именно под своим классом и в его группе,
    # а не просто встречаться где-нибудь в плоском списке имён.
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue

        marker = "# -> " + node.name + ":"
        block = header.partition(marker)[2]
        block = re.split(r"\n# -> |\n# (?:Классы|Функции|Тесты|Константы):", block, maxsplit=1)[0]
        if "# Классы:" not in header or not block:
            issues.append((node.lineno, "class_inventory_group"))

        for method in node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            entry = re.search(r"# {4,5}-> " + re.escape(method.name) + r"\(\):", block)
            prefix = block[:entry.start()] if entry else block
            group = "Конструктор" if method.name == "__init__" else (
                "Специальные методы" if method.name.startswith("__") else
                "Служебные методы" if method.name.startswith("_") else "Интерфейс"
            )
            headings = re.findall(r"# {4,5}(Конструктор|Интерфейс|Специальные методы|Служебные методы):", prefix)
            if entry is None or not headings or headings[-1] != group:
                issues.append((method.lineno, "method_inventory_group:" + method.name))

    # Разделяем группы двумя пустыми строками; строковые примеры не создают
    # группы, если рядом нет полного оформленного заголовка секции.
    for index, line in enumerate(lines):
        if index < 1 or line not in {"# " + name for name in known}:
            continue
        if not lines[index - 1].startswith("#***"):
            continue
        before = index - 2
        blanks = 0
        while before >= 0 and not lines[before].strip():
            blanks += 1
            before -= 1
        if blanks != 2:
            issues.append((index + 1, "section_spacing"))
    return issues
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Открывающие границы классов, функций и вложенных методов
#------------------------------------------------------------------------------------------------------------------
def _check_opening(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    ) -> list[tuple[int, str]]:

    """
    Require an opening divider block before each declaration and its decorators.

    :param node: Declaration whose opening block is checked.
    :type node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef

    :param lines: Source lines without newline characters.
    :type lines: list[str]

    :return: Violations of opening-divider structure or Russian block descriptions.
    :rtype: list[tuple[int, str]]
    """

    # node — декораторы входят внутрь блока объявления, а не отделяются от него.
    # lines — разделители обязаны иметь тот же отступ, что и объявление.

    start = min([node.lineno] + [item.lineno for item in node.decorator_list]) - 1
    divider = " " * node.col_offset + r"#-{8,}"
    block = lines[max(0, start - 3):start]
    if len(block) != 3 or not re.fullmatch(divider, block[0]) or not re.fullmatch(divider, block[2]):
        return [(start + 1, "opening_divider")]
    if not re.search("[А-Яа-яЁё]", block[1]):
        return [(start, "russian_block_description")]
    before = start - 4
    blanks = 0
    while before >= 0 and not lines[before].strip():
        blanks += 1
        before -= 1
    if blanks != 1:
        return [(start, "opening_block_spacing")]
    return []
#------------------------------------------------------------------------------------------------------------------

# ТОЧКА ВХОДА : Запуск CLI
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
