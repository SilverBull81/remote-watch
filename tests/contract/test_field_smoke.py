# Полевой сценарий с подставными каналами, ускоренным временем и безопасным отчётом.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Классы:
# -> RecordingChannel: Подставной канал для полевой проверки.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска работы.
#    Интерфейс:
#    -> open(): Открытие канала в принадлежащем ему цикле событий.
#    -> send(): Одна попытка отправки без собственного цикла повторов.
#    -> close(): Закрытие канала и освобождение временных данных.
#
# -> FastClock: Ускоренные часы расписания тестовых сообщений.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска работы.
#    Интерфейс:
#    -> monotonic(): Чтение управляемого времени расписания.
#    -> sleep(): Продвижение расписания без реального ожидания.
#
# Тесты:
# -> test_field_run(): Полный полевой сценарий через два подставных канала.
# -> test_field_failure_and_interrupt(): Отчёт при отказе сервиса и прерывании пользователем.
# -> test_field_bounds(): Ограничения длительности и числа сообщений.
# -> test_journal_bound(): Учёт переполнения диагностического буфера.
# -> test_cli_credentials_cleanup(): Очистка временного токена после ошибки настройки.
# -> test_report_is_exclusive(): Запрет перезаписи существующего отчёта.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from remote_watch import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Identity,
    RetryPolicy,
)
from remote_watch.diagnostics import field_smoke as field_smoke


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Подставной канал для полевой проверки
#------------------------------------------------------------------------------------------------------------------
class RecordingChannel:
    """Record the field workload without external network calls."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Prepare observation state and an optional first-attempt failure."""

        self.deliveries: list[Delivery] = []
        self.closed = False
        self.status = DeliveryStatus.PROVIDER_ACCEPTED
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие канала в принадлежащем ему цикле событий
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Keep the fake channel network-free."""

        pass
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправки без собственного цикла повторов
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Return a configured safe provider result.

        :param delivery: Immutable delivery attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        self.deliveries.append(delivery)
        return DeliveryResult(status=self.status)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие канала и освобождение временных данных
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Record cleanup at the end of the finite run."""

        self.closed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ускоренные часы расписания тестовых сообщений
#------------------------------------------------------------------------------------------------------------------
class FastClock:
    """Advance sample scheduling deterministically while using a real runtime thread."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Start an independent application clock."""

        self.value = 0.0
        self.interrupt = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение управляемого времени расписания
    #--------------------------------------------------------------------------------------------------------------
    def monotonic(self) -> float:

        """Read the current synthetic sample time.

        :return: The value described by this operation.
        :rtype: float
        """

        return self.value
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Продвижение расписания без реального ожидания
    #--------------------------------------------------------------------------------------------------------------
    def sleep(
        self,
        delay: float,
        ) -> None:

        """Advance the sample timer without simulating provider or retry time.

        :param delay: Validated minimum retry delay.
        :type delay: float
        """

        # delay - проверенная минимальная задержка повтора.

        if self.interrupt:
            raise KeyboardInterrupt
        self.value += delay
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полный полевой сценарий через два подставных канала
#------------------------------------------------------------------------------------------------------------------
def test_field_run(
    identity: Identity,
    tmp_path: Path,
    ) -> None:

    """Exercise all four sample types through two channels, logging and shutdown.

    :param identity: Explicit application identity.
    :type identity: Identity

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path
    """

    # identity - явные сведения об отправителе.
    # tmp_path - временный каталог теста.

    channels = [RecordingChannel(), RecordingChannel()]
    destinations = tuple(Destination(destination_id=name, channel_factory=lambda c=c: c)
                         for name, c in zip(("telegram", "ntfy"), channels))
    clock = FastClock()
    output = tmp_path / "field.jsonl"
    result = field_smoke._run(field_smoke.FieldConfig(identity=identity, duration=4, interval=1),
                              destinations, output, monotonic=clock.monotonic, sleep=clock.sleep)
    assert result == 0
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert records[0]["identity"] == {name: getattr(identity, name)
                                      for name in ("service", "environment", "region", "host", "instance_id")}
    assert records[-1]["samples"] == 4 and records[-1]["provider_acceptance_complete"]
    assert records[-1]["phone_display_verified"] is False
    assert len([r for r in records if r["kind"] == "attempt"]) == 8
    for channel in channels:
        assert len(channel.deliveries) == 4 and channel.closed
        notifications = [d.notification for d in channel.deliveries]
        assert [n.correlation_id for n in notifications] == ["1", "2", "3", "4"]
        assert "FIELD_TEST_SECRET" not in notifications[2].exception
        assert "[удалено]" in notifications[2].exception
        assert notifications[3].truncated_fields == ("message",)
        assert all("LOCAL_ONLY" not in n.message for n in notifications)
    assert "LOCAL_ONLY" in output.with_suffix(".log").read_text(encoding="utf-8")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отчёт при отказе сервиса и прерывании пользователем
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("interrupted", [False, True])
def test_field_failure_and_interrupt(
    identity: Identity,
    tmp_path: Path,
    interrupted: bool,
    ) -> None:

    """Preserve a summary and distinguish unavailable providers from user interruption.

    :param identity: Explicit application identity.
    :type identity: Identity

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param interrupted: Whether to interrupt the run.
    :type interrupted: bool
    """

    # identity - явные сведения об отправителе.
    # tmp_path - временный каталог теста.
    # interrupted - выбор прерывания пользователем.

    channel = RecordingChannel()
    channel.status = DeliveryStatus.PERMANENT_FAILURE
    clock = FastClock()
    clock.interrupt = interrupted
    output = tmp_path / "field.jsonl"
    destination = Destination(destination_id="ntfy", channel_factory=lambda: channel,
                              retry=RetryPolicy(max_attempts=1))
    result = field_smoke._run(field_smoke.FieldConfig(identity=identity, duration=1, interval=1),
                              (destination,), output, monotonic=clock.monotonic, sleep=clock.sleep)
    assert result == (130 if interrupted else 1)
    summary = json.loads(output.read_text(encoding="utf-8").splitlines()[-1])
    assert not summary["provider_acceptance_complete"] and summary["interrupted"] is interrupted
    assert channel.closed
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ограничения длительности и числа сообщений
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("duration,interval", [(float("nan"), 1), (0, 1), (604801, 900), (2000, 1), (5, 0.1)])
def test_field_bounds(
    identity: Identity,
    duration: float,
    interval: float,
    ) -> None:

    """Reject unbounded or overly frequent workloads before reading credentials.

    :param identity: Explicit application identity.
    :type identity: Identity

    :param duration: Finite run duration in seconds.
    :type duration: float

    :param interval: Sample interval in seconds.
    :type interval: float
    """

    # identity - явные сведения об отправителе.
    # duration - длительность проверки, секунды.
    # interval - интервал сообщений, секунды.

    with pytest.raises((ValueError, TypeError)):
        field_smoke.FieldConfig(identity=identity, duration=duration, interval=interval)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Учёт переполнения диагностического буфера
