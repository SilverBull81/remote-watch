# Проверка публичных инструкций, ссылок, примеров и границы private-документации.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-185741
#
# Функции:
# -> main(): Проверка пользовательских документов и поставляемых шаблонов
#
# -> check_markdown(): Ссылки, кодировка и отсутствие частной инфраструктуры в документе


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка пользовательских документов и поставляемых шаблонов
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """
    Validate maintained documentation without reading private files or contacting services.

    :return: Zero when links, snippets and examples pass, otherwise one.
    :rtype: int
    """

    root = Path(__file__).resolve().parents[1]
    documents = [root / "README.md", root / "AGENTS.md", root / "BACKLOG.md"]
    documents.extend(path for path in (root / "docs").rglob("*.md")
                     if "private" not in path.relative_to(root / "docs").parts)
    documents.extend((root / "src/remote_watch/gateway_server/templates").glob("*.md"))
    issues = []
    for path in documents:
        issues.extend(f"{path.relative_to(root).as_posix()}: {item}" for item in check_markdown(path, root))

    # Только JSON-примеры, не локальные конфиги оператора. Дубли и небезопасные числа
    # проверяются production loaders в contract tests; здесь отсеивается порча примера.
    examples = list((root / "docs/examples").glob("*.json"))
    examples.extend((root / "src/remote_watch/gateway_server/templates").glob("*.json"))
    for path in examples:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
            json.loads(text)
            if raw.startswith(b"\xef\xbb\xbf"):
                raise ValueError
            if re.search(r"\b(?:ru|lv|spambot)\b|C:[/\\]+Work[/\\]+", text, re.IGNORECASE):
                issues.append(f"{path.relative_to(root).as_posix()}: private_infrastructure_reference")
        except (UnicodeError, ValueError):
            issues.append(f"{path.relative_to(root).as_posix()}: invalid_json_example")

    for item in issues:
        print(item)

    print(f"Documentation: {len(documents)} documents, {len(examples)} JSON examples, {len(issues)} issues")
    return int(bool(issues))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ссылки, кодировка и отсутствие частной инфраструктуры в документе
#------------------------------------------------------------------------------------------------------------------
def check_markdown(
    path: Path,
    root: Path,
    ) -> list[str]:

    """
    Inspect one public document while refusing links into private or external local paths.

    :param path: Public Markdown document under the repository root.
    :type path: Path

    :param root: Repository boundary used for relative link validation.
    :type root: Path

    :return: Fixed diagnostic categories without document excerpts or private values.
    :rtype: list[str]
    """

    # path/root — private содержимое не открывается даже при ошибочной публичной ссылке.

    data = path.read_bytes()
    issues = []
    if not data.startswith(b"\xef\xbb\xbf") or b"\n" in data.replace(b"\r\n", b""):
        issues.append("document_encoding")

    text = data.decode("utf-8-sig")
    if re.search(r"\b(?:LV|RU)\b|(?i:SpamBot)|(?i:C:[/\\]Work[/\\])", text):
        issues.append("private_infrastructure_reference")

    # Относительные ссылки проверяются с учётом регистра, чтобы Windows не скрывал
    # будущую ошибку Linux. Query/fragment не являются частью имени файла.
    for match in re.finditer(r"\]\(([^\s)]+)(?:\s+[^)]*)?\)", text):
        url = urlsplit(match[1])
        if url.scheme in {"http", "https", "mailto"} or not url.path:
            continue
        if url.scheme:
            issues.append("private_or_external_local_link")
            continue
        target = (path.parent / unquote(url.path)).resolve()
        if not target.is_relative_to(root.resolve()) or "private" in target.relative_to(root.resolve()).parts:
            issues.append("private_or_external_local_link")
            continue
        if not target.exists():
            issues.append("broken_local_link")
            continue
        relative = target.relative_to(root.resolve())
        cursor = root.resolve()
        for part in relative.parts:
            if part not in {entry.name for entry in cursor.iterdir()}:
                issues.append("local_link_case")
                break
            cursor /= part

    for match in re.finditer(r"```python\s*\n(.*?)\n```", text, re.DOTALL):
        try:
            compile(match[1], "<documentation>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        except SyntaxError:
            issues.append("python_example_syntax")

    return issues
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Проверка документации
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
