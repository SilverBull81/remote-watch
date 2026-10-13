# ADR 0013: названия notification gateway без переходных алиасов

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261013-141730

## Статус и основание

Реализовано в 0.4.2.dev3. Владелец 13.10.2026 явно отказался от legacy API:
единственное использующее пакет приложение — SpamBot, его обновление можно
выполнить согласованно. Первоначальное предложение совместимых алиасов отменено.
Этот ADR заменяет терминологию и выбор extra `relay` из ADR 0002/0005.

## Решение

Клиент: `NotificationGatewayConfig` и `NotificationGatewayChannel` из
`remote_watch.adapters.notification_gateway`. Модель wire: `NotificationGatewayRequest`
из `remote_watch.notification_gateway_protocol`. Режим доставки и источник результата:
`DeliveryMode.GATEWAY`, `ResultSource.GATEWAY`, значение `gateway`.
Provider назначения, диагностический CLI и секция smoke тоже называются `gateway`.
Причины: `gateway_*` и `invalid_gateway_response`. Extra `relay` удалён;
существующий `[gateway]` используется и для клиента, и для сервера уведомлений.

Старые модули удалены; нет wrappers, re-exports, enum aliases или автоматического
исправления старого конфига. Серверный `GatewayConfig` сохраняет имя: он отличается
от `NotificationGatewayConfig` клиента и уже обозначает gateway.
Notifications и commands остаются раздельными возможностями; reconnect не добавлен.

## Протокол и постоянные данные

Маршрут `/v1/notifications`, поля запросов/ответов и правила схем 1/2/3 не зависят
от имени Python-класса. Они сохраняются без повторного POST/fallback. Удаление
протокольных схем — отдельное изменение, не часть удаления старых Python-имён.
Source назначается клиентом и не входит в wire. Reason_code меняется на новое
написание в существующем строковом поле. Смешанные версии могут работать при
успешных запросах, но не считаются целевой проверенной конфигурацией эксплуатации.

Identity, alias, credentials, ACL, команды, session/hub IDs, source_id,
journal generation, SQLite и Caddy CA не меняются. Переименование не требует
очистки журналов, замены сертификатов или перевыпуска токенов. Имена переменных
среды задаёт оператор и остаются произвольными.

## Миграция и проверки

Новый wheel не содержит старые модули/extra; enum и CLI отвергают `relay`.
Исторические отчёты/ADR сохраняют прежние названия как записи о прошлых версиях.
Активные примеры используют новый API. Миграция приложения выполняется отдельно
в его репозитории и venv; приватную схему SpamBot автоматически не переписываем.
Таблица замен и порядок обновления: [GATEWAY_FOLLOW_UP.md](../GATEWAY_FOLLOW_UP.md).

Проверки: отсутствие старых имён; loopback HTTP/TLS, auth/ACL, timeout и rendering;
прежняя форма успешных envelopes; чистые core/extras wheel из sdist;
исполнение документационных примеров. Результаты: [VALIDATION.md](../VALIDATION.md).
Недельный prod-прогон не обновлялся и не является тестом этого переименования.
