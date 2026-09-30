# ADR 0005: wire-контракт relay и границы упаковки

Version 1.0.2

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260930-172038

## Статус

Контракт и клиент реализованы в 0.2.0.dev1. Серверный HTTP transport выбран для
следующей итерации. В 0.2.0.dev3 сервер и auth/ACL реализованы;
решения уточнены в [ADR 0006](0006-gateway-admission-and-lifecycle.md).
Основной реальный RU → LV → Telegram smoke подтверждён владельцем на dev9
30.09.2026. Ниже сохранено исходное решение о границах.

## Решение

Оставить один репозиторий/distribution. Relay-клиент получает extra `relay` с aiohttp;
сервер будет получать отдельный extra `gateway` и работать отдельным процессом.
Никакие серверные зависимости не становятся обязательными для core. Пустой extra
gateway до появления реализации не создаётся.

Одна provider attempt передаётся через HTTPS POST `/v1/notifications`, versioned JSON.
Точная схема, лимиты и интерпретация ответа: [RELAY.md](../RELAY.md). Request содержит
alias, Notification, delivery ID, attempt, remaining TTL и server timeout. Credential
передаётся отдельно через Bearer. Ответ коррелируется с попыткой; enqueue receipt
не признаётся успешной доставкой.

Runtime передаёт адаптеру реальный остаток срока попытки как Delivery.remaining_timeout.
Это предотвращает выдачу gateway нового полного таймаута после ожидания в очереди,
повторов либо начала shutdown. Клиент сокращает server timeout на сетевой резерв.
Сервер обязан дополнительно ограничить его своей политикой и проверенным expiry.

Будущий сервер сопоставляет credential с точной identity и разрешёнными aliases;
provider credentials находятся только на сервере. Сервер не добавляет retries,
каскад relay и command endpoints. Документирование схемы не является реализацией ACL.

## Альтернативы и последствия

Отдельная distribution увеличила бы число синхронизируемых версий при маленьком
текущем проекте. Отдельный framework сервера пока не нужен: aiohttp уже выбран для
клиента и имеет управляемый lifecycle сервера. Серверный процесс остаётся изолированным
от приложений независимо от общей упаковки.

Для loopback contract tests используется управляемый AppRunner/SockSite; возможности
описаны в [официальной документации aiohttp](https://docs.aiohttp.org/en/stable/web_advanced.html#application-runners).
Это подтверждает механизм тестового HTTP-сервера, а не готовность рабочего gateway.

HTTP timeout после успешного provider POST остаётся UNKNOWN. Повтор с тем же delivery ID
может создать дубликат. Exactly-once, durable receipts и отказоустойчивый кластер
требуют отдельных решений и здесь не обещаются.
