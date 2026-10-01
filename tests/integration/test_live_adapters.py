# Явно включаемая проверка доставки одного синтетического сообщения реальному сервису.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Тесты:
# -> test_live_notification(): Явно разрешённая отправка реальному сервису.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import os

import pytest

from remote_watch import Delivery, DeliveryStatus, Notification
from remote_watch.adapters.ntfy import NtfyChannel, NtfyConfig
from remote_watch.adapters.telegram import TelegramChannel, TelegramConfig

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("REMOTE_WATCH_LIVE") != "1", reason="set REMOTE_WATCH_LIVE=1 explicitly",
)]


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Явно разрешённая отправка реальному сервису
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["telegram", "ntfy"])
def test_live_notification(
    provider: str,
    notification: Notification,
) -> None:

    """Send one synthetic notification only when live execution is explicitly enabled.

    :param provider: Provider selected by the test.
    :type provider: str

    :param notification: Immutable notification fixture.
    :type notification: Notification
    """

    # provider - сервис, выбранный для проверки.
    # notification - уведомление с заданными тестовыми данными.

    if provider == "telegram":
        if not os.environ.get("REMOTE_WATCH_TELEGRAM_CHAT") or not os.environ.get("REMOTE_WATCH_TELEGRAM_TOKEN"):
            pytest.skip("Telegram credentials are not configured")
        channel = TelegramChannel(TelegramConfig(token_env="REMOTE_WATCH_TELEGRAM_TOKEN",
            chat_id=os.environ["REMOTE_WATCH_TELEGRAM_CHAT"]))
    else:
        if not os.environ.get("REMOTE_WATCH_NTFY_TOPIC") or not os.environ.get("REMOTE_WATCH_NTFY_TOKEN"):
            pytest.skip("ntfy credentials are not configured")
        channel = NtfyChannel(NtfyConfig(token_env="REMOTE_WATCH_NTFY_TOKEN",
            topic=os.environ["REMOTE_WATCH_NTFY_TOPIC"],
            endpoint=os.environ.get("REMOTE_WATCH_NTFY_ENDPOINT", "https://ntfy.sh")))

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Close the client even when the live provider rejects the request."""

        try:
            await channel.open()
            result = await channel.send(Delivery(notification=notification,
                destination_id="live-test", delivery_id="synthetic-live-test"))
            assert result.status is DeliveryStatus.PROVIDER_ACCEPTED, result
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.integration.test_live_adapters не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
