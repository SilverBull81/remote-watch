# Проверка границ настройки адаптеров и освобождения частично созданного клиента.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-112704
#
# Тесты:
# -> test_invalid_settings(): Отклонение неверных настроек до чтения токена.
# -> test_partial_open_cleanup(): Закрытие соединителя при ошибке создания сессии.
# -> test_loop_ownership(): Запрет обращения к клиенту из чужого цикла asyncio.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from remote_watch import RetryPolicy
from remote_watch.adapters._http import HttpSender
from remote_watch.adapters.ntfy import NtfyConfig
from remote_watch.adapters.telegram import TelegramConfig

#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение неверных настроек до чтения токена
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider,settings", [
    ("telegram", {"chat_id": True}), ("telegram", {"chat_id": 0}),
    ("telegram", {"chat_id": "../chat"}), ("telegram", {"token_env": None}),
    ("telegram", {"token_env": "a=b"}), ("telegram", {"message_thread_id": 0}),
    ("telegram", {"disable_notification": 1}), ("ntfy", {"topic": "bad/topic"}),
    ("ntfy", {"topic": ""}), ("ntfy", {"topic": "t" * 65}),
    ("ntfy", {"priority": True}), ("ntfy", {"priority": 0}), ("ntfy", {"priority": 6}),
    ("ntfy", {"token_env": "x\ny"}), ("ntfy", {"title": "x" * 257}),
    ("ntfy", {"tags": tuple(str(i) for i in range(17))}), ("ntfy", {"allow_http": 1}),
    ("ntfy", {"tags": tuple(str(i) + "\x00" * 250 for i in range(16))}),
])
def test_invalid_settings(
    provider: str,
    settings: dict[str, object],
    ) -> None:

    """Reject invalid settings before any client or secret is accessed.

    :param provider: Provider selected by the test.
    :type provider: str

    :param settings: Client constructor settings.
    :type settings: dict[str, object]
    """

    # provider - сервис, выбранный для проверки.
    # settings - настройки создаваемого HTTP-клиента.

    config = TelegramConfig if provider == "telegram" else NtfyConfig
    values = {"token_env": "TEST_TOKEN", "chat_id": 1} if provider == "telegram" else {
        "token_env": None, "topic": "test",
    }
    values.update(settings)
    with pytest.raises((TypeError, ValueError)):
        config(**values)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Закрытие соединителя при ошибке создания сессии
#------------------------------------------------------------------------------------------------------------------
def test_partial_open_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:

    """Close a connector whose session constructor failed without exposing its error.

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # monkeypatch - фикстура подмены зависимостей и окружения.

    aiohttp = pytest.importorskip("aiohttp")
    connectors: list[Any] = []

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Воспроизведение ошибки при создании клиента
    #--------------------------------------------------------------------------------------------------------------
    def fail(**kwargs: Any) -> None:

        """Capture the connector before reproducing a private client error.

        :param kwargs: Captured request or client keyword arguments.
        :type kwargs: Any
        """

        # kwargs - именованные параметры запроса или клиента.

        connectors.append(kwargs["connector"])
        raise ValueError("synthetic_secret")
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(aiohttp, "ClientSession", fail)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение асинхронного сценария проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Exercise cleanup after a partially failed initialization."""

        sender = HttpSender(RetryPolicy())
        with pytest.raises(RuntimeError, match="could not be created") as caught:
            await sender.open()
        assert "synthetic_secret" not in str(caught.value)
        assert connectors[0].closed
        await sender.close()
    #--------------------------------------------------------------------------------------------------------------
    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет обращения к клиенту из чужого цикла asyncio
#------------------------------------------------------------------------------------------------------------------
def test_loop_ownership() -> None:

    """Reject use outside the original loop and leave cleanup to that owner."""

    pytest.importorskip("aiohttp")
    owner = asyncio.new_event_loop()
    other = asyncio.new_event_loop()
    sender = HttpSender(RetryPolicy())
    try:
        owner.run_until_complete(sender.open())
        with pytest.raises(RuntimeError, match="owning event loop"):
            other.run_until_complete(sender.close())
        owner.run_until_complete(sender.close())
        owner.run_until_complete(sender.close())
    finally:
        owner.run_until_complete(sender.close())
        owner.close()
        other.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_adapter_settings не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
