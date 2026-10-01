# Приём исходных сообщений Telegram с проверкой отправителя и чата.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-184110
#
# Классы:
# -> TelegramCommandConfig: Настройки единственного получателя сообщений bot.
#    Специальные методы:
#    -> __post_init__(): Проверка и фиксация настроек.
#
# -> TelegramCommandProvider: Получатель исходных пользовательских сообщений Telegram.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие ресурсов и проверка владения.
#    -> poll(): Получение ограниченной порции неподтверждённых событий.
#    -> acknowledge(): Подтверждение только записанного события.
#    -> reply(): Одна попытка отправить ответ проверенному получателю.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.
#    Служебные методы:
#    -> _call(): Один запрос Bot API с проверкой результата.
#    -> _event(): Выделение проверенного отправителя исходного сообщения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import re
from dataclasses import dataclass, field

from remote_watch._credentials import resolve_token, validate_credentials
from remote_watch.adapters._source_http import SourceHttp
from remote_watch.commands.source_protocol import SourceEvent
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки единственного получателя сообщений bot
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class TelegramCommandConfig:
    """Configure one exclusive Telegram Bot API reader without borrowing outbound permissions."""

    token: str | None = field(default=None, repr=False)     # Токен bot, скрытый из repr.
    token_env: str | None = None                            # Альтернатива literal-токену; читается при open.
    endpoint: str = "https://api.telegram.org"              # Явный адрес Bot API.
    allow_loopback_http: bool = False                       # HTTP разрешён только для локальных тестов.


    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка и фиксация настроек
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate credentials and the fixed provider endpoint without opening it."""

        validate_credentials(self.token, self.token_env, "telegram")
        SourceHttp(self.endpoint, allow_loopback_http=self.allow_loopback_http)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Получатель исходных пользовательских сообщений Telegram
#------------------------------------------------------------------------------------------------------------------
class TelegramCommandProvider:
    """Read only original user messages and confirm Telegram offsets after durable decisions."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: TelegramCommandConfig,
    ) -> None:

        """Retain validated settings without resolving credentials or importing aiohttp.

        :param config: Explicit credentials, access rules and finite limits.
        :type config: TelegramCommandConfig
        """

        # config — явные учётные данные, права и конечные пределы.

        self.config = config
        self._http = SourceHttp(config.endpoint, allow_loopback_http=config.allow_loopback_http)
        self._token: str | None = None
        self._username = ""
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов и проверка владения
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> str:

        """Verify the bot identity and refuse an existing webhook instead of deleting it.

        :return: Stable nonsecret fingerprint of the opened provider stream.
        :rtype: str
        """

        self._token = resolve_token(self.config.token, self.config.token_env, "telegram")
        await self._http.open()
        me = await self._call("getMe", {})

        if (type(me) is not dict or me.get("is_bot") is not True or type(me.get("id")) is not int
                or type(me.get("username")) is not str or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", me["username"])):
            raise CommandError("invalid")
        self._username = me["username"]
        webhook = await self._call("getWebhookInfo", {})

        if type(webhook) is not dict or webhook.get("url") != "":
            raise CommandError("conflict")
        return f"telegram:{self.config.endpoint}:{me['id']}"
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Получение ограниченной порции неподтверждённых событий
    #--------------------------------------------------------------------------------------------------------------
    async def poll(
        self,
        cursor: str,
    ) -> tuple[SourceEvent, ...]:

        """Read the first unconfirmed update without assuming IDs grow forever.

        :param cursor: Opaque identifier of the last durably processed provider event.
        :type cursor: str

        :return: Bounded batch of authenticated provider events.
        :rtype: tuple[SourceEvent, ...]
        """

        # cursor — непрозрачный ID последнего сохранённого события.

        # Отдельный ACK уже подтвердил прежний ID. Чтение без offset выдерживает
        # случайный новый update_id после недели без сообщений, включая меньший ID.
        result = await self._call("getUpdates", {"timeout": 10, "limit": 1, "allowed_updates": ["message"]})

        if type(result) is not list or len(result) > 1:
            raise CommandError("invalid")
        return tuple(self._event(value) for value in result)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение только записанного события
    #--------------------------------------------------------------------------------------------------------------
    async def acknowledge(
        self,
        cursor: str,
    ) -> None:

        """Confirm a committed update; leave any newly returned update unconfirmed for the next read.

        :param cursor: Opaque identifier of the last durably processed provider event.
        :type cursor: str
        """

        # cursor — непрозрачный ID последнего сохранённого события.

        if not cursor.isdecimal() or len(cursor) > 20:
            raise CommandError("invalid")
        head = await self._call("getUpdates", {"timeout": 0, "limit": 1, "allowed_updates": ["message"]})

        if type(head) is not list or len(head) > 1:
            raise CommandError("invalid")
        # После недели простоя старый offset нельзя повторять вслепую: новый ID
        # может оказаться меньше. Подтверждаем только реально стоящее первым событие.
        if not head or self._event(head[0]).event_id != cursor:
            return
        await self._call("getUpdates", {"offset": int(cursor) + 1, "timeout": 0, "limit": 1,
                                        "allowed_updates": ["message"]})
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправить ответ проверенному получателю
    #--------------------------------------------------------------------------------------------------------------
    async def reply(
        self,
        conversation_id: str,
        text: str,
    ) -> None:

        """Send one plain-text correlated reply to the original authenticated chat.

        :param conversation_id: Authenticated chat ID or configured private command topic.
        :type conversation_id: str

        :param text: Bounded plain text of a command or a provider reply.
        :type text: str
        """

        # conversation_id — проверенный ID чата либо закрытый топик команд.
        # text — ограниченный обычный текст команды либо ответа.

        if re.fullmatch(r"-?[0-9]{1,20}", conversation_id) is None:
            raise CommandError("invalid")
        result = await self._call("sendMessage", {"chat_id": conversation_id, "text": text,
                                                   "link_preview_options": {"is_disabled": True}})

        if type(result) is not dict or type(result.get("message_id")) is not int:
            raise CommandError("invalid")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close the provider session and discard the resolved bot credential."""

        try:
            await self._http.close()
        finally:
            self._token = None
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Один запрос Bot API с проверкой результата
    #--------------------------------------------------------------------------------------------------------------
    async def _call(
        self,
        method: str,
        payload: dict[str, object],
    ) -> object:

        """Perform one Bot API POST and validate its success envelope without exposing its description.

        :param method: Fixed HTTP method or Bot API operation.
        :type method: str

        :param payload: Bounded JSON body or a serialized pending decision.
        :type payload: dict[str, object]

        :return: Validated provider result or intercepted synthetic response.
        :rtype: object
        """

        # method — явный HTTP-метод либо операция Bot API.
        # payload — ограниченное тело JSON либо записанное решение источника.

        _, value = await self._http.call("POST", f"/bot{self._token}/{method}", payload=payload)

        if type(value) is not dict or type(value.get("ok")) is not bool:
            raise CommandError("invalid")

        if not value["ok"]:
            code = value.get("error_code")
            if code == 429:
                parameters = value.get("parameters")
                delay = parameters.get("retry_after") if type(parameters) is dict else None
                self._http.defer(delay if type(delay) is int else 2)
            raise CommandError("busy" if code == 429 else "conflict" if code == 409 else "denied")

        if "result" not in value:
            raise CommandError("invalid")
        return value["result"]
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Выделение проверенного отправителя исходного сообщения
    #--------------------------------------------------------------------------------------------------------------
    def _event(
        self,
        value: object,
    ) -> SourceEvent:

        """Accept original human messages while acknowledging unsupported Telegram update types safely.

        :param value: Untrusted configuration or provider value under validation.
        :type value: object

        :return: Bounded provider event with verified routing fields.
        :rtype: SourceEvent
        """

        # value — проверяемое значение настроек либо ответа провайдера.

        if type(value) is not dict or type(value.get("update_id")) is not int or value["update_id"] < 0:
            raise CommandError("invalid")
        event_id = str(value["update_id"])
        message = value.get("message")
        actor = chat = text = None
        date = 0

        if type(message) is dict:
            stamp = message.get("date")
            date = stamp if type(stamp) is int and stamp >= 0 else 0
            sender = message.get("from")
            conversation = message.get("chat")
            forbidden = ("sender_chat", "forward_origin", "forward_from", "forward_from_chat",
                         "is_automatic_forward", "message_thread_id", "business_connection_id")
            if (date and not any(key in message for key in forbidden) and type(sender) is dict
                    and sender.get("is_bot") is False and type(sender.get("id")) is int and sender["id"] > 0
                    and type(conversation) is dict and type(conversation.get("id")) is int
                    and conversation.get("type") in ("private", "group", "supergroup")
                    and type(message.get("text")) is str):
                candidate = message["text"]
                # Идентичность отправителя берётся только из from.id, никогда из текста.
                prefix = re.match(r"^/rw(?:@([A-Za-z0-9_]+))?(?=\s|$)", candidate)
                if prefix and (prefix[1] is None or prefix[1].lower() == self._username.lower()):
                    actor, chat = str(sender["id"]), str(conversation["id"])
                    text = "/rw" + candidate[prefix.end():]
        return SourceEvent(event_id=event_id, message_date=date, actor_id=actor, conversation_id=chat, text=text)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль telegram_commands не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
