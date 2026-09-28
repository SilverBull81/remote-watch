# План реализации

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260928-100331

## Текущее состояние

Подготовлена документационная основа. Исходный код, сборка пакета, тесты и gateway
ещё не реализованы. Номера 0.1/0.2/0.3 обозначают предполагаемые релизы пакета;
Version 1.0.0 в шапках обозначает редакцию отдельного документа.

Работа выполняется небольшими проверяемыми изменениями. Сначала проверяется
полный путь на fake-канале, затем добавляется сеть. Приложения из соседних
репозиториев не меняются в рамках подготовки этой библиотеки.

## Подготовка

- Подтвердить Python на целевых Windows/Linux-машинах; проверить проектную цель 3.10+.
- Выбрать async HTTP-клиент для Telegram/ntfy по lifecycle, отмене, timeouts,
  отсутствию скрытых retries, размеру зависимостей и поддерживаемым Python.
- Проверить ntfy с российских и остальных серверов и Android-телефона по
  [CHANNELS.md](CHANNELS.md). Размещение сервиса определить по результатам.
- Выбрать лицензию и private/public distribution до распространения пакета.

Результат: подтверждённая среда, выбранный HTTP transport и отчёт о полевой проверке.
Разработку fake-прототипа можно вести параллельно ожиданию доступа к серверам;
релиз нельзя объявлять проверенным на этих серверах без фактического теста.

## 0.1 — библиотека уведомлений

### Шаг 1. Каркас и контракты

Создать pyproject, layout src/remote_watch и тестовые каталоги только при начале
реализации. Определить Identity, Notification, Destination, DeliveryResult,
минимальный async channel protocol и типизированную конфигурацию.
Отдельно проверить сериализацию, ограничения размера и неизменяемость снимка.

Готово, когда core импортируется без provider dependencies, fake channel может
реализовать контракт структурно, а validation errors объясняют неверную настройку.

### Шаг 2. Полный путь без сети

Реализовать NotificationHandler, router, bounded ingress и управляемый worker.
Один стандартный logger одновременно пишет локально и доставляет выбранное событие
в fake channel. Затем проверить два назначения и custom adapter.

Готово, когда обычный logger и LoggerAdapter работают без подмены глобального
Logger, существующие handlers сохраняют поведение, а identity нельзя подменить
через extra. Результаты маршрутизации определяются тестами для notify/levels/tags.

### Шаг 3. Надёжность и lifecycle

Добавить budgets назначений, retry scheduler, full jitter, timeouts, TTL,
rate-limit handling, stats, sync/async lifecycle и диагностическую изоляцию.
Clock и random source подменяются в тестах; thread/loop ownership проверяется
отдельными настоящими concurrency-тестами с конечными deadlines.

Готово, когда retries не обходят bounds, slow destination не останавливает соседний,
очередь не порождает unbounded tasks/callbacks, stop учитывает неизвестный исход
активной отправки, а внутренняя диагностика не создаёт новые уведомления.

### Шаг 4. Telegram и ntfy

Создать два optional adapters с одной попыткой на send, управляемыми клиентами,
plain-text рендерингом, усечением и безопасной классификацией ответов.
Provider credentials не входят в модели событий. Fake HTTP проверяет ошибки,
rate limit, invalid responses и отмену; каждый адаптер проходит общие contract tests.

Готово, когда обе реализации проходят offline contract tests и core устанавливается
без их extras. Реальная отправка включается только отдельным integration run.

### Шаг 5. Пользовательская настройка и упаковка

Добавить helper для обычного logger и принадлежащего ему runtime, локальные
console/rotating-file handlers по явной настройке, примеры sync/async программ
и подключения к существующей logger hierarchy. Примеры должны исполняться в тестах
на fake channel; сетевые примеры отдельно помечаются как opt-in.

Готово, когда wheel/sdist устанавливаются в чистое окружение, docs соответствуют
реальному API, нет зависимости от fin-data, tests без сети проходят на выбранных
Windows/Linux и минимальном Python. Лицензия/metadata определены до публикации.

### Приёмка 0.1

Выполнить матрицу [VALIDATION.md](VALIDATION.md), включая полевой ntfy-сценарий.
Перенести одно реальное приложение на новый handler отдельным изменением в его
репозитории. Не создавать compatibility shim без новой конкретной потребности.
Зафиксировать известные потери, дубликаты и ограничения фоновой работы Android.

## 0.2 — необязательный outbound relay

1. Выбрать packaging и серверный transport; определить wire schema и auth.
2. Реализовать client transport как замену direct transport в одном назначении.
3. Реализовать gateway с aliases/ACL, bounded concurrency, одной provider attempt.
4. Проверить смешанный режим: Telegram через relay, ntfy напрямую в том же runtime.
5. Проверить timeout после provider success, overload, expiry и неправильную identity.

Готово, когда смена direct/relay не меняет вызовы logger, приложение в relay-режиме
не требует provider token/SDK, а недоступность gateway подчиняется общей retry policy.
Command endpoints в relay-only deployment отсутствуют.

## 0.3 — минимальные команды

1. Определить persistent command/replay/audit storage, bounded retention и lease/ack.
2. Создать отдельный CommandSource и единственного владельца shared inbound state.
3. Добавить authenticated instance registration, session binding и heartbeat.
4. Реализовать read-only status одной точной цели и коррелированный ответ.
5. Проверить ACL, replay, expiry, рестарт, старую сессию и повтор после потери ответа.

Первый вертикальный сценарий — через Telegram; проверка Matrix SDK/E2EE может
идти как отдельная подготовка. Затем Matrix реализует outbound и inbound contracts.
Изменяющие команды и broadcast требуют дополнительной модели partial/unknown outcomes.

## Отложено

Durable notification outbox, batching, suppression summaries, deduplication,
quiet hours, автоматические fallback/escalation, вложения, plugin entry points,
hot reload, email, webhook, Apprise и HA gateway добавляются по отдельным требованиям.
MAX не рассматривается. Пустые модули для отложенных возможностей не создаются.
