# Команды приложения из мессенджера

Version 1.0.6

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-184253

## Как устроен канал

Командный gateway читает Telegram updates либо закрытый ntfy topic, проверяет
отправителя/чат/target/scopes и хранит решения в SQLite. Приложение само подключается
к gateway по HTTPS, получает адресную команду и исполняет зарегистрированный callback.
Входящие соединения к приложению не нужны. Встроенного direct-приёма Telegram
без command gateway сейчас нет.

Ответ на команду отправляет командный канал. Он не зависит от notification Route
и может работать при отключённых удалённых логах. Канал уведомлений нужен для
событий самого приложения; канал команд — для запроса, исполнения и связанного ответа.
Один Telegram bot может обслуживать оба направления, но updates должен читать
ровно один командный источник, не несколько gateway с одинаковым bot token.

## Регистрация callbacks

Простой вариант — функции без аргументов или `functools.partial`. Они могут вернуть
строку ответа. Настоящие действия и состояние принадлежат приложению.

```python
from functools import partial
from threading import Event

from remote_watch import CommandRegistry

paused = Event()

def status():
    return "suspended" if paused.is_set() else "running"

def set_paused(event, value):
    event.set() if value else event.clear()
    return "suspended" if value else "running"

commands = CommandRegistry.from_callbacks({
    "status": status,
    "suspend_load": partial(set_paused, paused, True),
    "resume_load": partial(set_paused, paused, False),
})
```

Для контекста, проверки аргументов, описания и timeout используйте `CommandSpec`:

```python
from collections.abc import Mapping
from remote_watch import CommandContext, CommandRegistry, CommandSpec

def validate(arguments: Mapping[str, str]) -> None:
    if set(arguments) != {"count"} or not 1 <= int(arguments["count"]) <= 100:
        raise ValueError("invalid count")

def check(context: CommandContext) -> str:
    return f"Requested {context.arguments['count']} items"

commands = CommandRegistry(specs=(CommandSpec(
    name="check_load", callback=check, takes_context=True,
    validate_arguments=validate, description="Check loading state", timeout=5,
    required_scope="load:read", read_only=True, idempotent=True,
),))
```

Имена имеют вид `[a-z][a-z0-9_]{0,63}`, без начального `/`. Аргументы — строки.
`CommandContext` содержит Identity, actor/chat IDs, session ID и arguments.
`read_only`/`idempotent` описывают операцию, но не дают разрешение повторять callback.
Если задаёте свой required_scope (например load:read), разрешите его и в principal
приложения, и в actor/chat ACL источника; стандартный command:check_load его не заменяет.

## Подключение к RemoteWatcher

Скопируйте [command_client.example.json](examples/command_client.example.json)
в закрытый локальный файл. Подставьте ту же Identity и отдельный командный token,
что разрешены на gateway. CA-файл содержит **root certificate**, не leaf и не key.

```python
from remote_watch import RemoteWatcher, WatcherConfig
from remote_watch.gateway.command_config import load_command_client

client = load_command_client("command_client.local.json", commands)
config = WatcherConfig(identity=client.registration.identity, commands=commands)
with RemoteWatcher(config, command_client=client) as watcher:
    # Основной цикл приложения; здесь пример ждёт штатного завершения оператором.
    input("Press Enter to stop\n")
```

`load_command_client` создаёт новую сессию, но сохраняет указанные state_file/owner_id.
Сам callback не сериализуется. Менять набор команд нужно через новый экземпляр
приложения/регистрацию, не изменением исходного dict после создания registry.

Sync callbacks/validators работают в одном выделенном worker; async callbacks
при `astart()` работают в loop приложения. Приложение должно учитывать поток,
если callback обращается к UI, asyncio.Event или объекту с привязкой к loop.
Для async приложения используйте async lifecycle и подходящий async callback.

## Команды в Telegram

```text
/rw
/rw one
/rw one status
/rw one check_load count=10
```

`/rw` показывает доступные пользователю назначения и подсказку; `/rw one`
показывает help для назначения и полную форму вызова. Help содержит
`Allowed Bot Commands:` и строки вида `-> /status`, включая `/help`.
Встроенные ответы Remote Watch — на английском; текст callback выбирает приложение.

