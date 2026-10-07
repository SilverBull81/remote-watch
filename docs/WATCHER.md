# Подключение RemoteWatcher к приложению

Version 1.0.2

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261007-120324

## Назначение

С 0.4.1.dev11 `file` также принимает `DailyFileConfig(directory="log", prefix="spambot")`:
файл автоматически выбирается по дню в папке `YYYY/MM. EnglishMonth`.
Часовой пояс, самостоятельный DailyFileHandler и примеры:
[ежедневные журналы](LOCAL_LOGGING.md). Прежний RotatingFileConfig сохранён.

`RemoteWatcher(config, logger=..., console=..., file=..., redactor=...)` управляет
runtime и только теми обработчиками, которые создал сам. Записи идут через
`watcher.logger` — это обычный `logging.Logger` либо переданный `LoggerAdapter`.
Статистика и состояние доступны через `watcher.runtime.stats()` и `.state`.

Если logger не передан, используется `logging.getLogger(config.identity.service)`.
Его уровень, propagation, filters и существующие handlers сохраняются. Приложение
явно выбирает уровень logger: `notify=True` не возвращает отфильтрованную запись.
При стандартной настройке logging унаследованный уровень обычно WARNING.
Создание объекта не открывает файлы, не прикрепляет handlers и не запускает потоки.
Получение именованного logger может зарегистрировать его в стандартном logging.

`start()` / `astart()` открывают запрошенные локальные журналы, запускают доставку
и подключают handlers. Повторный start работающего объекта ничего не дублирует.
`stop()` / `astop()` снимают свои handlers, завершают доставку и закрывают свои файлы.
Чужие handlers и поток консоли остаются открытыми. После stop или ошибки запуска
объект нельзя запустить снова: создайте новый RemoteWatcher. Повторный stop допустим.
При отмене async-запуска выполняется откат; отмена async-остановки дожидается очистки.

Lifecycle вызывается приложением, вне callback адаптера или redactor. Такие вызовы
из callback отклоняются до получения блокировок, чтобы не ждать собственный поток.
Не управляйте `.runtime.start/stop` отдельно, если владельцем является RemoteWatcher.
Контекстные менеджеры `with` / `async with` — рекомендуемый способ управления.
Вложенные контексты одного объекта не поддерживаются: внутренний выход остановит его.

## Локальные журналы

| Настройка | По умолчанию | Поведение |
| --- | --- | --- |
| console | None | Консоль не добавляется автоматически |
| ConsoleConfig.level | INFO | Порог локального обработчика |
| ConsoleConfig.stream | None | stderr; пользовательский поток не закрывается |
| file | None | Файл не создаётся автоматически |
| RotatingFileConfig.level | DEBUG | Logger также должен пропускать этот уровень |
| max_bytes / backup_count | 10 MiB / 3 | Положительные порог ротации и число архивов |
| format | Стандартный %-шаблон | Отдельный Formatter каждого локального обработчика |

Файл записывается в UTF-8. Каталог должен существовать; относительный путь фиксируется
при создании RotatingFileConfig. Ошибка открытия выявляется при start до сети.
Один файл принадлежит одному процессу: совместная ротация несколькими instances
не поддерживается. Порог max_bytes проверяется перед записью, а не делит длинную
запись: одна строка может превысить порог. Файл не является очередью повторной отправки.

Локальные formatter, stream и файловая система выполняют обычную синхронную работу
logging. Срок runtime ограничивает удалённую доставку; он не способен прервать зависший
локальный write/flush. Redactor применяется к удалённому тексту, а не к локальному журналу.
Метаданные и identity следует заполнять без секретов. Чужой handler, который выбрасывает
исключение, может прервать обычный обход logging до следующих handlers.

## Исполняемые примеры без сети

Следующие блоки выполняются последовательно в одном файле. Они входят в offline-тесты.
Fake-канал можно заменить назначениями Telegram/ntfy из [ADAPTERS.md](ADAPTERS.md).
Реальная отправка проверяется отдельно через [SMOKE.md](SMOKE.md).

```python
import asyncio
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_watch import (
    ConsoleConfig, Delivery, DeliveryResult, DeliveryStatus, Destination, Identity,
    RemoteWatcher, RotatingFileConfig, Route, WatcherConfig,
)


class MemoryChannel:
    """Capture a notification without network access."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def open(self) -> None:
        pass

    async def send(self, delivery: Delivery) -> DeliveryResult:
        self.messages.append(delivery.notification.message)
        return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)

    async def close(self) -> None:
        pass


def make_config(channel: MemoryChannel) -> WatcherConfig:
    return WatcherConfig(
        identity=Identity(service="watcher.example", environment="test", region="local",
                          host="example-host", instance_id="one"),
        destinations=(Destination(destination_id="phone", channel_factory=lambda: channel),),
        routes=(Route(destination_ids=("phone",)),),
    )
```

Синхронная программа: INFO остаётся локально, ERROR также попадает в канал.
TemporaryDirectory нужен только для примера; приложение задаёт свой постоянный путь.

```python
channel = MemoryChannel()
logger = logging.getLogger("watcher.example.sync")
logger.setLevel(logging.INFO)
logger.propagate = False

with TemporaryDirectory() as directory:
    watcher = RemoteWatcher(
        make_config(channel), logger=logger, console=ConsoleConfig(),
        file=RotatingFileConfig(path=Path(directory) / "application.log"),
    )
    with watcher:
        watcher.logger.info("Приложение запущено")
        watcher.logger.error("Источник временно недоступен")

    assert len(channel.messages) == 1
    assert watcher.runtime.stats().accepted == 1
    assert "Приложение запущено" in (Path(directory) / "application.log").read_text(encoding="utf-8")
    assert not logger.handlers
```

Асинхронная программа использует тот же обычный logger. Сеть работает в отдельном
потоке runtime; `astart/astop` также не блокируют цикл приложения ожиданием потока.

```python
async def main() -> None:
    channel = MemoryChannel()
    async with RemoteWatcher(make_config(channel)) as watcher:
        watcher.logger.error("Сообщение из asyncio")
        await asyncio.sleep(0)
    assert channel.messages == ["Сообщение из asyncio"]


asyncio.run(main())
```

Для существующей иерархии handler достаточно подключить к общему родителю один раз.
Здесь `notify=True` снимает только remote-порог ERROR, оставляя правила маршрутизации.

```python
parent = logging.getLogger("watcher.example.hierarchy")
parent.setLevel(logging.INFO)
parent.propagate = False
child = logging.getLogger("watcher.example.hierarchy.worker")
channel = MemoryChannel()

with RemoteWatcher(make_config(channel), logger=parent):
    child.info("Работа завершена", extra={"notify": True})

assert channel.messages == ["Работа завершена"]
assert not parent.handlers
```

`WatcherConfig.commands` по-прежнему принимает словарь callbacks или CommandRegistry.
В 0.3.4 явный `command_client=...` в конструкторе RemoteWatcher включает отдельный
исполнитель. Без клиента регистрация команд не запускает сетевую работу.
Sync callbacks работают в отдельном потоке, async требуют `astart()` в loop
приложения. `command_stats` возвращает счётчики и состояние исполнителя либо None,
если команды не включены. [Пример и ограничения](COMMAND_EXECUTION.md).
Внедрение в реальное приложение отложено до Telegram source и приёмки 0.3.5.
