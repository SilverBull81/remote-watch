# Плановая остановка gateway: однократное оповещение и завершение очистки при ошибках сети.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261103-083907
#
# Тесты:
# -> test_stop_notice(): Плановое сообщение не ждёт cooldown или grace_period
# -> test_shutdown_cleanup(): Откат не выдаётся за плановый stop; отказ оповещения не мешает очистке


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from remote_watch.gateway_server.application import ApplicationConfig, OperatorAlerts
from remote_watch.gateway_server.config import ComponentConfig, DeploymentConfig
from remote_watch.gateway_server.manager import GatewayManager

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Плановое сообщение не ждёт cooldown или grace_period
#------------------------------------------------------------------------------------------------------------------
def test_stop_notice(
    caplog: pytest.LogCaptureFixture,
    ) -> None:

    """
    Emit one neutral stop notice immediately after a failure despite cooldown and grace settings.

    :param caplog: Capture local messages without a provider or any real credentials.
    :type caplog: pytest.LogCaptureFixture
    """

    # caplog — проверяется постановка сообщения, а не доставка на телефон.

    alerts = OperatorAlerts(ApplicationConfig(cooldown=86400, grace_period=3600))
    alerts.logger.addHandler(caplog.handler)
    alerts.observe("notifications", "failed", "storage", 0)
    alerts.notify_stopping()
    alerts.notify_stopping()

    assert len(caplog.records) == 2
    message = caplog.records[-1].getMessage()
    assert message == "Remote Watch gateway\nGateway server is stopping (planned shutdown)."
    assert "needs attention" not in message and "ready again" not in message
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Откат не выдаётся за плановый stop; отказ оповещения не мешает очистке
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("planned,send_failed,close_failed", [
    (False, False, False), (True, False, False), (True, True, False), (True, False, True)])
def test_shutdown_cleanup(
    tmp_path: Path,
    planned: bool,
    send_failed: bool,
    close_failed: bool,
    ) -> None:

    """
    Complete local shutdown even if optional owner notification enqueue or draining fails.

    :param tmp_path: Isolated paths without running components or native service operations.
    :type tmp_path: Path

    :param planned: Whether an operator requested a graceful shutdown.
    :type planned: bool

    :param send_failed: Inject a local notification enqueue failure.
    :type send_failed: bool

    :param close_failed: Inject a bounded delivery cleanup timeout.
    :type close_failed: bool
    """

    # tmp_path — журналы и реальные service/control файлы не открываются.
    # planned/send_failed/close_failed — независимые ветки shutdown, без ожидания сети.

    config = DeploymentConfig(endpoint="https://gateway.example.test:8443",
        control_dir=tmp_path / "control", log_dir=tmp_path / "logs",
        notifications=ComponentConfig(tmp_path / "notifications.json", 9765))
    manager = GatewayManager(config)
    manager._report = Mock()
    manager._alerts = SimpleNamespace(
        notify_stopping=Mock(side_effect=RuntimeError("private send failure") if send_failed else None),
        close=AsyncMock(side_effect=TimeoutError("private network failure") if close_failed else None))
    manager._runner = SimpleNamespace(cleanup=AsyncMock())
    handler = Mock()
    manager._log.addHandler(handler)

    asyncio.run(manager._shutdown(planned=planned))

    assert manager.state == "stopped" and manager.stop_event.is_set()
    assert manager._alerts.notify_stopping.call_count == int(planned)
    assert manager._alerts_failed == (send_failed or close_failed)
    manager._alerts.close.assert_awaited_once_with()
    manager._runner.cleanup.assert_awaited_once_with()
    handler.close.assert_called_once_with()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Запускайте тесты через pytest.")
#------------------------------------------------------------------------------------------------------------------
