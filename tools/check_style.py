# Проверка формальных правил оформления Python-файлов проекта без изменения исходников.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Функции:
# -> main(): Проверка файлов src, tests и tools с ненулевым кодом при нарушении.
# -> _check_file(): Кодировка, шапка, строки и оформление объявлений.
# -> _check_function(): Аннотации, docstring, сигнатура и пояснения аргументов.
# -> _check_class(): Поля dataclass и порядок групп методов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import ast
import re
from pathlib import Path


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка всех поддерживаемых Python-файлов репозитория
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """Check repository Python formatting without importing application modules.

    :return: Zero for clean sources, otherwise one.
    :rtype: int
    """

    root = Path(__file__).resolve().parents[1]
    files = sorted(path for folder in ("src", "tests", "tools") for path in (root / folder).rglob("*.py"))
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
# ФУНКЦИЯ : Общие правила оформления одного модуля
#------------------------------------------------------------------------------------------------------------------
def _check_file(path: Path) -> list[tuple[int, str]]:

    """Check physical file format and delegate Python declaration checks.

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
    if '__name__ == "__main__"' not in source and "__name__ == '__main__'" not in source:
        issues.append((len(lines), "main_guard"))

    if "tests" in path.parts and path.name != "conftest.py":
        if "# Тесты:" not in header:
            issues.append((1, "test_inventory"))
        if "pytest" not in source[source.rfind("if __name__"):]:
            issues.append((len(lines), "test_guard_pytest"))

    for number, line in enumerate(lines, 1):
        if len(line) > 115:
            issues.append((number, "line_length"))
        if line.rstrip() != line:
            issues.append((number, "trailing_whitespace"))

    # AST отделяет объявления от строковых примеров и комментариев. Проверка
    # подтверждает наличие пояснений, но не оценивает качество русского текста.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            issues.extend(_check_function(node, lines))
        elif isinstance(node, ast.ClassDef):
            issues.extend(_check_class(node, lines))
    return issues
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка сигнатуры, docstring и пояснений функции
#------------------------------------------------------------------------------------------------------------------
def _check_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
) -> list[tuple[int, str]]:

    """Check function declarations, including methods and nested test helpers.

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
    if len(args) > 1 and len({arg.lineno for arg in args}) != len(args):
        issues.append((node.lineno, "signature_columns"))

    doc = ast.get_docstring(node)
    if not doc:
        return issues + [(node.lineno, "docstring")]
    first = node.body[0]
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

    if explicit and len(node.body) > 1:
        comments = "\n".join(lines[first.end_lineno:node.body[1].lineno - 1])
        if not re.search("[А-Яа-яЁё]", comments):
            issues.append((node.lineno, "russian_argument_comments"))
    return issues
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка полей dataclass и порядка методов класса
#------------------------------------------------------------------------------------------------------------------
def _check_class(
    node: ast.ClassDef,
    lines: list[str],
) -> list[tuple[int, str]]:

    """Check dataclass field explanations and constructor/public/special/private ordering.

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

    if any("dataclass" in ast.unparse(decorator) for decorator in node.decorator_list):
        for field in node.body:
            if isinstance(field, ast.AnnAssign):
                comment = lines[field.lineno - 1].partition("#")[2]
                if not re.search("[А-Яа-яЁё]", comment):
                    issues.append((field.lineno, "russian_field_comment"))
    return issues
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Проверка исходных файлов без изменения их содержимого
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
