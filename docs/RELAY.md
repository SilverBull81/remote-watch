# Relay-клиент и первая итерация 0.2

Version 1.0.9

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261005-222720

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

В RelayConfig можно передать `token="..."` вместо `token_env="..."`.
Допускается ровно один источник; значение и имя переменной исключены из repr.
Литерал проверяется при создании настроек, окружение читается при open.
Оба способа используют сервисный токен длиной 32–512 символов ASCII: буквы,
цифры, `_` и `-`. Приложение не создаёт временных переменных окружения.

## Выбор отображения на стороне приложения

С **0.4.1.dev8** режим можно задать в RelayConfig клиента. Один раз обновите
библиотеку на gateway до dev8 или новее и перезапустите notification gateway.
После этого смена режима требует только изменения конфига и перезапуска приложения;
gateway продолжает работать со своим прежним конфигом. Caddy и командный gateway
для этой настройки менять не требуется.

```python
destination = RelayConfig(
    endpoint="https://gateway.example.com",
    alias="operations-phone",
    token_env="REMOTE_WATCH_GATEWAY_TOKEN",
    display_mode="text",  # full, compact или text.
).destination("alerts")
```

Если приложение читает собственный JSON и передаёт его параметры в RelayConfig,
в тот же раздел клиента добавьте `"display_mode": "text"`. Поля совпадают с
TelegramConfig/NtfyConfig: необязательный `display_fields` выбирает группы
`identity`, `level`, `logger`, `time`, `ids`. Например, compact с источником и временем:

```json
{
  "display_mode": "compact",
  "display_fields": ["identity", "time"]
}
```

Это фрагмент настроек клиента, а не отдельный конфиг запуска gateway.
Для text удалите display_fields; для стандартного состава любого режима также
оставьте его отсутствующим/null. Если display_mode отсутствует/null, действует
серверный режим; display_fields без режима запрещён. Чтобы вернуть настройки
gateway, удалите оба поля из клиента и перезапустите приложение.

Явный клиентский режим полностью заменяет серверный выбор отображения для этой
доставки: его поля не смешиваются с серверными. Другие приложения и назначения
не затрагиваются. Identity, event/session/delivery ID остаются в данных и проверках
доступа. Client override реализован в Telegram/ntfy; собственному адаптеру нужно
учитывать Delivery.display_mode/display_fields при подготовке текста.

Для режима автоматически выбирается wire schema 3 при создании RelayConfig.
Если в прежнем клиентском конфиге явно стоит schema_version=1 или 2, **удалите
это поле либо замените значение на 3**. Старый gateway отклонит схему 3;
автоматического повтора без режима нет. Изменение JSON на диске само по себе
не меняет работающий объект: перечитывание конфигурации без перезапуска не добавлено.

## Wire-контракт: схемы 1, 2 и 3

С 0.2.0.dev5 поддерживаются схемы **1 и 2**, с 0.4.1.dev8 — также **3**,
на том же /v1/notifications. RelayConfig.schema_version=None при создании
выбирает 1 без display_mode либо 3 при заданном режиме. После проверки в объекте
хранится выбранное целое число. Без новых настроек клиент совместим со старым
gateway. Новый gateway отвечает в версии, указанной в запросе.
Для числовой диагностики без отображения можно явно задать schema_version=2.
Автоматического перехода на другую схему и дополнительного POST нет: это могло бы
повторно отправить уже принятое уведомление. Старый сервер отклоняет схему 2;
новый клиент не считает ответ другой версии подтверждением доставки.

Одна попытка — один `POST {endpoint}/v1/notifications`, JSON UTF-8, без компрессии.
Состав request envelope строгий; отсутствующие, лишние и повторяющиеся JSON-поля
отклоняются при разборе RelayRequest. Размер запроса ограничен 64 KiB.

| Поле | Содержание |
| --- | --- |
| schema_version | Целое 1, 2 или 3, bool не принимается |
| notification | Данные Notification.to_dict(), включая свою schema_version и явную identity |
| alias | Разрешённое сервером имя назначения, `[a-zA-Z][a-zA-Z0-9_-]{0,63}` |
| delivery_id / attempt | Стабильный идентификатор доставки и положительный номер попытки |
| remaining_ttl | Положительный остаток допустимого срока, секунды |
| timeout | Положительный срок обработки на gateway, не больше remaining_ttl |
| display | Только схема 3: обязательное поле null либо объект с ровно mode и fields |

В display.mode разрешены full/compact/text; display.fields — null либо JSON-массив
до пяти уникальных имён разрешённых групп. Для compact обязательна identity,
для text допустим только fields=null. Пустой/частичный объект, неизвестные поля,
дубли и неверные типы отклоняются. В схемах 1/2 display отсутствует: передача даже
display=null запрещена. Notification сохраняет собственную прежнюю схему.

Локальный destination_id не передаётся: gateway выбирает только alias, проверенный
его собственной ACL. Поля credential, provider URL и provider token отсутствуют.
Декодирование модели проверяет данные, но не заменяет серверную auth/ACL/expiry-проверку.

