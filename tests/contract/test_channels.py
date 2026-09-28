# Проверка структурного async-контракта на fake-канале без наследования.
# Класс MemoryChannel: Тестовая реализация без provider dependencies.
#   -> __init__(): Создать локальное тестовое состояние.
#   -> open(): Зафиксировать loop.
#   -> send(): Принять одно задание в том же loop.
#   -> close(): Зафиксировать завершение.
# Функция exercise_channel(): Проверить lifecycle и результат отправки.
# Тест test_structural_async_channel_contract(): Проверить фабрику и протокол.
#
# Version 1.0.0
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
# Дата и время последнего изменения: 260928-110519

#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
import asyncio

from remote_watch import Delivery, DeliveryResult, DeliveryStatus, Destination, Notification, NotificationChannel

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Fake-канал с независимой реализацией
#------------------------------------------------------------------------------------------------------------------
class MemoryChannel:
    """Implement the channel protocol without importing any concrete provider."""

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Создать локальное состояние тестового канала
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Initialize state without creating an event loop or network client."""

        self.loop: asyncio.AbstractEventLoop | None = None
        self.deliveries: list[Delivery] = []
        self.closed = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Зафиксировать loop владельца
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Bind the fake to the running owner loop."""

        self.loop = asyncio.get_running_loop()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Одна тестовая попытка
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Record exactly one delivery attempt in the owner loop.

        :param delivery: Immutable attempt.
        :type delivery: Delivery

        :return: Synthetic provider acceptance.
        :rtype: DeliveryResult
        """

        # delivery - задание тестовой отправки.

        assert asyncio.get_running_loop() is self.loop
        assert not self.closed
        self.deliveries.append(delivery)
        return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, provider_message_id="fake-1")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Завершение тестового клиента
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Record cleanup in the same loop."""

        assert asyncio.get_running_loop() is self.loop
        self.closed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка общей границы канала
#------------------------------------------------------------------------------------------------------------------
async def exercise_channel(
    channel: NotificationChannel,
    notification: Notification,
    ) -> None:

    """Exercise async lifecycle using only the structural protocol.

    :param channel: Any structural channel implementation.
    :type channel: NotificationChannel

    :param notification: Immutable snapshot fixture.
    :type notification: Notification
    """

    # channel - реализация протокола без наследования.
    # notification - снимок для отправки.

    await channel.open()
    try:
        result = await channel.send(Delivery(notification=notification, destination_id="fake", delivery_id="d1"))
        assert result.status is DeliveryStatus.PROVIDER_ACCEPTED
    finally:
        await channel.close()
#------------------------------------------------------------------------------------------------------------------

#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Независимый fake соответствует async-протоколу
#------------------------------------------------------------------------------------------------------------------
def test_structural_async_channel_contract(
    notification: Notification,
    ) -> None:

    """Use a class factory and exercise the protocol without provider dependencies.

    :param notification: Immutable snapshot fixture.
    :type notification: Notification
    """

    # notification - снимок тестового события.

    destination = Destination(destination_id="fake", channel_factory=MemoryChannel)
    channel = destination.channel_factory()
    asyncio.run(exercise_channel(channel, notification))
    assert len(channel.deliveries) == 1
    assert channel.closed
#------------------------------------------------------------------------------------------------------------------
