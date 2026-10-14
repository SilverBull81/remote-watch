# Уведомления

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-200855

## Прямая доставка

Один объект настройки провайдера создаёт одно назначение. Фабрика `destination()`
не отправляет сообщение: сеть открывается при старте watcher.

```python
from remote_watch.adapters.telegram_notifications import TelegramConfig
from remote_watch.adapters.ntfy_notifications import NtfyConfig

telegram = TelegramConfig(token_env="APP_TELEGRAM_TOKEN", chat_id="123456789",
                           display_mode="compact").destination("telegram-phone")
ntfy = NtfyConfig(endpoint="https://ntfy.sh", topic="replace-with-your-topic",
                  token_env="APP_NTFY_TOKEN", display_mode="text").destination("ntfy-phone")
```

Передайте назначения в `WatcherConfig.destinations`, а их IDs — в `Route`.
Telegram token выдаёт BotFather; начните личный разговор с ботом либо добавьте его
в нужный чат. `chat_id` обозначает чат; для темы форума есть `message_thread_id`.
Бот для отправки не обязан читать входящие сообщения.

Для ntfy установите клиент на телефоне и подпишитесь на тот же endpoint/topic.
Topic — адрес публикации, а не гарантированная приватность. Непредсказуемое имя
без настроенного ACL не заменяет авторизацию. Для приватности настройте учётную
запись, резервирование/ACL согласно возможностям выбранного ntfy-сервера.
Прямая публикация в публичный topic возможна без token; для команд это неприемлемо.
Инструкции самого ntfy: [authentication](https://docs.ntfy.sh/config/#access-control).

Telegram и ntfy допускают либо `token="..."`, либо `token_env="NAME"`; одновременно
указывать оба нельзя. Закрытый файл с literal token допустим, если права файла
ограничены. Значения не должны попадать в Git, командную строку или логи.

## Учётная запись и token ntfy

Для ntfy.sh создайте учётную запись и access token в её настройках, если публикация
требует авторизации. Возможность закрыть/зарезервировать topic зависит от плана сервиса;
аккаунт сам по себе не делает topic приватным. Сохраните token в окружении приложения
или закрытом JSON и подпишитесь на topic в Android-клиенте под нужным аккаунтом.

На собственном ntfy настройте HTTPS, auth-file и `auth-default-access: deny-all`.
CLI выполняется **на ntfy-сервере**, а не произвольном клиенте:

```console
ntfy user add app-writer
ntfy access app-writer app-alerts rw
ntfy token add app-writer
```

Передайте выданный token в NtfyConfig; отдельно разрешите чтение пользователю телефона.
Для команд права topic/reply_topic разделяются, как описано в [COMMANDS.md](COMMANDS.md).

## Через gateway: клиент

Приложение знает только адрес шлюза, отдельный сервисный token и разрешённый alias.
Bot token/chat/topic остаются на сервере. У каждого назначения независимо выбирается
direct или gateway; автоматического переключения между ними нет.

```python
import ssl
from remote_watch.adapters.notification_gateway import NotificationGatewayClientConfig

tls = ssl.create_default_context(cafile="secrets/root-ca.crt")
destination = NotificationGatewayClientConfig(
    endpoint="https://gateway.example.test:8443",
    alias="app1-telegram",
    token_env="APP_NOTIFICATION_GATEWAY_TOKEN",
    ssl_context=tls,
    display_mode="compact",
).destination("phone")
```

После передачи SSLContext каналу не меняйте его настройки; для другой политики
создавайте отдельный context.

При публичном доверенном сертификате используйте системное доверие: `ssl_context`
можно не передавать. Проверки сертификата и имени остаются включены.
Alias должен быть разрешён серверному principal с той же полной Identity.
Изменять вызовы logger при переходе с direct на gateway не требуется.

Режим gateway не превращает маршрут в постоянную очередь. `provider_accepted`
означает принятие сообщения конечным провайдером в рамках запроса, а не постановку
в серверную durable outbox. Настройка шлюза: [GATEWAY.md](GATEWAY.md).

## Отображение на телефоне

| Режим | Вид |
| --- | --- |
| `full` | Полная техническая шапка и текст |
| `compact` | Короткая различимая Identity и текст |
| `text` | Только текст приложения |

`display_fields` выбирает группы `identity`, `level`, `logger`, `time`, `ids`.
В compact нельзя убрать identity; в text список полей не задаётся.
При нескольких instances compact помогает различать источник. Изменения
касаются отображения: event/session/delivery IDs и права остаются в протоколе.

Настройки прямого провайдера действуют на клиенте. Для gateway сервер задаёт
default, а `NotificationGatewayClientConfig.display_mode` может его заменить
для уведомлений клиента. `None` наследует серверный default. Достаточно изменить
конфиг приложения и перезапустить его; gateway продолжает работать.
Динамической перезагрузки конфигурации работающего watcher нет.
Настройки ответов на команды независимы: [COMMANDS.md](COMMANDS.md).

## Очереди, таймауты и повторы

По умолчанию ingress вмещает 1024 события, назначение — 256 незавершённых доставок,
максимум назначений — 16. Это ограничения памяти/работы, а не обещание сохранности.
Переполнение одного назначения не должно блокировать соседнее.

`RetryPolicy`: `max_attempts=3`, `connect_timeout=3`, `attempt_timeout=10`,
`backoff_base=1`, `backoff_cap=30`, `ttl=300` (все времена в секундах).
`max_attempts=1` отключает повтор. Временные отказы повторяются с jitter и учётом
Retry-After, только пока остаётся TTL. Постоянный отказ повтором не исправляется.
Неопределённый исход отправки может означать, что провайдер уже получил сообщение;
повтор уведомления допускает дубликаты. Семантика callback команд строже.

Остановка имеет конечный deadline. Недоставленные очереди после него не становятся
автоматически файлами на диске. TTL включает ожидание и повторы; абсолютной гарантии
хотя бы одной сетевой попытки нет. `runtime.health()` не делает пробных отправок.

## Собственный канал

Реализуйте async `open()`, `send(delivery) -> DeliveryResult` и `close()`.
Создайте `Destination(destination_id="custom", channel_factory=YourChannel)`.
Фабрика без аргументов возвращает отдельный канал данного назначения; его loop
и закрытие принадлежат runtime. Отдавайте фиксированную безопасную классификацию
исхода, без секретов и сырого текста исключений. Контракт типов — в
`remote_watch.notifications.channels` и `remote_watch.notifications.delivery`.
