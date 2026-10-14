# Remote Watch

Version 1.4.6

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-202944

Remote Watch подключает обычный Python `logging.Logger` к уведомлениям в Telegram
и ntfy и позволяет управлять приложением через собственные команды в мессенджере.
Можно оставить только локальные журналы, добавить уведомления или подключить оба канала.

Python 3.10+, Windows и Linux. Core не имеет обязательных runtime-зависимостей;
адаптеры и Windows UI устанавливаются через extras. Уведомления отправляются фоном
через ограниченные очереди. Команды адресуются конкретному приложению/экземпляру
и проверяются по правам; произвольный shell не предоставляется.

## Клиент: первые уведомления

Установка из репозитория в виртуальное окружение:

```console
python -m venv .venv
.venv\Scripts\python.exe -m pip install "remote-watch[telegram] @ git+https://github.com/SilverBull81/remote-watch.git"
```

В Linux вместо `.venv\Scripts\python.exe` используйте `.venv/bin/python`.
Следующие команды также выполняйте Python созданного venv. Для production закрепляйте
проверенный commit/tag в Git URL через `@<commit-or-tag>`.
Задайте `APP_TELEGRAM_TOKEN` и `APP_TELEGRAM_CHAT_ID` в окружении приложения:

```python
import logging
import os

from remote_watch import ConsoleConfig, Identity, RemoteWatcher, Route, WatcherConfig
from remote_watch.adapters.telegram_notifications import TelegramConfig

logger = logging.getLogger("my-app")
logger.setLevel(logging.INFO)
destination = TelegramConfig(
    token_env="APP_TELEGRAM_TOKEN",
    chat_id=os.environ["APP_TELEGRAM_CHAT_ID"],
    display_mode="compact",
).destination("phone")
config = WatcherConfig(
    identity=Identity(service="my-app", environment="production", region="region-1",
                      host="app-host", instance_id="one"),
    destinations=(destination,),
    routes=(Route(destination_ids=("phone",), min_level=logging.WARNING),),
)
with RemoteWatcher(config, logger=logger, console=ConsoleConfig()):
    logger.info("Application started")
    logger.warning("An event needs attention")
```

INFO останется в консоли, WARNING также отправится в Telegram. Уровень исходного
logger задаёт приложение. Создание watcher не запускает сеть: её запускает вход
в контекст либо явный `start()`; завершение выполняется через `stop()`.

Для ntfy установите extra `ntfy` и используйте `NtfyConfig`. Подключение callbacks
`status`/`resume`/`suspend` и JSON командного клиента: [команды](docs/COMMANDS.md).
Полный пример жизненного цикла: [клиент](docs/CLIENT.md).

## Сервер: необязательный компонент

Для прямых Telegram/ntfy-уведомлений свой сервер не нужен. Gateway полезен, если
нужна централизованная маршрутизация или у приложения нет прямого доступа к провайдеру.
Встроенный приём команд из мессенджеров работает через command gateway.

```console
python -m pip install "remote-watch[gateway,commands,telegram,ntfy] @ git+https://github.com/SilverBull81/remote-watch.git"
python -m remote_watch.gateway_server.deploy --target ./gateway --endpoint https://gateway.example.test:8443 --dry-run
```

После проверки плана повторите deploy без `--dry-run`, заполните локальные JSON
и настройте HTTPS. Deploy создаёт комплект, но ничего не запускает и не перезаписывает
существующие файлы. Инструкции: [gateway](docs/GATEWAY.md), [TLS](docs/TLS.md),
[службы](docs/SERVICES.md), [диагностика](docs/OPERATIONS.md).

[Все инструкции и конфиги](docs/README.md) · [План дальнейших работ](docs/BACKLOG.md)

Уведомления не являются постоянной очередью: при переполнении, истечении TTL или
остановке возможны потери. Приём сообщения провайдером не означает показ на телефоне.
Неопределённый исход команды сохраняется как UNKNOWN; callback автоматически не повторяется.
Лицензия — [MIT](LICENSE), включая поставляемую иконку проекта в пределах прав
владельца; зависимости сохраняют собственные лицензии. Публичный prod-релиз пока
не объявлен.