Для отдельного чата с единственным **статически разрешённым** target доступны
`Help` без учёта регистра, `/help`, `/status` и другие разрешённые `/command`.
При двух разрешённых targets нужен явный alias. Временно отсутствующий второй
instance не превращает команду в обращение к единственному живому instance.
Неявной рассылки всем приложениям нет. Help сам бизнес-callback не запускает.

## Права и идентификаторы

- `actor_id` — числовой Telegram ID человека, отправившего сообщение.
- `conversation_id` — ID разрешённого чата; `chat_id` используется в notification config.
- В личном чате эти числа часто равны, но обозначают разные сущности. В группе
  conversation_id относится к группе, actor_id — к человеку. Username не заменяет ID.
- `targets` связывает aliases с полной Identity; `scopes` ограничивают команды.
  Проверяются и права приложения, и права actor/chat. Описание callback не выдаёт ACL.
- Для каждого приложения/instance используйте свой командный token. Один token
  для двух principals приводит к отказу проверки. Notification token — другой секрет.

Получите IDs из доверенного собственного Telegram update/клиента. Не публикуйте bot token
через сторонние диагностические сайты. Не запускайте второй getUpdates reader параллельно
работающему gateway. До включения прав на изменения проверьте status и help.

## Закрытый ntfy

Используйте [пример ntfy](examples/command_gateway.ntfy.example.json) только с topic,
где сервер ntfy реально запрещает запись всем, кроме доверенного оператора.
Входящий ntfy event не несёт проверенного Telegram-подобного actor ID; доверие
опирается на write ACL. Публичный или только случайно названный topic не подходит.
Нужны отдельные command topic и reply topic/права. Обычные исходящие ntfy-уведомления
не дают права принимать команды. Полевая проверка private ntfy-команд отложена;
поддержку этого канала нельзя считать подтверждённой реальным deployment.

Для ntfy после проверки ACL подтвердите `private_topic_confirmed=true`.
Adapter дополнительно требует отказ анонимного чтения обеих тем, но не публикует
пробную команду для проверки write ACL. Настроенный actor_id обозначает всех
разрешённых писателей topic, а не удостоверенную личность автора. Публикуйте обычный
текст `/rw one status` в command topic; ответы приходят в reply_topic.

## Свежесть, reconnect и неизвестный исход

Для команд gateway использует TrustedClock с явно выбранным TimeAPI UTC-источником,
монотонными бюджетами и ограничением возраста. Обычные часы двух ОС могут расходиться;
намеренно отключать проверку свежести или TLS нельзя. При недоступном доверенном
времени новые команды не допускаются. Проверка: `python -m remote_watch.diagnostics.time_probe`.

После успешного начального старта клиент автоматически восстанавливается при обычном
разрыве сети, expiry или перезапуске gateway. Одна heartbeat-задача выполняет backoff
и регистрацию; ожидание зависит также от TTL сессии, поэтому 20 секунд — не гарантия.
Первый старт при недоступном сервере возвращает ошибку, а не запускает бесконечное ожидание.

Перед новой сессией должны быть урегулированы текущий callback и неподтверждённые
результаты. Совпадающий сохранённый результат подтверждается квитанцией, без повторного
callback. Конфликт исходов блокирует восстановление с `operator_action_required`.
`UNKNOWN` сохраняется для ручного разбора. Истёкший timeout не доказывает, что
callback прекратил внешнее действие. Не удаляйте SQLite, не меняйте owner_id и
не сбрасывайте cursors для обхода этой защиты. [Диагностика и сверка](OPERATIONS.md).

## Оформление ответов

`display_mode` в command client JSON выбирает full/compact/text отдельно от
уведомлений. `null` наследует default соответствующего `sources[].display_mode`.
Измените конфиг и перезапустите приложение; gateway перезапускать не нужно.
Режим фиксируется в исходном запросе: ответ старой команды не получает настройки
случайно появившейся новой сессии. Изменение оформления не меняет ACL и журнал исполнения.
