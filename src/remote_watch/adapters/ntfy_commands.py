# Команды и ответы через два закрытых топика ntfy.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-184110
#
# Классы:
# -> NtfyCommandConfig: Настройки закрытых топиков команд и ответов.
#    Специальные методы:
#    -> __post_init__(): Проверка и фиксация настроек.
#
# -> NtfyCommandProvider: Получатель команд из закрытого топика.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие ресурсов и проверка владения.
#    -> poll(): Получение ограниченной порции неподтверждённых событий.
#    -> acknowledge(): Подтверждение только записанного события.
#    -> reply(): Одна попытка отправить ответ проверенному получателю.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import re
from dataclasses import dataclass, field

from remote_watch._credentials import resolve_token, validate_credentials
from remote_watch._validation import require_text
from remote_watch.adapters._source_http import SourceHttp
from remote_watch.commands.source_protocol import SourceEvent
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки закрытых топиков команд и ответов
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class NtfyCommandConfig:
    """Map a private topic's authorized writers to one explicitly configured application actor."""

    topic: str = field(repr=False)              # Закрытый топик только для команд.
    reply_topic: str = field(repr=False)        # Другой закрытый топик для ответов.
    actor_id: str                               # Локальное имя владельца права записи в командный топик.
    private_topic_confirmed: bool               # Оператор проверил запрет чужой записи и чтения обоих топиков.
    token: str | None = field(default=None, repr=False)     # Чтение команд и публикация ответов.
    token_env: str | None = None                # Альтернатива literal-токену, без чтения при конструировании.
    endpoint: str = "https://ntfy.sh"           # Доверенный сервер ntfy.
    allow_loopback_http: bool = False           # Только локальный тестовый HTTP.


    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка и фиксация настроек
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Require explicit private-topic ownership and distinct command/reply streams."""

        for value in (self.topic, self.reply_topic):
            if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None:
                raise ValueError("invalid command topic")

        if self.topic == self.reply_topic or self.private_topic_confirmed is not True:
            raise ValueError("private separate command topics required")

        if self.token is None and self.token_env is None:
            raise ValueError("command topic credential required")
        require_text(self.actor_id, "topic actor", 128)
        validate_credentials(self.token, self.token_env, "ntfy")
        SourceHttp(self.endpoint, allow_loopback_http=self.allow_loopback_http)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Получатель команд из закрытого топика
#------------------------------------------------------------------------------------------------------------------
class NtfyCommandProvider:
    """Read bounded batches from one private topic without trusting claimed authors in message text."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: NtfyCommandConfig,
    ) -> None:

        """Keep private topic configuration without creating a network client.

        :param config: Explicit credentials, access rules and finite limits.
        :type config: NtfyCommandConfig
        """

        # config — явные учётные данные, права и конечные пределы.

        self.config = config
        self._http = SourceHttp(config.endpoint, allow_loopback_http=config.allow_loopback_http)
        self._token: str | None = None
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов и проверка владения
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> str:

        """Verify anonymous reading is denied without publishing test messages or changing server ACL.

        :return: Stable nonsecret fingerprint of the opened provider stream.
        :rtype: str
        """

        self._token = resolve_token(self.config.token, self.config.token_env, "ntfy")
        await self._http.open()

        for topic in (self.config.topic, self.config.reply_topic):
            await self._http.call("GET", f"/{topic}/json", params={"poll": "1", "since": "latest"},
                                  privacy_probe=True)
        # Это проверка закрытого чтения, не доказательство write ACL. Права записи
        # явно подтверждает оператор; ntfy не передаёт удостоверенного автора сообщения.
        return f"ntfy:{self.config.endpoint}:{self.config.topic}:{self.config.reply_topic}:{self.config.actor_id}"
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Получение ограниченной порции неподтверждённых событий
    #--------------------------------------------------------------------------------------------------------------
    async def poll(
        self,
        cursor: str,
    ) -> tuple[SourceEvent, ...]:

        """Read the first bounded cache page; the source journal handles opaque IDs and redelivery.

        :param cursor: Opaque identifier of the last durably processed provider event.
        :type cursor: str

        :return: Bounded batch of authenticated provider events.
        :rtype: tuple[SourceEvent, ...]
        """

        # cursor — непрозрачный ID последнего сохранённого события.

        _, values = await self._http.call("GET", f"/{self.config.topic}/json", token=self._token,
            params={"since": cursor or "all"}, stream=True)
        result = []

        for value in values:
            if type(value) is not dict:
                raise CommandError("invalid")
            if value.get("event") != "message":
                continue
            if (value.get("topic") != self.config.topic or type(value.get("id")) is not str
                    or type(value.get("time")) is not int or value["time"] < 0):
                raise CommandError("invalid")
            text = value.get("message")
            if type(text) is not str or not text.startswith("/rw"):
                text = None
            result.append(SourceEvent(event_id=value["id"], message_date=value["time"],
                actor_id=self.config.actor_id, conversation_id=self.config.topic, text=text))
        return tuple(result)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подтверждение только записанного события
    #--------------------------------------------------------------------------------------------------------------
    async def acknowledge(
        self,
        cursor: str,
    ) -> None:

        """Leave ntfy cache intact; subsequent reads use only the locally committed message ID.

        :param cursor: Opaque identifier of the last durably processed provider event.
        :type cursor: str
        """

        # cursor — непрозрачный ID последнего сохранённого события.

        require_text(cursor, "ntfy checkpoint", 128)
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправить ответ проверенному получателю
    #--------------------------------------------------------------------------------------------------------------
    async def reply(
        self,
        conversation_id: str,
        text: str,
    ) -> None:

        """Publish a result only to the configured private reply topic, never an incoming URL.

        :param conversation_id: Authenticated chat ID or configured private command topic.
        :type conversation_id: str

        :param text: Bounded plain text of a command or a provider reply.
        :type text: str
        """

        # conversation_id — проверенный ID чата либо закрытый топик команд.
        # text — ограниченный обычный текст команды либо ответа.

        if conversation_id != self.config.topic:
            raise CommandError("denied")
        _, result = await self._http.call("POST", "/", token=self._token,
            payload={"topic": self.config.reply_topic, "message": text, "title": "Remote Watch: команда"})

        if (type(result) is not dict or result.get("event") != "message"
                or result.get("topic") != self.config.reply_topic or type(result.get("id")) is not str):
            raise CommandError("invalid")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close owned provider resources and discard the resolved access token."""

        try:
            await self._http.close()
        finally:
            self._token = None
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль ntfy_commands не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
