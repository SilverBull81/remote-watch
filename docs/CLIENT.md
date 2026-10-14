# Клиентское приложение

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-184253

## Установка

Создайте venv, активируйте его либо вызывайте его Python абсолютным путём. Установка
из клонированного репозитория: `python -m pip install ".[telegram]"`.
Установка из Git URL приведена в [README](../README.md). Публикация на PyPI пока
не предполагается этой инструкцией. Для production используйте проверенный commit/tag.

| Extra | Назначение |
| --- | --- |
| Без extras | Локальное логирование, модели и пользовательские каналы |
| `telegram` / `ntfy` | Прямые уведомления соответствующему провайдеру |
| `gateway` | Клиент/сервер шлюза уведомлений |
| `commands` | HTTPS-командный клиент и командный gateway |
| `service` | Windows-интеграция через pywin32 |
| `tray` | Windows tray; клиентскому приложению не требуется |
| `dev` | Инструменты разработки и тестовые TLS-сертификаты |

Extras можно объединять: `python -m pip install ".[gateway,commands]"`.
Для серверного приёма Telegram-команд нужны также Telegram-зависимости.

## Объекты и ответственность

`Identity` содержит пять обязательных строк: `service`, `environment`, `region`,
`host`, `instance_id`. Согласуйте их с разрешённой Identity на gateway. Это
идентификатор приложения, а не адрес сервера и не Telegram username.

`Destination` описывает получателя, очередь и retry-политику. `Route` выбирает,
какие записи отправлять. `WatcherConfig` связывает эти настройки.
`RemoteWatcher` подключает обработчики к обычному `logging.Logger` и управляет
своими ресурсами. Не меняет чужие handlers, уровень logger и propagation.

Пустой `routes` означает отсутствие удалённой отправки. `Route` по умолчанию
выбирает ERROR и выше. Настройте уровень logger явно: отфильтрованная самим logging
запись до Remote Watch не дойдёт.

## Синхронное приложение

```python
import logging

from remote_watch import ConsoleConfig, DailyFileConfig, Identity, RemoteWatcher, WatcherConfig

logger = logging.getLogger("my-app")
logger.setLevel(logging.DEBUG)
config = WatcherConfig(identity=Identity(
    service="my-app", environment="production", region="region-1", host="app-host", instance_id="one"))
watcher = RemoteWatcher(config, logger=logger, console=ConsoleConfig(),
                        file=DailyFileConfig(directory="log", prefix="my-app"))
try:
    watcher.start()
    watcher.logger.info("Application started")
    # Работа приложения; logger можно передать другим его модулям.
finally:
    watcher.stop()
```

Синхронный режим использует собственный фоновый event loop. Эквивалентный краткий
вариант — `with RemoteWatcher(...) as watcher:`. Создание объекта ничего не запускает.
У одного объекта один жизненный цикл: после завершения создавайте новый watcher.

## Асинхронное приложение

```python
import asyncio
import logging

from remote_watch import Identity, RemoteWatcher, WatcherConfig

async def main():
    logger = logging.getLogger("my-app")
    logger.setLevel(logging.INFO)
    config = WatcherConfig(identity=Identity(
        service="my-app", environment="production", region="region-1", host="app-host", instance_id="one"))
    async with RemoteWatcher(config, logger=logger) as watcher:
        watcher.logger.info("Application started")
        await asyncio.sleep(1)

asyncio.run(main())
```

Явные аналоги — `await watcher.astart()` и `await watcher.astop()`. Используется
loop приложения. Не вызывайте блокирующий `start()` вместо async lifecycle внутри
его активного loop. Остановку планируйте в `finally`; atexit — ограниченная страховка,
а не гарантия завершения очередей.

## Маршрутизация

```python
import logging
from remote_watch import Route

route = Route(destination_ids=("phone",), min_level=logging.WARNING,
              topic="operations", required_tags=("urgent",))
logger = logging.getLogger("my-app")
logger.error("Queue stalled", extra={"topic": "operations", "tags": ("urgent",)})
logger.info("Explicit notification", extra={"notify": True})
logger.error("Local only", extra={"notify": False})
```

`notify=False` запрещает удалённую отправку, но не локальные handlers.
`notify=True` разрешает обойти уровень правила; остальные условия маршрута
сохраняются. Правила также могут фильтровать поля Identity. Повторяющиеся
destination IDs объединяются; два разных ID одного чата означают две доставки.

`redactor` в RemoteWatcher обрабатывает текст удалённого уведомления. Локальные
handlers имеют собственное форматирование; они не становятся автоматически безопасными
для секретов. Не передавайте токены, пароли и private keys в сообщения logging.

## Наблюдение и ограничения

`watcher.runtime.stats()` — накопительные счётчики. `watcher.runtime.health()` —
последнее наблюдение доставки. `watcher.command_stats` — текущая готовность
подключённого command dispatcher и история его работы.
Как использовать эти данные без вечного degraded: [OPERATIONS.md](OPERATIONS.md).

Приложение отвечает за собственное расписание, повтор бизнес-операций и хранение
данных. Remote Watch не сканирует сетевые диски, не планирует рассылки и не гарантирует
доставку уведомлений после аварии процесса. Политика очередей описана в
[NOTIFICATIONS.md](NOTIFICATIONS.md), семантика команд — в [COMMANDS.md](COMMANDS.md).
