# Контракт конфигурации

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260928-100331

## Статус

Это планируемый контракт. Публичные конструкторы и формат файла ещё не реализованы.
В 0.1 конфигурация выражается типизированными Python-объектами; core не требует
YAML, JSON, TOML или специальных URL. Интеграция handler с обычным logging остаётся
возможной; отдельный loader расширений dictConfig не входит в первую версию.

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
Смешивание прямых credentials и relay binding в одном назначении отклоняется.
0.1 отклоняет relay как ещё неподдерживаемый режим. Нет автоматического переключения
на direct при недоступности gateway и нет автоматического выбора канала по стране.

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