Во всех схемах фиксированы стандартные SnapshotLimits: message — 8192 байта UTF-8,
exception — 4096, metadata — 2048 байта компактного JSON, весь Notification — 16384.
Увеличение локальных SnapshotLimits не расширяет эти пределы. Клиент проверяет их
до POST: несовместимое событие даёт PERMANENT_FAILURE / relay_payload_limits,
без сетевой попытки и без повторов. Маленькое событие с увеличенными локальными
лимитами допускается, если его фактический размер укладывается в wire-пределы.
Проверка размера и HTTP-отправка используют одинаковый компактный JSON UTF-8.

Ответ, максимум 4 KiB: `schema_version`, `delivery_id`, `attempt`, `result`.
Result строго содержит `status`, `reason_code`, `provider_message_id`, `retry_after`;
необязательные значения передаются как null. Status использует DeliveryStatus,
поля проверяются обычным DeliveryResult. На клиенте source всегда ResultSource.RELAY.
Идентификатор доставки и номер попытки должны совпасть с отправленным запросом.

В схемах 2 и 3 result дополнительно содержит ровно четыре nullable-поля: http_status,
provider_code, message_bytes, request_bytes. Это диагностические числа провайдера,
возвращённые внутри ответа gateway, а не HTTP-код внешнего ответа 200.
При отказе самого gateway вне успешного envelope http_status, если известен,
относится к gateway. provider_code допускает 0–999999; размеры — 0..2³¹−1;
HTTP — 100–599. Текст error/description и адреса не передаются. В схеме 1 этих
ключей нет даже на новом сервере. Все схемы сохраняют предел ответа 4 KiB.

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

Сервер по умолчанию также сравнивает даты с собственными UTC-часами.
Для VM с известным расхождением в dev9 добавлен явный clock_skew_tolerance;
он ослабляет UTC-отсечение возраста, сохраняя относительные бюджеты попытки.
Старым клиентам schema 1/2 обновление для этого не требуется.
Настройка и границы: [GATEWAY_SERVER.md](GATEWAY_SERVER.md).
relay_expired означает истёкший срок; relay_clock_skew — created_at из будущего
сверх серверного допуска. Оба кода передаются внутри корректного HTTP 200 envelope,
поэтому http_status провайдера может быть null: провайдер ещё не вызывался.

Все повторы остаются у NotificationRuntime. Потерянный ответ gateway после приёма
провайдером может привести к дубликату; delivery ID пока служит для корреляции.
Дедупликация, durable receipt и восстановление после рестарта в эту итерацию не входят.

## Одноразовый smoke

Для relay также подготовлена команда `python -m remote_watch.diagnostics.smoke relay`.
Она работает с сервером 0.2.0.dev3; его principal должен разрешать синтетическую
Identity smoke, как в GATEWAY_SERVER.md. В локальном credentials-файле нужна отдельная секция:

```json
{
  "relay": {
    "endpoint": "https://gateway.example.com",
    "alias": "operations-phone",
    "schema_version": 2,
    "token": "<SERVICE_TOKEN>"
  }
}
```

Замените placeholder собственным сервисным токеном только в локальном файле.
Для gateway до dev5 удалите schema_version или задайте 1.
Команда выполняет одну попытку, не добавляет retries и не требует provider credentials.
30.09.2026 владелец подтвердил реальный RU → LV → Telegram smoke на dev9:
provider_accepted и немедленное появление сообщения на телефоне. Длительный mixed
пока не проверен. [Свидетельства и границы](REVIEW_0_1_0_2.md).

## Следующий шаг

Начинается проектирование контура команд 0.3: проверка состояния и resume/suspend.
Эксплуатационные проверки relay продолжаются отдельно: длительный mixed,
автозапуск/reboot, восстановление связи и проверка ingress-ограничений.
Библиотечные проверки auth/ACL, overload, expiry, потери ответа и смешанного режима
выполнены offline. В 0.3.3.dev1 командные endpoints реализованы отдельным
CommandHubServer и не включаются настройкой relay: [COMMAND_HUB.md](COMMAND_HUB.md).

## Явный контекст доверия TLS

С 0.4.1.dev5 RelayConfig принимает ssl_context: ssl.SSLContext | None.
None сохраняет стандартное доверие Python/aiohttp. Приложение может создать
ssl.create_default_context(), добавить свой CA через load_verify_locations
и передать контекст в RelayConfig. Переменные окружения не меняются; TLS
проверяет цепочку, срок и имя сервера. CERT_REQUIRED и check_hostname=True
обязательны; адаптер повторно проверяет их перед созданием HTTP-клиента.
Контекст исключён из repr и не передаётся в wire JSON. После передачи контекст
нельзя изменять, особенно во время запросов. Gateway обновления не требует:
параметр влияет только на HTTPS-соединение приложения с gateway.
