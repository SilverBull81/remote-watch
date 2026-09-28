# Проверки ручных smoke-команд без реальных токенов и сетевых запросов.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-180519
#
# Классы:
# -> FakeChannel: Тестовый канал для ручной проверки.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Подготовка клиента и данных авторизации.
#    -> send(): Одна попытка отправки и проверка ответа.
#    -> close(): Закрытие клиента и освобождение ресурсов.
#
# Функции и тесты:
# -> channels(): Подмена обоих каналов без запуска сети.
# -> test_smoke_command(): Выбор одного сервиса, свежие данные и очистка временного токена.
# -> test_bad_credentials(): Безопасная ошибка при неверном локальном файле.
# -> test_default_path_and_missing_file(): Чтение настроек из текущего каталога.
# -> test_cleanup_on_failure(): Очистка после отмены и ошибок запуска или доставки.
# -> test_argument_errors_are_private(): Ошибочные аргументы не попадают в вывод.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from remote_watch import Delivery, DeliveryResult, DeliveryStatus, RetryPolicy, smoke
from remote_watch.adapters import ntfy, telegram


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Тестовый канал для ручной проверки
#------------------------------------------------------------------------------------------------------------------
class FakeChannel:
    """Capture smoke delivery and model startup failures or cancellation."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: telegram.TelegramConfig | ntfy.NtfyConfig,
        *,
        retry: RetryPolicy,
        ) -> None:

        """Keep the real validated configuration but skip the HTTP client.

        :param config: Validated provider configuration.
        :type config: telegram.TelegramConfig | ntfy.NtfyConfig

        :param retry: Shared runtime and channel policy.
        :type retry: RetryPolicy
        """

        # config - проверенные настройки сервиса.
        # retry - общие ограничения runtime и канала.

        self.config = config
        self.retry = retry
        self.deliveries: list[Delivery] = []
        self.closed = False
        self.cancel = False
        self.open_error = False
        self.status = DeliveryStatus.PROVIDER_ACCEPTED
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка клиента и данных авторизации
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Check temporary authentication or reproduce a private startup error."""

        assert os.environ[self.config.token_env] == "123:synthetic_secret"
        if self.open_error:
            raise RuntimeError("synthetic_secret")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправки и проверка ответа
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Record one attempt or propagate cancellation to the smoke owner.

        :param delivery: Immutable delivery attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        self.deliveries.append(delivery)
        if self.cancel:
            raise asyncio.CancelledError
        return DeliveryResult(status=self.status)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие клиента и освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Record cleanup without touching the network."""

        self.closed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подмена обоих каналов без запуска сети
#------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def channels(monkeypatch: pytest.MonkeyPatch) -> list[FakeChannel]:

    """Replace both adapter constructors while preserving configuration validation.

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :return: The value described by this operation.
    :rtype: list[FakeChannel]
    """

    # monkeypatch - фикстура подмены зависимостей и окружения.

    created: list[FakeChannel] = []

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Создание и сохранение тестовой сессии
    #--------------------------------------------------------------------------------------------------------------
    def create(
        config: telegram.TelegramConfig | ntfy.NtfyConfig,
        *,
        retry: RetryPolicy,
        ) -> FakeChannel:

        """Capture a channel constructed by the smoke command.

        :param config: Validated provider configuration.
        :type config: telegram.TelegramConfig | ntfy.NtfyConfig

        :param retry: Shared runtime and channel policy.
        :type retry: RetryPolicy

        :return: The value described by this operation.
        :rtype: FakeChannel
        """

        # config - проверенные настройки сервиса.
        # retry - общие ограничения runtime и канала.

        channel = FakeChannel(config, retry=retry)
        created.append(channel)
        return channel
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(telegram, "TelegramChannel", create)
    monkeypatch.setattr(ntfy, "NtfyChannel", create)
    return created
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Выбор одного сервиса, свежие данные и очистка временного токена
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider,address", [("telegram", "chat"), ("telegram", "chat_id"),
    ("ntfy", "chat"), ("ntfy", "topic")])
def test_smoke_command(
    provider: str,
    address: str,
    tmp_path: Path,
    channels: list[FakeChannel],
    capsys: pytest.CaptureFixture[str],
    ) -> None:

    """Use one provider from the local file, send fresh data and remove its temporary token.

    :param provider: Provider selected by the test.
    :type provider: str

    :param address: Address field name used by the local file.
    :type address: str

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param channels: Captured fake adapters.
    :type channels: list[FakeChannel]

    :param capsys: Captured command output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # provider - сервис, выбранный для проверки.
    # address - имя поля адресата в локальном файле.
    # tmp_path - временный каталог теста.
    # channels - тестовые адаптеры, созданные командой.
    # capsys - перехват вывода команды.

    path = tmp_path / "credentials.local.json"
    target = "-123" if provider == "telegram" else "test-topic"
    path.write_text(json.dumps({provider: {"token": "123:synthetic_secret", address: target},
        "unused": {"token": ""}}), encoding="utf-8-sig")
    original = path.read_bytes()
    before = dict(os.environ)
    assert smoke.main([provider, "--credentials", str(path)]) == 0
    assert len(channels) == 1 and channels[0].closed
    assert len(channels[0].deliveries) == 1 and channels[0].retry.max_attempts == 1
    event = channels[0].deliveries[0].notification
    assert event.identity.service == "remote-watch-smoke"
    assert event.created_at.year >= 2026 and event.level_name == "INFO"
    assert event.event_id != "event-1"
    assert dict(os.environ) == before and path.read_bytes() == original
    output = capsys.readouterr().out
    assert "synthetic_secret" not in output and target not in output and str(path) not in output
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасная ошибка при неверном локальном файле
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("content", ["{synthetic_secret", "[]", "{}", '{"telegram":[]}',
    '{"telegram":{"token":""}}', '{"telegram":{"token":123}}', "x" * 65537],
    ids=[
        "invalid-json", "list-root", "missing-section", "list-section", "empty-token", "numeric-token", "oversize",
    ],
)
def test_bad_credentials(
    content: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    ) -> None:

    """Report invalid local data without printing its values or JSON parser details.

    :param content: Synthetic local JSON content.
    :type content: str

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param capsys: Captured command output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # content - синтетическое содержимое локального JSON.
    # tmp_path - временный каталог теста.
    # capsys - перехват вывода команды.

    path = tmp_path / "credentials.local.json"
    path.write_text(content, encoding="utf-8")
    assert smoke.main(["telegram", "--credentials", str(path)]) == 2
    assert "synthetic_secret" not in capsys.readouterr().out
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Чтение настроек из текущего каталога
#------------------------------------------------------------------------------------------------------------------
def test_default_path_and_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    channels: list[FakeChannel],
    ) -> None:

    """Read only the current directory by default and fail before network access if absent.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param channels: Captured fake adapters.
    :type channels: list[FakeChannel]
    """

    # tmp_path - временный каталог теста.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # channels - тестовые адаптеры, созданные командой.

    monkeypatch.chdir(tmp_path)
    assert smoke.main(["telegram"]) == 2
    assert not channels
    Path("credentials.local.json").write_text(
        '{"telegram":{"token":"123:synthetic_secret","chat":"-123"}}', encoding="utf-8",
    )
    assert smoke.main(["telegram"]) == 0
    assert len(channels) == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Очистка после отмены и ошибок запуска или доставки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["cancel", "open_error", "unknown", "permanent_failure"])
def test_cleanup_on_failure(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """Remove the temporary token and close the adapter for every failure outcome.

    :param mode: Selected failure or success scenario.
    :type mode: str

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # mode - выбранный сценарий ответа сервера.
    # monkeypatch - фикстура подмены зависимостей и окружения.

    created: list[FakeChannel] = []

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Создание и сохранение тестовой сессии
    #--------------------------------------------------------------------------------------------------------------
    def create(
        config: telegram.TelegramConfig,
        *,
        retry: RetryPolicy,
        ) -> FakeChannel:

        """Prepare one selected failure mode.

        :param config: Validated provider configuration.
        :type config: telegram.TelegramConfig

        :param retry: Shared runtime and channel policy.
        :type retry: RetryPolicy

        :return: The value described by this operation.
        :rtype: FakeChannel
        """

        # config - проверенные настройки сервиса.
        # retry - общие ограничения runtime и канала.

        channel = FakeChannel(config, retry=retry)
        channel.cancel = mode == "cancel"
        channel.open_error = mode == "open_error"
        if mode in ("unknown", "permanent_failure"):
            channel.status = DeliveryStatus(mode)
        created.append(channel)
        return channel
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(telegram, "TelegramChannel", create)
    before = dict(os.environ)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Check cancellation and exceptions separately from classified provider results."""

        settings = {"token": "123:synthetic_secret", "chat": "-123"}
        if mode == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await smoke._send("telegram", settings)
        elif mode == "open_error":
            with pytest.raises(RuntimeError):
                await smoke._send("telegram", settings)
        else:
            result = await smoke._send("telegram", settings)
            assert result.status.value == mode
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
    assert len(created) == 1 and created[0].closed
    assert len(created[0].deliveries) <= 1 and dict(os.environ) == before
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибочные аргументы не попадают в вывод
#------------------------------------------------------------------------------------------------------------------
def test_argument_errors_are_private(capsys: pytest.CaptureFixture[str]) -> None:

    """Do not echo even an accidentally supplied token-like command argument.

    :param capsys: Captured command output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # capsys - перехват вывода команды.

    assert smoke.main(["synthetic_secret"]) == 2
    assert "synthetic_secret" not in capsys.readouterr().out
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.unit.test_smoke не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
