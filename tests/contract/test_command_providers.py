# Проверки Telegram и закрытых топиков ntfy через локальный HTTP.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-184110
#
# Тесты:
# -> update(): Подставное исходное сообщение Telegram.
# -> test_provider_http(): Локальный HTTP, подтверждения и ограничения провайдеров.
# -> test_telegram_authenticated_fields(): Исходные сообщения людей без подмены отправителя.
# -> test_ntfy_private_config(): Запрет публичного топика и неоднозначных настроек.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("aiohttp")
from aiohttp import web

from remote_watch.adapters.ntfy_commands import NtfyCommandConfig, NtfyCommandProvider
from remote_watch.adapters.telegram_commands import TelegramCommandConfig, TelegramCommandProvider
from remote_watch.commands.transport import CommandError

TOKEN = "12345:" + "a" * 35


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подставное исходное сообщение Telegram
#------------------------------------------------------------------------------------------------------------------
def update(number: int = 17) -> dict[str, Any]:

    """Build one authentic-shaped human message without actual provider credentials.

    :param number: Synthetic command and provider event number.
    :type number: int

    :return: Validated configuration, synthetic provider object or aggregate service counters.
    :rtype: dict[str, Any]
    """

    # number — номер подставной команды и события провайдера.

    return {"update_id": number, "message": {"date": 1000, "from": {"id": 42, "is_bot": False},
        "chat": {"id": -123, "type": "group"}, "text": "/rw loader resume_load"}}
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Локальный HTTP, подтверждения и ограничения провайдеров
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", [
    "normal", "webhook", "week_reset", "rate_limit", "private_topic", "public_topic",
])
def test_provider_http(kind: str) -> None:

    """Exercise real local HTTP framing, provider acknowledgement and private-topic refusal.

    :param kind: Selected synthetic failure or provider scenario.
    :type kind: str
    """

    # kind — выбранный подставной сценарий провайдера или отказа.


    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Изолированный асинхронный сценарий проверки
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Run a finite mock API with the actual optional HTTP transport."""

        calls = []
        queue = [update()]
        is_ntfy = kind in ("private_topic", "public_topic")

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Ответ подставного локального API
        #----------------------------------------------------------------------------------------------------------
        async def handle(request: web.Request) -> web.Response:

            """Record requests while returning only synthetic provider data.

            :param request: Incoming request validated before dispatch.
            :type request: web.Request

            :return: Synthetic HTTP response for the local provider test.
            :rtype: web.Response
            """

            # request — входящий запрос, проверяемый перед обработкой.

            payload = await request.json() if request.method == "POST" else dict(request.query)
            calls.append((request.path, payload, request.headers.get("Authorization")))
            method = request.path.rsplit("/", 1)[-1]

            if is_ntfy:
                if request.method == "POST":
                    assert payload["topic"] == "private-replies"
                    return web.json_response({"event": "message", "topic": "private-replies", "id": "reply-one"})
                if request.headers.get("Authorization") is None:
                    return web.Response(status=200 if kind == "public_topic" else 403)
                data = {"event": "message", "topic": "private-commands", "id": "opaque-X", "time": 1000,
                        "message": "/rw loader resume_load", "actor_id": "forged-publisher"}
                return web.Response(text=json.dumps({"event": "open"}) + "\n" + json.dumps(data) + "\n")

            if method == "getMe":
                value = {"id": 12345, "is_bot": True, "username": "test_bot"}
            elif method == "getWebhookInfo":
                value = {"url": "https://example.invalid/webhook" if kind == "webhook" else ""}
            elif method == "getUpdates":
                if "offset" in payload:
                    queue[:] = [item for item in queue if item["update_id"] >= payload["offset"]]
                value = queue[:1]
            else:
                if kind == "rate_limit":
                    return web.json_response({"ok": False, "error_code": 429,
                                              "parameters": {"retry_after": 30}}, status=429)
                assert "parse_mode" not in payload
                value = {"message_id": 5}
            return web.json_response({"ok": True, "result": value})
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handle)
        server = web.AppRunner(app)
        await server.setup()
        site = web.TCPSite(server, "127.0.0.1", 0)
        await site.start()
        endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        provider = (NtfyCommandProvider(NtfyCommandConfig(topic="private-commands", reply_topic="private-replies",
                    actor_id="owner", private_topic_confirmed=True, token="tk_" + "x" * 32,
                    endpoint=endpoint, allow_loopback_http=True)) if is_ntfy else
                    TelegramCommandProvider(TelegramCommandConfig(token=TOKEN, endpoint=endpoint,
                                                                  allow_loopback_http=True)))

        try:
            if kind in ("webhook", "public_topic"):
                with pytest.raises(CommandError, match="conflict" if kind == "webhook" else "denied"):
                    await provider.open()
                assert not any("getUpdates" in call[0] for call in calls)
                return
            await provider.open()
            events = await provider.poll("")
            assert len(events) == 1 and events[0].text == "/rw loader resume_load"
            assert events[0].actor_id == ("owner" if is_ntfy else "42")
            if kind == "week_reset":
                queue[:] = [update(3)]
                await provider.acknowledge("17")
                assert queue[0]["update_id"] == 3
                assert not any("offset" in call[1] for call in calls)
            else:
                await provider.acknowledge(events[0].event_id)
                if not is_ntfy:
                    assert queue == []
            conversation = "private-commands" if is_ntfy else "-123"
            if kind == "rate_limit":
                with pytest.raises(CommandError, match="busy"):
                    await provider.reply(conversation, "done")
                count = len(calls)
                with pytest.raises(CommandError, match="busy"):
                    await provider.poll("")
                assert len(calls) == count
            else:
                await provider.reply(conversation, "done")
        finally:
            await provider.close()
            await server.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Исходные сообщения людей без подмены отправителя
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["normal", "other_bot", "own_bot", "sender_chat", "forward_origin", "bot",
                                 "edited", "channel", "message_thread_id", "long"])
def test_telegram_authenticated_fields(kind: str) -> None:

    """Reject copied, edited, anonymous and foreign-bot messages without trusting displayed names.

    :param kind: Selected synthetic failure or provider scenario.
    :type kind: str
    """

    # kind — выбранный подставной сценарий провайдера или отказа.

    provider = TelegramCommandProvider(TelegramCommandConfig(token=TOKEN))
    provider._username = "test_bot"
    value = update()

    if kind == "other_bot":
        value["message"]["text"] = "/rw@other_bot loader resume_load"
    elif kind == "own_bot":
        value["message"]["text"] = "/rw@TEST_BOT loader resume_load"
    elif kind in ("sender_chat", "forward_origin", "message_thread_id"):
        value["message"][kind] = {}
    elif kind == "bot":
        value["message"]["from"]["is_bot"] = True
    elif kind == "edited":
        value["edited_message"] = value.pop("message")
    elif kind == "channel":
        value["message"]["chat"]["type"] = "channel"
    elif kind == "long":
        value["message"]["text"] += " x=" + "я" * 4000
    event = provider._event(value)
    assert (event.actor_id is not None) == (kind in ("normal", "own_bot", "long"))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет публичного топика и неоднозначных настроек
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["no_confirmation", "same_topic", "no_token", "http_remote"])
def test_ntfy_private_config(kind: str) -> None:

    """Require two protected streams and explicit TLS and credential configuration.

    :param kind: Selected synthetic failure or provider scenario.
    :type kind: str
    """

    # kind — выбранный подставной сценарий провайдера или отказа.

    values = {"topic": "commands", "reply_topic": "replies", "actor_id": "owner",
              "private_topic_confirmed": True, "token": "tk_" + "x" * 32}

    if kind == "no_confirmation":
        values["private_topic_confirmed"] = False
    elif kind == "same_topic":
        values["reply_topic"] = "commands"
    elif kind == "no_token":
        values.pop("token")
    else:
        values.update(endpoint="http://example.com", allow_loopback_http=True)
    with pytest.raises((ValueError, TypeError)):
        NtfyCommandConfig(**values)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_command_providers не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
