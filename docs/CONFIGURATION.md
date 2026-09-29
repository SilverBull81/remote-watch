# Контракт конфигурации

Version 1.0.7

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260929-122613

## Статус

Реализованы Identity, SnapshotLimits, RetryPolicy, RuntimeConfig, Destination,
Route и WatcherConfig. Они проверяют данные без запуска runtime. Добавлены
TelegramConfig/NtfyConfig и их ленивые фабрики. RemoteWatcher, ConsoleConfig и
RotatingFileConfig описаны в [WATCHER.md](WATCHER.md). Обязательного формата файла нет;
локальный JSON используется только явной smoke-командой.
В 0.1 конфигурация выражается типизированными Python-объектами; core не требует
YAML, JSON, TOML или специальных URL. Интеграция со стандартным logging реализована
через NotificationRuntime.handler; отдельный loader расширений dictConfig не входит
в первую версию. Пример: [RUNTIME.md](RUNTIME.md).

## Разделы

| Раздел | Содержание |
| --- | --- |
| identity | service, environment, region, host, instance_id |
| local logging | Явно запрошенные handlers, уровни, формат, rotation и владение |
| destinations | Уникальные ID, provider kind, direct/relay, policy |
| routing | Remote-порог, topic/tags/identity predicates, destination IDs |
| runtime | Входная очередь, lifecycle deadlines, лимит числа назначений |
| delivery policy | Outstanding budget, TTL, attempts, timeouts, backoff |
| redaction | Обработка чувствительных данных до очереди |
| internal diagnostics | Локальный sink и ограничение частоты |
| commands | Реализован: словарь callbacks или CommandRegistry из CommandSpec |

Все реализованные policy/model-конструкторы доступны из remote_watch. Публичные имена:
RuntimeConfig.snapshot_limits, Destination.outstanding_capacity, Destination.retry,
WatcherConfig.routes и WatcherConfig.commands. Сами dataclasses очереди не создают
и таймауты не исполняют. NotificationRuntime применяет ограничения при запуске.

В версии 0.1.0 runtime применяет `RetryPolicy` полностью для очереди и повторов:
по умолчанию три попытки, full jitter, backoff cap, retry-after и общий TTL.
`max_attempts=1` отключает повторы. Общий attempt_timeout применяется к каждой попытке;
connect_timeout отдельно соблюдает HTTP-клиент Telegram/ntfy, поскольку общий
протокол send не раскрывает стадию установления соединения. Метод config.destination()
передаёт одну политику обеим сторонам; см. [ADAPTERS.md](ADAPTERS.md).

Для воспроизводимых проверок NotificationRuntime принимает `delivery_clock` с методами
`monotonic()` и `async sleep(delay)`, а также `random_source()` с конечным значением
от 0 до 1. Они должны быть быстрыми и поддерживать кооперативную отмену ожидания.
Часы `clock()` задают только UTC-дату уведомления. Lifecycle и таймаут send остаются
ограниченными реальным временем; виртуальные часы не могут отменить срок остановки.

Identity — непустые строки; пустое/неизвестное значение не подставляется из сети
или названия чата. Для неизвестной region/host допустим явный маркер unknown.
Session ID создаётся на каждый runtime. Метаданные LogRecord не меняют identity.

## Назначение и способ доставки

Следующая таблица иллюстрирует будущие настройки, не является готовым config-файлом.

| ID назначения | Provider | Mode | Где адресат и provider secret |
| --- | --- | --- | --- |
| operations-chat | telegram | relay (0.2) | На gateway под разрешённым alias |
| phone-alerts | ntfy | direct | В локальных настройках topic и secret reference |

Для direct необходимы provider settings. Для relay необходимы HTTPS gateway URL,
service secret reference и remote alias; provider credentials у приложения не нужны.
RelayConfig не содержит provider credentials и создаёт relay factory. В 0.2.0.dev3
реализованы клиент и сервер: [GATEWAY_SERVER.md](GATEWAY_SERVER.md). Сам mode не подменяет произвольную
пользовательскую фабрику. См. [RELAY.md](RELAY.md). Нет автоматического переключения
на direct при недоступности gateway и нет автоматического выбора канала по стране.

На контрактном этапе Destination принимает destination_id, provider (описательное
имя, по умолчанию custom), channel_factory, mode=DeliveryMode.DIRECT,
outstanding_capacity и RetryPolicy. Фабрика — синхронный callable без аргументов,
в том числе класс с пустым конструктором. Она должна вернуть NotificationChannel;
создание конфигурации проверяет её сигнатуру, но не вызывает её и не доказывает
соответствие возвращаемого объекта async-протоколу. Это проверяется contract tests.
Provider settings и ссылки на секреты принадлежат фабрике; её repr не раскрывается.
TelegramConfig/NtfyConfig/RelayConfig создают готовые фабрики через destination(); токены читаются
из переменных окружения при open. Общего secret resolver пока нет. Неизвестные provider-имена
не импортируются: так подключаются пользовательские реализации без registry SDK.