#------------------------------------------------------------------------------------------------------------------
def test_journal_bound() -> None:

    """Account for diagnostic overflow without blocking the worker on local disk."""

    journal = field_smoke._Journal()
    for index in range(1025):
        journal.record({"index": index})
    output = io.StringIO()
    journal.flush(output)
    assert len(output.getvalue().splitlines()) == 1024 and journal.dropped == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Очистка временного токена после ошибки настройки
#------------------------------------------------------------------------------------------------------------------
def test_cli_credentials_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ) -> None:

    """Resolve both local credential sections and erase temporary environment entries on failure.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured command output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # tmp_path - временный каталог теста.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # capsys - перехват вывода команды.

    secret = "synthetic_private_field_token"
    credentials = tmp_path / "credentials.local.json"
    credentials.write_text(json.dumps({"telegram": {"token": secret}, "ntfy": {"token": secret}}))
    environment_names = []

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ленивое назначение с заданным способом доставки
    #--------------------------------------------------------------------------------------------------------------
    def destination(
        provider: str,
        settings: dict[str, object],
        token_env: str,
        ) -> Destination:

        """Verify temporary ownership before simulating a configuration failure.

        :param provider: Provider selected by the test.
        :type provider: str

        :param settings: Local provider settings.
        :type settings: dict[str, object]

        :param token_env: Temporary service credential environment name.
        :type token_env: str

        :return: Lazy destination configuration.
        :rtype: Destination
        """

        # provider - сервис, выбранный для проверки.
        # settings - настройки провайдера из локального файла.
        # token_env - имя временной переменной с токеном.

        environment_names.append(token_env)
        assert os.environ[token_env] == secret
        raise ValueError(secret)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(field_smoke, "_destination", destination)
    result = field_smoke.main(["both", "--credentials", str(credentials), "--region", "test",
                               "--host", "test-host", "--instance-id", "one"])
    assert result == 2 and all(name not in os.environ for name in environment_names)
    assert secret not in capsys.readouterr().out
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет перезаписи существующего отчёта
#------------------------------------------------------------------------------------------------------------------
def test_report_is_exclusive(
    identity: Identity,
    tmp_path: Path,
    ) -> None:

    """Never replace an earlier report when a path is reused.

    :param identity: Explicit application identity.
    :type identity: Identity

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path
    """

    # identity - явные сведения об отправителе.
    # tmp_path - временный каталог теста.

    output = tmp_path / "field.jsonl"
    output.write_text("previous report")
    channel = RecordingChannel()
    with pytest.raises(FileExistsError):
        field_smoke._run(field_smoke.FieldConfig(identity=identity, duration=1, interval=1),
                          (Destination(destination_id="phone", channel_factory=lambda: channel),), output)
    assert output.read_text() == "previous report" and not channel.closed
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_field_smoke не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
