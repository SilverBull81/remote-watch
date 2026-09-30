# Адаптеры Telegram и ntfy

Version 1.0.5

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260930-122644

## Реализовано в 0.1.0

Оба канала отправляют уведомления напрямую, через собственный асинхронный HTTP-клиент.
Один вызов send выполняет один POST. Повторы, TTL и очереди остаются у NotificationRuntime.
Приём команд, relay, вложения и разбиение одного события на несколько сообщений здесь
не включаются. Подтверждение API означает приём сервисом, а не показ на телефоне.

## Установка

Из локального репозитория:

```powershell
python -m pip install ".[telegram,ntfy]"
```

Можно выбрать только один extra. Оба используют aiohttp>=3.14.3,<4; core остаётся
без обязательных зависимостей. Даже импорт provider-модулей и создание настроек
не загружают aiohttp: он потребуется при open. При отсутствии зависимости канал
сообщает, какой extra установить; runtime учитывает ошибку запуска без исходного traceback.

Выбран aiohttp: его сессия объединяет асинхронное соединение, общий таймаут запроса,
потоковое чтение и закрытие. POST не участвует во внутренних повторах запросов
после обрыва соединения. HTTPX рассматривался, но его штатная INFO-диагностика
включает полный URL, содержащий токен Telegram.
Источники: [aiohttp client](https://docs.aiohttp.org/en/stable/client_reference.html),
[логика повторов aiohttp](https://docs.aiohttp.org/en/stable/_modules/aiohttp/client.html),
[HTTPX logging](https://www.python-httpx.org/logging/).

## Токен в Python-настройках, начиная с 0.2.0.dev8

TelegramConfig и NtfyConfig принимают token либо token_env. Значение token скрыто
из repr и проверяется при создании настроек; окружение читается при open.
Например, `TelegramConfig(token="123456:REPLACE_WITH_BOT_TOKEN", chat_id=123456789)`
или `NtfyConfig(token="REPLACE_WITH_NTFY_TOKEN", topic="test-topic")`.
Это вымышленные заглушки; реальные значения не помещайте в исходники репозитория.
Два непустых источника одновременно запрещены. В Python None означает отсутствие
источника; у ntfy оба None выбирают анонимную отправку. В gateway JSON требуется
явный ключ, и анонимный режим задаётся только token_env=null.

## Настройка двух получателей

Этот пример только создаёт конфигурацию: он не читает токены и ничего не отправляет.
Названия переменных окружения можно выбирать самостоятельно.

```python
from remote_watch import Identity, RetryPolicy, Route, WatcherConfig
from remote_watch.adapters.telegram import TelegramConfig
from remote_watch.adapters.ntfy import NtfyConfig

policy = RetryPolicy(connect_timeout=3, attempt_timeout=10, max_attempts=3)
telegram = TelegramConfig(
    token_env="REMOTE_WATCH_TELEGRAM_TOKEN",
    chat_id=-123456789,  # Заменить на ID своего чата.
)
ntfy = NtfyConfig(
    endpoint="https://ntfy.example.com",
    topic="application-alerts",
    token_env="REMOTE_WATCH_NTFY_TOKEN",
    title="Состояние приложений",
    priority=3,
    tags=("computer",),
)
config = WatcherConfig(
    identity=Identity(
        service="loader", environment="production", region="ru",
        host="server-1", instance_id="loader-1",
    ),
    destinations=(
        telegram.destination("operations-chat", retry=policy),
        ntfy.destination("phone-alerts", retry=policy),
    ),
    routes=(Route(destination_ids=("operations-chat", "phone-alerts")),),
)
assert all(destination.retry is policy for destination in config.destinations)
```

Метод `destination(destination_id, retry=None, outstanding_capacity=256)` создаёт
ленивую фабрику и передаёт **одну и ту же RetryPolicy** runtime и HTTP-клиенту.
Это предпочтительный способ подключения. Если вручную задавать channel_factory,
следует передать тот же retry в TelegramChannel/NtfyChannel и Destination.
Runtime ограничивает всю попытку; адаптер отдельно ограничивает соединение.

Для подключения config к существующему logger используйте пример из
[RUNTIME.md](RUNTIME.md): создайте NotificationRuntime, добавьте runtime.handler
и войдите в sync/async контекст. После этого logger.error выбирает оба назначения.
Обычные консольный и файловый handlers продолжают работать независимо.

## Telegram

`TelegramConfig` принимает token или token_env, chat_id, endpoint, message_thread_id,
disable_notification и allow_http. Чат — ненулевой int, его строковое представление
или @имя канала. Для темы группы-форума задаётся положительный message_thread_id.
По умолчанию endpoint — https://api.telegram.org, тема не задана, звук включён.
Собственный Bot API допускается через endpoint; это не Remote Watch relay.

Используется sendMessage без parse_mode, предпросмотр ссылок выключен. Консервативный
предел — 4096 единиц UTF-16: emoji не разрезаются, некоторые тексты с emoji могут
быть сокращены раньше предела сервиса. Автоматический переход по migrate_to_chat_id
не выполняется: адрес назначения должен изменить владелец приложения.
Источник: [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage).

## ntfy

`NtfyConfig` принимает topic, token или token_env, endpoint, title, priority, tags и allow_http.
По умолчанию endpoint — https://ntfy.sh, title — Remote Watch, priority — 3, tags — ().
Приоритет задаётся числом 1–5. Допускается до 16 меток, каждая до 256 байт UTF-8;
заголовок также ограничен 256 байтами. Topic содержит 1–64 ASCII-буквы, цифры, `_`, `-`.
Это ограничения адаптера; сервер может дополнительно ограничивать публикацию.

Публикация — JSON POST в корень endpoint. Кириллица в заголовке передаётся в JSON,
а не в HTTP-заголовках. С dev6 поле message ограничено **4095 байтами UTF-8**
с сохранением целых символов; отметка сокращения входит в этот предел.
Это запас в один байт относительно документированного предела ntfy: два полевых
запуска dev5 стабильно получили HTTP 500/50001 при 4096 байтах, но HTTP 200 при 4095.
Подробности и границы вывода: [NTFY_DIAGNOSTIC.md](NTFY_DIAGNOSTIC.md).
В 0.2.0.dev2 JSON передаётся компактно, без замены кириллицы на `\u…`.
Дополнительно весь JSON-запрос ограничен 8192 байтами: это отдельный предел
`transformBodyJSON` при стандартном message-size-limit. Если экранирование или
метаданные не оставляют достаточно места, текст сокращается дальше с отметкой
`[сокращено]`. Заголовок и метки сохраняются; конфигурация, в которой они сами
не оставляют места даже для отметки, отклоняется при создании NtfyConfig.
Topic и отображаемые tags берутся из настройки канала, а не из LogRecord.
Источники: [ntfy JSON publishing](https://docs.ntfy.sh/publish/#publish-as-json),
[transformBodyJSON](https://github.com/binwiederhier/ntfy/blob/main/server/server.go).
Свой сервер с уменьшенными лимитами может отклонить и такой запрос.

token либо token_env включает Bearer-авторизацию; оба None в Python-настройках
выбирают анонимную публикацию. Для рабочего применения
рекомендуется закрытый topic с отдельными правами отправителя и телефона.
Basic Auth в этой версии не поддерживается; используйте access token.
Источники: [ntfy authentication](https://docs.ntfy.sh/publish/#authentication),
[настройка доступа](https://docs.ntfy.sh/config/#access-control).

## Текст, ошибки и ресурсы

Перед сообщением выводятся service, environment, region, host, instance_id,
уровень, logger, время UTC и event/session/delivery IDs. Затем идут сообщение
и описание исключения. При усечении добавляется `[сокращено]`; исходное Notification
не меняется. Длинное сообщение может вытеснить конец traceback. Текст уже должен
пройти редактор приложения; адаптер не ищет произвольные секреты в рабочих логах.

| Ответ/ошибка | Результат |
| --- | --- |
| Проверенное подтверждение с message ID | provider_accepted |
| HTTP/API 429 | rate_limited; retry-after из заголовка или параметров Telegram |
| 408, 5xx | transient_failure |
| Другой 4xx, любой redirect | permanent_failure |
| Ошибка сертификата/TLS | permanent_failure |
| Не удалось установить соединение | transient_failure |
| Обрыв после начала запроса, таймаут чтения, неверное подтверждение | unknown |
| Ответ больше 64 KiB | unknown; чтение прекращается |
| Отмена вызывающей задачи | CancelledError передаётся runtime |

Retry-After поддерживает число секунд и HTTP-дату; отрицательные, бесконечные
и непонятные значения игнорируются. Если Telegram задаёт задержку ещё и в JSON,
берётся большая из двух. Тело ошибки и исходное исключение не входят в DeliveryResult.
Runtime может повторить unknown, поэтому возможны дубликаты.

Клиент создаётся в open в рабочем loop и закрывается там же. Импорт, конструктор
и open не отправляют проверочных запросов. Использование из другого loop отклоняется;
после close канал не открывается заново. Одно назначение получает отдельный клиент
с одним соединением, send вызывается последовательно владельцем-runtime.

По умолчанию требуется HTTPS. HTTP разрешается только через `allow_http=True`;
это не отключает проверку сертификатов для HTTPS. Credentials, query и fragment
в endpoint запрещены; base path допускает буквы ASCII, цифры, `/`, `_`, `-`.
Redirect, cookies, proxy из окружения и автоматическая распаковка ответа отключены.
Настройка proxy и пользовательского центра сертификации пока не добавлена.
Токен читается только при open и не хранится в dataclass/repr. Для смены токена
нужно создать новый канал/runtime. Локальный журнал и traceback самого приложения
остаются под контролем приложения.

## Проверки и реальная отправка

Для ручной проверки теперь достаточно одной команды из корня проекта:

```powershell
python -m remote_watch.smoke telegram
python -m remote_watch.smoke ntfy
```

Запускайте нужную строку отдельно: каждая отправляет одно сообщение выбранному сервису.
Формат `credentials.local.json` и безопасное чтение: [SMOKE.md](SMOKE.md).
Пошаговая настройка access control и токена: [NTFY_SETUP.md](NTFY_SETUP.md).

Offline-проверки с extras:

```powershell
python -m pip install -e ".[dev,telegram,ntfy]"
python -m pytest -q
```

Contract tests используют подменённые HTTP-сессии и локальный loopback-сервер.
Без aiohttp соответствующие тестовые модули пропускаются; core-тесты продолжаются.
Live-тесты по умолчанию пропущены даже при наличии токенов в окружении.

Для отдельного live run заранее задаются REMOTE_WATCH_TELEGRAM_TOKEN и
REMOTE_WATCH_TELEGRAM_CHAT либо REMOTE_WATCH_NTFY_TOKEN и REMOTE_WATCH_NTFY_TOPIC.
Для своего ntfy дополнительно задайте REMOTE_WATCH_NTFY_ENDPOINT.
Затем явно включите отправку:

```powershell
$env:REMOTE_WATCH_LIVE = "1"
python -m pytest tests/integration/test_live_adapters.py -m integration -q
Remove-Item Env:REMOTE_WATCH_LIVE
```

Тест без настроек конкретного провайдера будет пропущен. Используются только
синтетические данные, один POST на выбранный сервис. Сами токены не записываются
в отслеживаемые файлы проекта. 28.09.2026 пользователь подтвердил получение
обоих smoke на телефоне. В ntfy используется бесплатный аккаунт без резервирования
темы: получение подтверждено, закрытый доступ не проверен.
Приём сервисом нужно сопоставить с получением на Android;
полевой сценарий описан в [CHANNELS.md](CHANNELS.md).
