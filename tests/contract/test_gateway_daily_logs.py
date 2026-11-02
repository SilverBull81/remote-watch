# Ежедневные журналы сервера и выдержка кратких служебных оповещений.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261102-183757
#
# Тесты:
# -> test_daily_logs(): Полночь, пустые дни, ротация и повторное открытие
# -> test_foreign_calendar_file(): Отказ от чужих файлов во вложенных папках
# -> test_alert_grace(): Выдержка, восстановление и смена причины без сброса срока
# -> test_alert_bounds(): Проверка конечных пределов выдержки


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

import logging
from datetime import datetime
from pathlib import Path

import pytest

from remote_watch.gateway_server.application import OperatorAlerts, application_config
from remote_watch.gateway_server.config import DeploymentConfigError, check_log_directory
from remote_watch.gateway_server.manager import StatusLog

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полночь, пустые дни, ротация и повторное открытие
#------------------------------------------------------------------------------------------------------------------
def test_daily_logs(
    tmp_path: Path,
    ) -> None:

    """
    Append only nonempty calendar days and keep bounded size rotation inside each day.

    :param tmp_path: Dedicated synthetic log directory.
    :type tmp_path: Path
    """

    # tmp_path — два непоследовательных дня; переход месяца и перезапуск writer.

    for _ in range(2):
        handler = StatusLog(tmp_path / "gateway_server.jsonl", maxBytes=100, backupCount=1, delay=True)
        try:
            for day in (datetime(2026, 10, 31, 23, 59), datetime(2026, 11, 2, 0, 1)):
                for _ in range(4):
                    record = logging.LogRecord("synthetic", logging.INFO, "", 0, '{"state":"ready"}', (), None)
                    record.created = day.timestamp()
                    handler.handle(record)
        finally:
            handler.close()

    assert not handler.failed
    assert not (tmp_path / "gateway_server.jsonl").exists()
    assert not (tmp_path / "2026/11. November/gateway_server_log_261101.jsonl").exists()
    assert len(list(tmp_path.rglob("*.jsonl"))) == 2
    assert len(list(tmp_path.rglob("*.jsonl.1"))) == 2
    check_log_directory(tmp_path)

    # Старый журнал сохраняется: переход на ежедневные файлы не требует удаления.
    (tmp_path / "gateway_server.jsonl").write_text("old evidence", encoding="utf-8")
    check_log_directory(tmp_path)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ от чужих файлов во вложенных папках
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["foreign.txt", "gateway_server_log_261031.jsonl",
                                  "gateway_server_log_261132.jsonl", "gateway_server_log_261102.jsonl.21"])
def test_foreign_calendar_file(
    tmp_path: Path,
    name: str,
    ) -> None:

    """
    Reject foreign names and mismatched calendar files before changing any directory ACL.

    :param tmp_path: Synthetic directory with no production ownership.
    :type tmp_path: Path

    :param name: Invalid filename placed in the November directory.
    :type name: str
    """

    # tmp_path/name — содержимое остаётся нетронутым после отказа.

    folder = tmp_path / "2026/11. November"
    folder.mkdir(parents=True)
    entry = folder / name
    entry.write_text("preserve", encoding="utf-8")

    with pytest.raises(DeploymentConfigError, match="log_directory_shared"):
        check_log_directory(tmp_path)

    assert entry.read_text(encoding="utf-8") == "preserve"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Выдержка, восстановление и смена причины без сброса срока
#------------------------------------------------------------------------------------------------------------------
def test_alert_grace(
    caplog: pytest.LogCaptureFixture,
    ) -> None:

    """
    Suppress brief incidents, notify sustained failures and preserve immediate serious alerts.

    :param caplog: Local capture without provider credentials or network delivery.
    :type caplog: pytest.LogCaptureFixture
    """

    # caplog — время задаётся явно, без реального ожидания или изменения часов ОС.

    config = application_config({"alerts": {"cooldown": 60, "grace_period": 180}})
    alerts = OperatorAlerts(config)
    alerts.logger.addHandler(caplog.handler)

    alerts.observe("commands", "degraded", "time_unavailable", 0)
    alerts.observe("commands", "ready", None, 120)
    assert not caplog.records

    alerts.observe("commands", "degraded", "source_unavailable", 200)
    alerts.observe("commands", "degraded", "time_unavailable", 300)
    alerts.observe("commands", "degraded", "time_unavailable", 379)
    assert not caplog.records

    alerts.observe("commands", "degraded", "time_unavailable", 380)
    assert len(caplog.records) == 1
    alerts.observe("commands", "ready", None, 450)
    assert len(caplog.records) == 2 and "ready again" in caplog.records[-1].getMessage()

    alerts.observe("commands", "failed", "storage", 600)
    assert len(caplog.records) == 3

    # Серьёзная ошибка во время ещё не объявленного сбоя не ждёт выдержку.
    alerts = OperatorAlerts(config)
    alerts.logger.addHandler(caplog.handler)
    alerts.observe("commands", "degraded", "time_unavailable", 0)
    alerts.observe("commands", "failed", "storage", 1)
    assert len(caplog.records) == 4
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка конечных пределов выдержки
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [-1, 3601, True, "180", float("nan"), float("inf")])
def test_alert_bounds(
    value: object,
    ) -> None:

    """
    Reject nonnumeric, nonfinite and excessive grace periods without any I/O.

    :param value: Invalid untrusted JSON setting.
    :type value: object
    """

    # value — выдержка ограничена одним часом и не отключает диагностику.

    with pytest.raises((ValueError, TypeError)):
        application_config({"alerts": {"grace_period": value}})
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Запускайте тесты через pytest.")
#------------------------------------------------------------------------------------------------------------------
