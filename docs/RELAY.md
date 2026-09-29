# Relay-клиент и первая итерация 0.2

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260929-122613

## Готовность

В 0.2.0.dev1 реализованы RelayConfig/RelayChannel и строгий контракт RelayRequest.
В 0.2.0.dev3 добавлен сервер с аутентификацией, точными Identity/alias ACL,
ограничением нагрузки и одной provider attempt. Клиент и сервер проверены вместе
на loopback, включая смешанный direct/relay-сценарий. Реальный deployment ещё
не проверен. Настройка сервера: [GATEWAY_SERVER.md](GATEWAY_SERVER.md).
Контур команд не добавлен.

Выбран тот же репозиторий и optional extra `relay` с aiohttp. Core не требует HTTP,
relay-клиент не импортирует Telegram/ntfy и не хранит их токены/адреса назначения.
Gateway использует отдельный optional extra `gateway` в этом пакете и отдельный процесс;
выбор границ зафиксирован в [ADR 0005](adr/0005-relay-wire-and-packaging.md).

## Конфигурация клиента

```python
from remote_watch import DeliveryMode, RetryPolicy
from remote_watch.adapters.relay import RelayConfig

destination = RelayConfig(
    endpoint="https://gateway.example.com",
    alias="operations-phone",
    token_env="REMOTE_WATCH_GATEWAY_TOKEN",
).destination("alerts", retry=RetryPolicy(max_attempts=3, attempt_timeout=10))

assert destination.mode is DeliveryMode.RELAY
assert destination.destination_id == "alerts"
```

Этот пример создаёт только настройки и не обращается к gateway. Destination можно
передать в прежний WatcherConfig вместе с direct-назначением; Route выбирает прежнее
имя `alerts`. Код приложения продолжает использовать обычный logger.

Установка клиента: `python -m pip install ".[relay]"` из checkout либо аналогичный
extra при установке из GitHub. Сервисный токен — отдельная случайная строка не менее
32 символов из букв ASCII, цифр, `_` и `-`; это не Telegram token. Токен читается при
open и передаётся только в Authorization Bearer. Сервер связывает его с точной
Identity и разрешёнными aliases. Не помещайте его в endpoint, query или тело Notification.

HTTPS и проверка сертификата включены по умолчанию. Redirect не выполняется.
`allow_http=True` существует для явной локальной проверки; передача рабочего токена
по незашифрованной сети не является целевым режимом. Alias — короткое логическое имя,
а не URL или chat/topic. Сам `DeliveryMode.RELAY` не меняет произвольную фабрику канала;
согласованную пару mode/factory создаёт `RelayConfig.destination()`.

## Wire-контракт v1

Одна попытка — один `POST {endpoint}/v1/notifications`, JSON UTF-8, без компрессии.
Состав request envelope строгий; отсутствующие, лишние и повторяющиеся JSON-поля
отклоняются при разборе RelayRequest. Размер запроса ограничен 64 KiB.

| Поле | Содержание |
| --- | --- |
| schema_version | Целое 1, bool не принимается |
| notification | Данные Notification.to_dict(), включая свою schema_version и явную identity |
| alias | Разрешённое сервером имя назначения, `[a-zA-Z][a-zA-Z0-9_-]{0,63}` |
| delivery_id / attempt | Стабильный идентификатор доставки и положительный номер попытки |
| remaining_ttl | Положительный остаток допустимого срока, секунды |
| timeout | Положительный срок обработки на gateway, не больше remaining_ttl |

Локальный destination_id не передаётся: gateway выбирает только alias, проверенный
его собственной ACL. Поля credential, provider URL и provider token отсутствуют.
Декодирование модели проверяет данные, но не заменяет серверную auth/ACL/expiry-проверку.

Ответ, максимум 4 KiB: `schema_version`, `delivery_id`, `attempt`, `result`.
Result строго содержит `status`, `reason_code`, `provider_message_id`, `retry_after`;
необязательные значения передаются как null. Status использует DeliveryStatus,
поля проверяются обычным DeliveryResult. На клиенте source всегда ResultSource.RELAY.
Идентификатор доставки и номер попытки должны совпасть с отправленным запросом.

HTTP 2xx без корректного результата, неизвестная версия, повторяющиеся ключи,
несовпавшая корреляция или слишком большой ответ дают UNKNOWN. Очередь с ответом
«принято gateway, позже отправим» не поддерживается. Успех — только provider_accepted.
HTTP 401/403 означает постоянный отказ `relay_auth_denied`, 429 — RATE_LIMITED
с возможным Retry-After, 408/5xx — временную ошибку. Timeout/обрыв после POST даёт
неизвестный исход; один send не выполняет повторный запрос.

## Сроки и повторы

По умолчанию server_timeout=8 секунд, network_margin=1 секунда при attempt_timeout=10.
Сумма server_timeout и network_margin не должна превышать attempt_timeout.
Runtime перед каждой попыткой передаёт Delivery.remaining_timeout: минимум срока
попытки, остатка монотонного TTL и времени до окончания обработки при остановке.
Это дополнительное необязательное поле; существующие direct-адаптеры его игнорируют.

Клиент берёт минимум remaining_timeout, локального UTC expiry, policy TTL и
attempt_timeout. Gateway timeout дополнительно сокращается на network_margin.
Если остатка уже не хватает, POST не выполняется; возвращается постоянный отказ
`relay_budget_exhausted`. UTC не может продлить срок, рассчитанный runtime.
remaining_ttl в wire — консервативная верхняя граница для данной попытки, а не новый TTL.

Все повторы остаются у NotificationRuntime. Потерянный ответ gateway после приёма
провайдером может привести к дубликату; delivery ID пока служит для корреляции.
Дедупликация, durable receipt и восстановление после рестарта в эту итерацию не входят.

## Одноразовый smoke

Для relay также подготовлена команда `python -m remote_watch.smoke relay`.
Она работает с сервером 0.2.0.dev3; его principal должен разрешать синтетическую
Identity smoke, как в GATEWAY_SERVER.md. В локальном credentials-файле нужна отдельная секция:

```json
{
  "relay": {
    "endpoint": "https://gateway.example.com",
    "alias": "operations-phone",
    "token": "<SERVICE_TOKEN>"
  }
}
```

Замените placeholder собственным сервисным токеном только в локальном файле.
Команда выполняет одну попытку, не добавляет retries и не требует provider credentials.
Реальная отправка через удалённый gateway пока не выполнялась.

## Следующий шаг

Следующая проверка — размещение gateway на доступном сервере, TLS и лимиты входящих
соединений, затем smoke с российских машин и сверка Android. Библиотечные проверки
auth/ACL, overload, expiry, потери ответа и смешанного режима выполнены offline.
Запуск command endpoints остаётся за этапом 0.3.
