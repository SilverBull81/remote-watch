# Исходящий gateway: настройка и запуск

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260929-122613

## Реализовано в 0.2.0.dev3

Gateway принимает одну попытку по [relay-протоколу](RELAY.md), проверяет сервисный
токен, точную Identity и разрешённый alias, вызывает прямой NotificationChannel
и возвращает его результат. Telegram/ntfy используют прежние адаптеры. Контур команд
отсутствует; `/v1/commands` не существует. Проверки выполнялись на loopback с
синтетическими токенами; развёртывание на реальном сервере ещё не проверено.

Установка на машине gateway: `python -m pip install ".[gateway,telegram]"` из checkout.
Для ntfy добавьте extra ntfy. Приложению, использующему только relay, достаточно extra
relay: provider token и chat/topic ему не нужны. Ни импорт, ни создание настроек
не запускают сеть. Отдельный процесс стартует только явной командой.

## Конфигурация

В доверенном локальном модуле `gateway_settings.py` определите функцию `build_config`.
Файл с таким именем в корне проекта исключён из Git и сборки. Это исполняемая
конфигурация владельца процесса; HTTP-запрос не может выбрать модуль или функцию.

```python
from remote_watch import Identity, RetryPolicy
from remote_watch.adapters.telegram import TelegramConfig
from remote_watch.gateway_config import GatewayConfig, GatewayPrincipal


def build_config() -> GatewayConfig:
    """Build a gateway for the explicit synthetic relay smoke identity."""

    return GatewayConfig(
        destinations=(
            TelegramConfig(
                token_env="REMOTE_WATCH_TELEGRAM_TOKEN",
                chat_id=123456789,  # Замените своим chat ID только в локальном файле.
            ).destination("operations-phone", retry=RetryPolicy(max_attempts=1)),
        ),
        principals=(
            GatewayPrincipal(
                name="manual-check",
                token_env="REMOTE_WATCH_GATEWAY_TOKEN",
                identity=Identity(
                    service="remote-watch-smoke", environment="smoke", region="manual",
                    host="manual", instance_id="smoke",
                ),
                aliases=("operations-phone",),
            ),
        ),
    )
```

Пример специально разрешает Identity одноразового `remote_watch.smoke relay`.
Для рабочего приложения добавьте отдельный GatewayPrincipal с его **точными пятью
полями Identity** и отдельным токеном. Имена хоста или приложения из текста сообщения
не дают прав. Session ID может меняться при перезапуске, identity остаётся привязанной
к credential. `name` — локальный идентификатор настроек, не дополнительное поле Identity.

Сервисный токен должен быть случайным, 32–512 символов ASCII из букв, цифр, `_`, `-`.
Передайте его через окружение на gateway и клиенте; Telegram token хранится только
на gateway. Нельзя повторно использовать один сервисный токен в двух principals.
Секреты считываются при start; изменение окружения не меняет работающие права.
Для отзыва/ротации перезапустите процесс с новой конфигурацией. Два разных токена
с одинаковой Identity допустимы, например на время контролируемой ротации.

При создании GatewayConfig проверяются ссылки aliases, дубликаты имён, конечные
числа, размеры списков и способ доставки. Принимаются только DIRECT-назначения
с `max_attempts=1`. Пользовательская фабрика должна создавать отдельный прямой канал
и соблюдать async/cancellation-контракт; нельзя скрывать за ней следующий relay.

## Запуск

После задания переменных окружения запустите из каталога с `gateway_settings.py`:

```sh
python -m remote_watch.gateway gateway_settings:build_config
```

По умолчанию слушается `127.0.0.1:8765`. На другом адресе нужен TLS:

```sh
python -m remote_watch.gateway gateway_settings:build_config --host 0.0.0.0 --port 8765 --cert /secure/fullchain.pem --key /secure/privkey.pem
```

Пути замените реальными локальными путями сертификата и ключа. Без TLS разрешены
только числовые loopback-адреса; даже строка `localhost` не является обходом проверки.
Клиент проверяет сертификат и имя сервера. Для работы за reverse proxy оставьте
gateway на loopback, а в RelayConfig укажите публичный HTTPS-адрес proxy.
Forwarded-заголовки не меняют права и настройки сервера.

Для публичного размещения перед gateway нужен HTTP reverse proxy с ограничениями
соединений, чтения заголовков и общего входящего трафика. `capacity` ограничивает
**принятые gateway запросы**, а не все TCP/TLS-соединения и предварительные буферы
HTTP-парсера aiohttp. Одного backlog=64 для ограничения активных соединений недостаточно.
На proxy задайте конечные общие и per-IP пределы, тело максимум 64 KiB, сроки чтения
заголовков/тела, ограниченные буферы и запрет автоматического повтора POST к backend.
Не записывайте Authorization и тела запросов в журналы. Например, nginx предоставляет
[limit_conn](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html) и
[лимиты HTTP-запроса](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size).
Это требования к размещению, а не автоматически установленная конфигурация proxy.

Остановка: Ctrl+C; на Unix предусмотрен SIGTERM. Реально проверен Windows/Python 3.12,
обработку сигналов Unix и TLS с рабочими сертификатами нужно проверить при развёртывании.
Код выхода 1 означает безопасно скрытую ошибку настройки/запуска, 130 — Ctrl+C.
Никаких service credentials через аргументы командной строки нет.

