# Исполнение опубликованных Python-примеров на подставных каналах без сети.
#
# Version 1.0.5
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Тесты:
# -> test_documentation_examples(): Исполнение примеров из документации без сети.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import re
from pathlib import Path

import pytest


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Исполнение примеров из документации без сети
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("document", [
    "COMMANDS", "COMMAND_PROTOCOL", "COMMAND_TIME", "COMMAND_STORAGE",
    "RUNTIME", "ADAPTERS", "WATCHER", "RELAY", "GATEWAY_SERVER",
])
def test_documentation_examples(document: str) -> None:

    """Execute each document's Python fences in order using its fake transport.

    :param document: Documentation basename.
    :type document: str
    """

    # document - имя документа с проверяемыми примерами.

    path = Path(__file__).resolve().parents[2] / "docs" / (document + ".md")
    blocks = re.findall(r"```python\s*\n(.*?)```", path.read_text(encoding="utf-8-sig"), re.S)
    assert blocks
    namespace = {"__name__": "__main__"}
    for index, block in enumerate(blocks, 1):
        exec(compile(block, f"{path}:example-{index}", "exec"), namespace)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.unit.test_documentation не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
