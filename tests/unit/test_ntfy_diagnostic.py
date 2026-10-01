# Проверки конечного ntfy-сценария, безопасного отчёта и очистки при ошибках.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Тесты:
# -> test_diagnostic_run(): Серия точных размеров и очистка при отказах.
# -> test_diagnostic_bounds(): Отказ от неверного расписания до сети.
# -> test_diagnostic_exclusive(): Сохранение существующего отчёта.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from remote_watch import Delivery, DeliveryResult, DeliveryStatus
from remote_watch.adapters._common import render
from remote_watch.diagnostics import ntfy_diagnostic as diagnostic


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Серия точных размеров и очистка при отказах
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["success", "failure", "rate", "cancel", "open_error", "close_error"])
def test_diagnostic_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:

    """Check exact sizes, one attempt per sample, early stop and secret cleanup.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured command output.
    :type capsys: pytest.CaptureFixture[str]

    :param mode: Selected failure or success scenario.
    :type mode: str
    """

    # tmp_path - временный каталог теста.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # capsys - перехват вывода команды.
    # mode - выбранный сценарий ответа сервера.

    calls: list[Delivery] = []
    delays: list[float] = []
    states = {"closed": False}
    original_environment = dict(os.environ)

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Подставной канал короткого диагностического сценария
    #--------------------------------------------------------------------------------------------------------------
    class Channel:
        """Stand in for ntfy without HTTP or credentials outside the test process."""

        #----------------------------------------------------------------------------------------------------------
        # КОНСТРУКТОР
        #----------------------------------------------------------------------------------------------------------
        def __init__(
            self,
            config: Any,
            *,
            retry: Any,
        ) -> None:

            """Verify the diagnostic owns a single-attempt channel.

            :param config: Validated provider configuration.
            :type config: Any

            :param retry: Shared runtime and channel policy.
            :type retry: Any
            """

            # config - проверенные настройки сервиса.
            # retry - общие ограничения runtime и канала.

            self.config = config
            assert retry.max_attempts == 1
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Подготовка клиента и данных авторизации
        #----------------------------------------------------------------------------------------------------------
        async def open(self) -> None:

            """Read only the temporary synthetic token."""

            assert os.environ[self.config.token_env] == "private_token"
            if mode == "open_error":
                raise ValueError("private_token")
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Одна попытка отправки и проверка ответа
        #----------------------------------------------------------------------------------------------------------
        async def send(
            self,
            delivery: Delivery,
        ) -> DeliveryResult:

            """Record each exact-size input and optionally fail without retrying.

            :param delivery: Immutable delivery attempt.
            :type delivery: Delivery

            :return: Sanitized provider result.
            :rtype: DeliveryResult
            """

            # delivery - подготовленные данные одной попытки.

            calls.append(delivery)
            if mode == "cancel":
                raise asyncio.CancelledError
            status = {"rate": DeliveryStatus.RATE_LIMITED, "failure": DeliveryStatus.TRANSIENT_FAILURE}.get(
                mode, DeliveryStatus.PROVIDER_ACCEPTED)
            http = {"rate": 429, "failure": 502}.get(mode, 200)
            return DeliveryResult(status=status, http_status=http, message_bytes=500, request_bytes=600)
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Закрытие клиента и освобождение ресурсов
        #----------------------------------------------------------------------------------------------------------
        async def close(self) -> None:

            """Record cleanup even after partial startup or cancellation."""

            states["closed"] = True
            if mode == "close_error":
                raise ValueError("private_token")
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Учёт пауз без реального ожидания
    #--------------------------------------------------------------------------------------------------------------
    async def sleep(delay: float) -> None:

        """Advance the schedule immediately while recording the requested pause.

        :param delay: Validated minimum retry delay.
        :type delay: float
        """

        # delay - проверенная минимальная задержка повтора.

        delays.append(delay)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(diagnostic, "NtfyChannel", Channel)
    monkeypatch.setattr(diagnostic.asyncio, "sleep", sleep)
    credentials = tmp_path / "credentials.local.json"
    credentials.write_text(json.dumps({"ntfy": {"topic": "private_topic", "token": "private_token"}}))
    output = tmp_path / "report.jsonl"
    args = ["--credentials", str(credentials), "--output", str(output)]
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            diagnostic.main(args)
    else:
        expected = 0 if mode == "success" else 2 if mode.endswith("error") else 1
        assert diagnostic.main(args) == expected
    assert states["closed"]
    assert dict(os.environ) == original_environment
    raw = output.read_text(encoding="utf-8")
    records = [json.loads(line) for line in raw.splitlines()]
    assert records[-1]["kind"] == "summary"
    expected_count = 0 if mode == "open_error" else 1 if mode in ("rate", "cancel") else 10
    assert len(calls) == expected_count
    for delivery, (_, size) in zip(calls, diagnostic.CASES):
        assert len(render(delivery).encode("utf-8")) == size
    assert all(delay == 10 for delay in delays)
    assert "private_token" not in raw + capsys.readouterr().out
    assert "private_topic" not in raw
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ от неверного расписания до сети
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("interval", ["0", "4", "61", "nan", "inf", "private_token"])
def test_diagnostic_bounds(
    tmp_path: Path,
    interval: str,
    capsys: pytest.CaptureFixture[str],
) -> None:

    """Reject invalid schedules before creating reports or attempting network access.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param interval: Seconds between attempts.
    :type interval: str

    :param capsys: Captured command output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # tmp_path - временный каталог теста.
    # interval - пауза между попытками в секундах.
    # capsys - перехват вывода команды.

    credentials = tmp_path / "credentials.local.json"
    credentials.write_text(json.dumps({"ntfy": {"topic": "synthetic", "token": "synthetic"}}))
    output = tmp_path / "report.jsonl"
    args = ["--credentials", str(credentials), "--output", str(output), "--interval", interval]
    assert diagnostic.main(args) == 2
    assert not output.exists()
    assert "private_token" not in capsys.readouterr().out
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение существующего отчёта
#------------------------------------------------------------------------------------------------------------------
def test_diagnostic_exclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    """Preserve an existing report and leave no temporary credentials behind.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # tmp_path - временный каталог теста.
    # monkeypatch - фикстура подмены зависимостей и окружения.

    output = tmp_path / "report.jsonl"
    output.write_text("original", encoding="ascii")
    environment = dict(os.environ)
    with pytest.raises(FileExistsError):
        asyncio.run(diagnostic._run({"topic": "synthetic", "token": "synthetic"}, output, 10))
    assert output.read_text() == "original"
    assert dict(os.environ) == environment
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.unit.test_ntfy_diagnostic не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
