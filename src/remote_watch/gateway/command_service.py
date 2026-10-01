# Совместный запуск hub, источников команд и постоянных журналов.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-184110
#
# Классы:
# -> CommandGateway: Владелец сервера, времени и всех источников команд.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> start(): Открытие ресурсов до начала приёма команд.
#    -> stats(): Чтение счётчиков и состояния источника.
#    -> close(): Остановка фоновой работы и закрытие принадлежащих объекту ресурсов.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import ssl
from dataclasses import asdict
from secrets import token_hex
from typing import Any

from remote_watch.adapters.ntfy_commands import NtfyCommandConfig, NtfyCommandProvider
from remote_watch.adapters.telegram_commands import TelegramCommandProvider
from remote_watch.adapters.timeapi import TimeApiTimeSource
from remote_watch.commands._worker import StoreWorker
from remote_watch.commands.hub import CommandHub
from remote_watch.commands.source import CommandSourceRunner
from remote_watch.commands.source_store import ProcessLock, SourceJournal
from remote_watch.commands.sqlite_store import SQLiteCommandStore
from remote_watch.commands.storage import StoreRole
from remote_watch.commands.time import TimeSource, TrustedClock
from remote_watch.gateway.command_config import CommandGatewayConfig
from remote_watch.gateway.command_server import CommandHubServer


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Владелец сервера, времени и всех источников команд
#------------------------------------------------------------------------------------------------------------------
class CommandGateway:
    """Own the command listener, trusted time, durable journals and explicitly enabled provider readers."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: CommandGatewayConfig,
        *,
        time_source: TimeSource | None = None,
    ) -> None:

        """Compose resources lazily while retaining command-only authorization boundaries.

        :param config: Explicit credentials, access rules and finite limits.
        :type config: CommandGatewayConfig

        :param time_source: Explicit UTC provider; None selects the agreed TimeAPI source.
        :type time_source: TimeSource | None
        """

        # config — явные учётные данные, права и конечные пределы.
        # time_source — источник UTC; None выбирает согласованный TimeAPI.

        self.config = config
        epoch = token_hex(16)
        store = SQLiteCommandStore(config.state_dir / "hub.sqlite", owner_id="command-hub",
                                   generation=epoch, role=StoreRole.HUB)
        self.hub = CommandHub(config.hub, store, epoch=epoch, trusted_clock=TrustedClock(),
                              time_source=TimeApiTimeSource() if time_source is None else time_source)
        self.server = CommandHubServer(self.hub)
        self._owner = StoreWorker(ProcessLock(config.state_dir / "gateway.lock"), 3.0)
        self.sources: list[CommandSourceRunner] = []
        self._started = self._closed = False

        for binding in config.providers:
            provider = (NtfyCommandProvider(binding.settings) if isinstance(binding.settings, NtfyCommandConfig)
                        else TelegramCommandProvider(binding.settings))
            policy = next(source for source in config.hub.sources if source.source_id == binding.source_id)
            journal = SourceJournal(config.state_dir / (binding.source_id + ".source.sqlite"))
            self.sources.append(CommandSourceRunner(self.hub, policy, config.targets, provider, journal))
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие ресурсов до начала приёма команд
    #--------------------------------------------------------------------------------------------------------------
    async def start(
        self,
        *,
        host: str = '127.0.0.1',
        port: int = 8766,
        ssl_context: ssl.SSLContext | None = None,
        allow_loopback_http: bool = False,
    ) -> None:

        """Acquire deployment ownership and validate every reader before activating any command source.

        :param host: Explicit listener address.
        :type host: str

        :param port: Listener port; zero requests a free local port.
        :type port: int

        :param ssl_context: TLS context; clients must require certificate and hostname verification.
        :type ssl_context: ssl.SSLContext | None

        :param allow_loopback_http: Explicit permission for plaintext numeric loopback only.
        :type allow_loopback_http: bool
        """

        # host — явный адрес прослушивания сервера.
        # port — порт сервера; ноль выбирает свободный локальный порт.
        # ssl_context — контекст TLS; клиент обязан проверять сертификат и имя сервера.
        # allow_loopback_http — явное разрешение HTTP только на числовом loopback-адресе.

        if self._started or self._closed:
            raise RuntimeError("command gateway closed or started")

        try:
            await self._owner.open()
            await self.server.start(host=host, port=port, ssl_context=ssl_context,
                                     allow_loopback_http=allow_loopback_http)
            for source in self.sources:
                await source.start(activate=False)
            for source in self.sources:
                source.activate()
            self._started = True
        except BaseException:
            await self.close()
            raise
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение счётчиков и состояния источника
    #--------------------------------------------------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:

        """Return aggregate counters and fixed error codes without source names or identities.

        :return: Validated configuration, synthetic provider object or aggregate service counters.
        :rtype: dict[str, Any]
        """

        return {"closed": self._closed, "sources": [asdict(source.stats) for source in self.sources]}
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Остановка фоновой работы и закрытие принадлежащих объекту ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Stop provider consumption before closing the hub and releasing process ownership."""

        if self._closed:
            return
        self._closed = True

        try:
            # Число sources ограничено конфигурацией. Их остановки независимы и
            # не превращают восемь последовательных сетевых timeout в общее ожидание.
            await asyncio.gather(*(source.close() for source in self.sources), return_exceptions=True)
        finally:
            try:
                await self.server.close()
            finally:
                await self._owner.close(5)
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль command_service не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