В 0.1 каждый adapter instance привязан к одному назначению. Разные destination IDs,
указывающие на один chat/topic, считаются разными намеренными доставками; router
дедуплицирует только ID. Пользователь обязан избегать непреднамеренных aliases.

## Исходные лимиты 0.1

Это проектные defaults для проверки прототипом, а не измеренные оптимальные значения.
Все временные параметры — секунды; размер — байты UTF-8, KiB = 1024 байта.

| Настройка | Default | Семантика |
| --- | --- | --- |
| ingress_capacity | 1024 | Снимки, ожидающие маршрутизации |
| max_destinations | 16 | Проверяемый верхний предел настроенных назначений |
| outstanding_per_destination | 256 | Ожидание + active send + retry вместе |
| concurrent_sends_per_destination | 1 | В 0.1 фиксировано; последовательные попытки |
| overflow | drop_newest | Без ожидания места, без исключения в logging caller |
| event_max_bytes | 16 KiB | Максимальный сериализованный снимок |
| message_max_bytes | 8 KiB | UTF-8-safe усечение с видимым маркером |
| exception_max_bytes | 4 KiB | Текст исключения без traceback objects |
| metadata_max_bytes | 2 KiB | Общий бюджет identity/topic/tags/correlation |
| ttl | 300 | Срок с момента приёма в handler |
| max_attempts | 3 | Включая первую; применяется и к unknown |
| connect_timeout | 3 | Максимум соединения, внутри attempt timeout |
| attempt_timeout | 10 | Вся попытка, включая чтение ответа |
| backoff_base / cap | 1 / 30 | Full jitter для retry |
| startup_timeout | 5 | Запуск worker/клиентов, без обязательного health request |
| shutdown_timeout | 5 | Общее время drain, cancellation и close |

В реализации SnapshotLimits.metadata_max_bytes ограничивает компактный JSON всех
полей снимка, кроме message/exception; учитываются также IDs, timestamps, schema
и признаки усечения. Notification валидирует уже подготовленный текст и не усекает
его самостоятельно. Logging normalizer выполняет усечение до создания модели.
From_dict принимает только schema_version=1 и не принимает limits из payload.

TTL назначения хранится в RetryPolicy.ttl. Эффективный deadline — минимум срока
снимка и created_at + TTL назначения; счёт с момента каждой новой попытки запрещён.

При первой неуспешной попытке задержка выбирается равномерно от 0 до base,
далее верхняя граница удваивается до cap. Для rate limit задержка не меньше
валидного retry-after. Deadline отдельной попытки ограничен оставшимися TTL
и shutdown budget. Некорректный retry-after не допускает бесконечного ожидания.

Метаданные за пределом бюджета отклоняются, а не обрезают identity до совпадающего ID.
После усечения текста проверяется общий размер снимка; если он всё ещё превышен,
событие отклоняется с причиной oversize. Размер provider payload дополнительно
ограничивается адаптером. В 0.1 длинное уведомление усекается с маркером; автоматическое
разбиение на несколько provider messages отложено из-за частичных успехов.

Лимиты очередей не означают нулевого выделения памяти при форматировании чужого
объекта. Должны проверяться именно пределы удерживаемых снимков и delivery state.

## Правила маршрутизации

- Отсутствующий notify: порог ERROR, если конфигурация не задала другой.
- notify=True: снять remote-порог, сохранить прочие predicates.
- notify=False: запретить remote для этой записи.
- Внутри правила — AND; между правилами — объединение выбранных destination IDs.
- Без совпадения нет remote-отправки; локальные handlers не затрагиваются.
- Logger level и его filters проверяются раньше handler; helper явно объясняет
  их настройку и не обещает доставку отфильтрованных записей.

Форматирование по умолчанию включает service/environment, region/host, instance ID
и уровень. В локальном rotating file rotation задаётся пользователем; файл не
используется как автоматический outbox и не перечитывается для повторной отправки.

## Валидация, секреты и изменение настроек

Проверяются типы, положительные конечные бюджеты, уникальность IDs, ссылки правил,
доступность optional adapter, взаимоисключение direct/relay и безопасный URL.
Неизвестные поля отклоняются. Сетевая проверка не является частью schema validation.

Секрет задаётся через явно выбранный environment name или injected provider.
Автоматический поиск произвольных credentials-файлов и чтение личных браузерных
профилей не предусмотрены. Значение секрета не входит в repr конфигурации.
Custom adapter обязан соблюдать тот же контракт.

Hot reload в 0.1 отсутствует. Для изменения настроек закрывается старый runtime
и создаётся новый; перенос очереди между ними не обещается. Ротация credentials
также требует пересоздания соответствующего runtime до появления reload-контракта.

Команды регистрируются локально через WatcherConfig.commands. Конфигурация принимает
либо CommandRegistry, либо mapping имён на функции без оставшихся обязательных
аргументов; partial позволяет привязать состояние приложения. Подробности:
[COMMANDS.md](COMMANDS.md). Сетевого command mode в этом каркасе нет.
