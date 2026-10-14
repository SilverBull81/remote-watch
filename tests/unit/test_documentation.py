# Проверка защиты публичных инструкций от частных ссылок и испорченных примеров.
#
# Version 1.1.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-185741
#
# Тесты:
# -> test_document_checks(): Неверные ссылки, частные пути и Python-примеры обнаруживаются без сети
#
# -> test_documentation_examples(): Актуальные Python-примеры исполняются с подставным провайдером


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

import logging
import re
import runpy
import ssl
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from remote_watch import DeliveryResult, DeliveryStatus

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Неверные ссылки, частные пути и Python-примеры обнаруживаются без сети
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("body, expected", [
    ("[private](private/operator.md)", "private_or_external_local_link"),
    ("[local](file:///private/operator.md)", "private_or_external_local_link"),
    ("[missing](missing.md)", "broken_local_link"),
    ("```python\ndef broken(\n```", "python_example_syntax"),
    ("# Example\n\nPublic instructions", None),
])
def test_document_checks(
    tmp_path: Path,
    body: str,
    expected: str | None,
    ) -> None:

    """
    Detect documentation regressions without opening private targets or executing examples.

    :param tmp_path: Temporary documentation root.
    :type tmp_path: Path

    :param body: Synthetic Markdown content containing one controlled case.
    :type body: str

    :param expected: Required safe diagnostic, or None for valid Markdown.
    :type expected: str | None
    """

    # tmp_path/body/expected — проверяются только временные документы, без production файлов.

    tool = Path(__file__).resolve().parents[2] / "tools/check_docs.py"
    check = runpy.run_path(str(tool))["check_markdown"]
    page = tmp_path / "README.md"
    page.write_bytes(b"\xef\xbb\xbf" + body.replace("\n", "\r\n").encode("utf-8"))
    result = check(page, tmp_path)
    assert result == ([expected] if expected else [])
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Актуальные Python-примеры исполняются с подставным провайдером
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("document", ["README", "CLIENT", "NOTIFICATIONS", "LOCAL_LOGGING", "COMMANDS"])
def test_documentation_examples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document: str,
    ) -> None:

    """
    Execute maintained client examples with temporary files and no provider or gateway traffic.

    :param tmp_path: Working directory for local handlers created by examples.
    :type tmp_path: Path

    :param monkeypatch: Temporary environment and fake provider operations.
    :type monkeypatch: pytest.MonkeyPatch

    :param document: User guide containing executable Python fences.
    :type document: str
    """

    # tmp_path/monkeypatch — примеры пишут только временные журналы; сетевые операции подменены.
    # document — в COMMANDS исполняются оба примера регистрации, а подключение живого
    # клиента проверяется отдельными contract tests и синтаксической проверкой всех блоков.

    root = Path(__file__).resolve().parents[2]
    path = root / ("README.md" if document == "README" else f"docs/{document}.md")
    blocks = re.findall(r"```python\s*\n(.*?)\n```", path.read_text(encoding="utf-8-sig"), re.S)
    assert blocks
    if document == "COMMANDS":
        assert len(blocks) == 3
        blocks = blocks[:2]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_TELEGRAM_TOKEN", "12345:synthetic_documentation_token")
    monkeypatch.setenv("APP_TELEGRAM_CHAT_ID", "123456789")
    from remote_watch.adapters.telegram_notifications import TelegramChannel

    monkeypatch.setattr(TelegramChannel, "open", AsyncMock())
    monkeypatch.setattr(TelegramChannel, "send", AsyncMock(
        return_value=DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)))
    monkeypatch.setattr(TelegramChannel, "close", AsyncMock())
    context = ssl.create_default_context()
    monkeypatch.setattr(ssl, "create_default_context", lambda *args, **kwargs: context)
    logger = logging.getLogger("my-app")
    previous_level = logger.level
    namespace = {"__name__": "__main__"}
    try:
        for block in blocks:
            exec(compile(block, "<documentation>", "exec"), namespace)
    finally:
        logger.setLevel(previous_level)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Запускайте тесты через pytest.")
#------------------------------------------------------------------------------------------------------------------