## Ограничения обработки

| Настройка | По умолчанию | Смысл |
| --- | --- | --- |
| GatewayConfig.capacity | 32 | Одновременное чтение тел и выполнение принятых запросов, максимум 256 |
| GatewayPrincipal.capacity | 4 | То же ограничение для одного приложения, максимум 256 |
| GatewayPrincipal.min_interval | 0.2 с | Минимальная пауза между принятыми запросами приложения |
| GatewayConfig.destination_interval | 1 с | Минимальная пауза между началами отправок одного alias |
| Активная попытка на alias | 1 | Пока канал занят, новый запрос получает 429 без очереди |
| body_timeout | 2 с | Общий срок чтения тела после приёма |
| attempt_timeout | 8 с | Серверный предел одной отправки, дополнительно ограниченный клиентом и expiry |
| startup_timeout | 5 с | Общий срок подготовки всех provider channels |
| shutdown_timeout | 5 с | Общий срок дренирования, отмены и очистки |
| future_tolerance | 0 с | Допуск будущего created_at; можно задать до 30 с, expiry не продлевается |

Интервалы частоты допускают 0 для отключения и не более 3600 секунд. Это минимальная
пауза без накопления burst-кредита. Структуры учёта содержат только настроенные имена;
HTTP-клиент не создаёт новые ключи статистики. Максимум 64 назначения и 256 principals.
`Destination.outstanding_capacity` не создаёт очередь gateway: здесь один активный
канал на alias; настройка предназначена для runtime приложения.

Auth и общая/per-principal ёмкость проверяются до чтения тела. Чтение ограничено
64 KiB даже без Content-Length. Сжатие, query-параметры и Expect отклоняются;
Content-Type должен быть application/json. JSON разбирается строго: лишние поля,
повторяющиеся ключи и неподдерживаемые версии не принимаются.

Сервер вычитает время чтения/разбора из timeout и remaining_ttl клиента, проверяет
UTC expires_at и срок `created_at + Destination.retry.ttl`, затем ограничивает попытку
server/destination attempt_timeout. Истёкший запрос получает постоянный `relay_expired`
без provider send. Корректные часы на клиентах и gateway нужны до полевой проверки.

HTTP 401/403 — отказ доступа; 429 с Retry-After — перегрузка или ограничение частоты;
413 — слишком большое тело; 408 — истёк срок чтения. Provider timeout/ошибка после
начала send возвращается как UNKNOWN в коррелированном ответе. Никакого дополнительного
retry на gateway нет. Дубликат возможен при потере ответа после provider success:
повторный delivery ID не подавляется. При отключении клиента aiohttp отменяет handler;
уже начатая отправка может иметь неизвестный исход. Механизм:
[aiohttp handler cancellation](https://docs.aiohttp.org/en/stable/web_advanced.html#web-handler-cancellation).

## Владение ресурсами и диагностика

`Gateway(config)` предоставляет async start/close, свойство port и stats(). Все методы
вызываются в одном loop; фонового потока у gateway нет. После close повторный start
запрещён. Для прерывания незавершённого start отмените и дождитесь именно его задачи;
конкурирующий close во время start явно отклоняется. Ошибка/отмена start закрывает
в том числе канал с частично завершённым open.

Close сначала закрывает приём и listener, оставляет часть общего срока активным
запросам, затем отменяет оставшиеся и параллельно закрывает каналы. Медленный close
не мешает остальным. Повторные close ждут общую задачу; отмена одного ожидающего её
не отменяет. Пользовательский адаптер обязан отдавать управление loop и принимать
отмену: библиотека не может принудительно остановить блокирующий Python-код.

Stats возвращает копию фиксированных агрегатов: requests, auth_denied, invalid,
overloaded, rate_limited, expired, attempts, provider_accepted, статусы provider failures,
unknown, body_timeout, cancelled_requests, request_errors, close_failed и текущие
active/destinations_busy. Нулевые накопительные счётчики могут отсутствовать;
читайте их через `.get(name, 0)`. Это статистика процесса, не durable audit и не
подтверждение показа сообщения на телефоне.

Access log отключён; неожиданные HTTP/provider ошибки не раскрываются в ответах.
Контекст send/open/close защищает от рекурсивной отправки transport logging через
Remote Watch. Сторонний адаптер сам отвечает за отсутствие секретов в собственных
локальных handlers. Health/admin HTTP endpoints в этой версии не добавлены.

## Проверка с приложений

На клиенте заполните секцию relay в `credentials.local.json` по [RELAY.md](RELAY.md)
и запустите `python -m remote_watch.smoke relay`. В примере выше smoke должен пройти
точную проверку Identity. Для смешанного режима передайте в WatcherConfig одно
RelayConfig.destination и одно NtfyConfig.destination: вызовы logger остаются прежними.

До реального размещения проверены offline: auth/ACL, ограничения тела и частоты,
overload, expiry, cancellation, startup rollback, shutdown, возможный дубликат после
потери ответа и полный путь logger → relay HTTP → Telegram HTTP рядом с direct ntfy HTTP.
Провайдеры в этих тестах подставные. Региональный путь через размещённый gateway
и получение на Android остаются отдельной следующей проверкой.
