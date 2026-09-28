# Проверки и критерии приёмки

Version 1.0.8

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260928-222548

## Статус

Реализован путь от logging до Telegram/ntfy с повторами и RemoteWatcher в 0.1.0. Проверяются модели,
настройки, команды, подготовка записей, маршрутизация, ограниченные очереди и worker.
Проверены retry scheduler, sync/async lifecycle и оба HTTP-адаптера. Gateway
ещё отсутствует. Пользователь подтвердил оба smoke на Android. Доставка со всех
целевых серверов и длительная фоновая работа телефона ещё не проверены.
Библиотечная матрица проверена на Windows/Python 3.12; полевой сценарий — отдельно.

## Итерация 0.2.0.dev1

Добавлены полевой smoke и relay-клиент. Серверный gateway и его ACL ещё не реализованы.
Проверки текущей итерации на Windows/Python 3.12.2:

- 313 passed, 2 skipped; Ruff без ошибок. Live-запуски в этой итерации не выполнялись.
- Field smoke: два подставных канала, четыре вида сообщений, локальное подавление,
  redaction, усечение, отчёт, ошибка провайдера, Ctrl+C, эксклюзивный путь и очистка токена.
- Relay: строгая схема, повторяющиеся ключи, версия, корреляция, неправильные бюджеты,
  ограничение размеров и отсутствие запроса при недостаточном остатке срока.
- Одноразовый relay smoke: отдельная секция credentials, одна попытка, безопасный вывод
  и очистка временного токена на подставном канале.
- Настоящий HTTP на loopback: успех, 403, 429, повреждённый/слишком большой ответ,
  обрыв после обработки запроса, один POST на send и очистка клиента.
- Direct/relay режимы в runtime проверены на подставных каналах; стабильный delivery ID
  сохраняется между повторами, остаток срока передаётся каждой попытке. Это ещё
  не сквозной тест рабочего gateway с provider adapter.
- Wheel/sdist собраны; установка проверена в чистых build/validation-02-core и
  build/validation-02-relay. Десять Python-блоков документации выполнены на установленном
  пакете. Relay-клиент импортируется без Telegram/ntfy; field smoke --help работает без extras.
- Финальный прогон выявил нестабильность прежнего теста startup timeout с общим сроком
  0.2 с и резервом очистки 40 мс. Для проверки отмены open срок теста увеличен до 1 с,
  добавлен барьер начала open. Изолированная проверка и повторный полный набор прошли;
  production-таймауты и логика очистки в связи с этим не менялись.

Полевой запуск на трёх серверах выполняет пользователь; подтверждённых результатов
на момент этой итерации нет. Инструкция: [FIELD_SMOKE.md](FIELD_SMOKE.md).

## Проверки реальной отправки

- 28.09.2026, сообщение пользователя: offline с extras — 241 passed, 2 skipped;
  live pytest для Telegram — 1 passed, 1 skipped. Сообщение получено в нужном чате
  на телефоне. Сервер/регион запуска и версия Android не указаны.
- 28.09.2026, запуск агентом `python -m remote_watch.smoke telegram`: код 0,
  Telegram подтвердил приём одного нового синтетического сообщения. Использован
  локальный файл, значения credentials в вывод/отчёт не включены. Получение именно
  этого сообщения относится к последующему подтверждению пользователя.
- 28.09.2026 пользователь подтвердил: оба smoke доставили сообщение на телефон.
  У ntfy бесплатный аккаунт без резервирования темы; это проверка доставки, а не ACL.
  Все регионы, Android idle и восстановление сети этим запуском не проверены.
  Инструкция настройки: [NTFY_SETUP.md](NTFY_SETUP.md).

## Выполненная проверка каркаса

Среда: Windows, CPython 3.12.2; pytest 8.2.0, Ruff 0.15.4, setuptools 82.0.0,
build 1.2.2.post1, aiohttp 3.14.3. Проверки не обращаются к внешним сервисам
и не используют реальные credentials; часть HTTP-проверок запускает loopback-сервер.

- Ruff check: без ошибок.
- Unit/contract suite с extras: 274 сценария, два live-теста штатно пропущены.
  Добавлены проверки RemoteWatcher и исполнения документационных примеров.
- Регрессии итогового review: запоздавшая запись не открывает закрытый файл снова;
  медленный close одного канала не препятствует закрытию остальных.
- Smoke проверяет выбранную секцию JSON, алиасы chat/chat_id/topic, свежие сведения,
  один вызов send, закрытие при ошибке/отмене, удаление временного токена, неизменность
  файла и отсутствие приватных значений в выводе. Используются только вымышленные credentials.
- Logger/LoggerAdapter, консоль и файл, два канала, защита identity, ошибки форматирования,
  редактирование/усечение текста, уровни/фильтры/иерархия logging.
- Настоящий фоновый поток: единый loop клиентов, медленный/ошибочный канал рядом
  со здоровым, несколько producers, переполнение и объединение loop wakeups,
  рекурсия двух runtime, остановка из адаптера, остатки очередей и отмена активной отправки.
- Проверены обычная остановка, идемпотентный lifecycle, ошибка/таймаут open с закрытием,
  timeout send и истёкшее уведомление без ожидания полного TTL.
- Повторы с виртуальными часами: full jitter, cap, retry-after, стабильные ID,
  порядок очереди, permanent failure, exhausted, unknown, ошибки источника случайных
  значений, TTL и сохранение слота во время backoff. Burst нескольких получателей
  проверяет ограничение задач и незавершённых доставок.
- Async lifecycle: async with, отмена astart/astop с закрытием ресурсов, повторная
  отмена, astop из канала. Нарушающий контракт blocking adapter даёт FAILED/TimeoutError.
- Нормальный выход отдельного процесса проверяет закрытие канала через atexit.
- Telegram/ntfy: параметры POST, кириллица/emoji, явная identity, усечение,
  успешные и повреждённые подтверждения, HTTP/API 429, retry-after и HTTP-дата,
  4xx/5xx, redirect, слишком большой ответ, неизвестный исход и отмена.
- Fake HTTP проверяет настройки клиента; настоящий aiohttp на loopback проверяет
  таймаут, отмену, обрыв, запрет redirect, отсутствие повторного POST и токенов в DEBUG-журнале.
- Проверены частичный сбой open с освобождением connector, принадлежность loop,
  чтение токенов при open и полный путь logger → оба адаптера → безопасная остановка.
- Синтаксис всех исходников и тестов разбирается с grammar Python 3.10.
- Сборка sdist и wheel проверена локальным backend с --no-isolation;
  wheel строится из распакованного sdist, затем устанавливается в два чистых venv.
- Wheel 0.1.0 проверен в build/validation-01-core без extras
  и build/validation-01-extras с обоими extras;
  smoke --help не требует aiohttp. Установка использует локальный архив. Изолированный импорт
  использует установленный пакет, не src; runtime dependencies отсутствуют,
  py.typed включён, импорт не создаёт потоки и не меняет root logging handlers.
- Девять Python-блоков из COMMANDS.md, RUNTIME.md, ADAPTERS.md и WATCHER.md выполнены на установленном wheel.
- В sdist включены документация и тесты; wheel содержит только пакет и metadata.
- Локальный credentials-файл исключён из индекса Git и обоих архивов; при проверке
  содержимого значения токена/адресата Telegram не выводятся.

Runtime-совместимость Python 3.10 и Linux не проверена. Fake HTTP и loopback не доказывают
доступность провайдеров, их ACL или получение сообщения на Android. В установленном
build-окружении есть предупреждение о старом packaging для проверки license
expressions; для личного использования открытая лицензия не задана. Обновление глобальных пакетов не выполнялось.

## Проверка документационной основы

Проверяются наличие относительных Markdown-ссылок, UTF-8 BOM/CRLF, заполнение
метаданных и согласованность границ реализованного каркаса и будущих 0.1/0.2/0.3.
Перед коммитом проверяется staged diff и git diff --check. Официальные provider
документы использованы для выбора канала; источники перечислены в CHANNELS.md.

## Матрица 0.1

| Область | Сценарии | Ожидаемый результат |
| --- | --- | --- |
| Logging | Logger/LoggerAdapter, hierarchy, filters, propagation, чужие handlers | Нет глобальной перенастройки и изменения исходной записи |
| Eligibility | notify true/false/absent, ERROR default, topic/tags/identity | Точные правила, без двойного destination ID |
| Snapshot | Изменяемые args, exception, неверные metadata, oversize | Неизменяемые ограниченные данные, без traceback references |
| Identity | Подмена instance/service через extra | Runtime identity сохраняется |
| Queue | Полный ingress и одно переполненное назначение | Drop без ожидания, другие назначения продолжаются |
| Scheduling | Retry storm, bridge wakeups, несколько producers | Общий outstanding budget не обходится callbacks/tasks |
| Delivery | Accepted, transient/permanent failure, malformed reply, unknown | Правильная классификация и безопасная диагностика |
| Retry | Attempts, jitter, retry-after, TTL | Конечные попытки, нет повторов после expiry |
| Isolation | Slow/failing channel рядом со здоровым | Здоровый обслуживается независимо |
| Recursion | Worker, adapter, HTTP dependency и diagnostic sink logs | Не возникают новые remote notifications |
| Secrets | Config repr, ошибки HTTP, redactor, tokens в URL | Собственные credentials не появляются в снимках/диагностике |
| Lifecycle | До start, duplicate start, stop до start, startup failure | Явные состояния, корректный rollback ресурсов |
| Shutdown | Full queue, retry wait, active send, concurrent producers | Закрытие приёма, общий deadline, discard/unknown accounting |
| Async | Логирование из приложения с asyncio, async shutdown | Не захватывается и не блокируется main loop доставкой |
| Packaging | Core без extras, adapters с extras, clean wheel install | Нет скрытой provider dependency |

Clock и random source подменяются в unit-тестах; retry-тесты не ждут реальные
минуты. Настоящие concurrency-тесты используют barriers/events и конечные deadlines,
а не хрупкие ожидания по короткому sleep. Проверки воспроизводимы и не требуют сети.

Contract suite для custom adapters проверяет lifecycle в одном loop, одну попытку
на send, cancellation, классификацию ошибок и отсутствие скрытого retry.
Синхронно блокирующий/игнорирующий отмену адаптер контракту не соответствует.
Тестируемый fake transport не должен зависеть от Telegram token или ntfy endpoint.

## Полевой сценарий Android

Отдельно от offline tests выполняется [проверка канала](CHANNELS.md).
Нужны все целевые server regions, Wi-Fi и мобильная сеть, заблокированный телефон,
простой, перезапуск приложения и восстановление сети. Фиксируются задержки,
пропуски, duplicates и настройки background delivery. Не считать HTTP success
подтверждением показа уведомления на телефоне.

Live tests запускаются только явно, отправляют синтетический текст в выделенное
назначение, получают credentials извне и не запускаются в обычном CI/pytest.
Порог приемлемой мобильной задержки уточняется по полевому сценарию до приёмки;
гарантия времени remote display не является свойством runtime библиотеки.

## Relay 0.2

Проверить direct и relay одновременно, aliases/ACL, подмену identity,
несовместимую wire schema, expiry, size limits, overload и закрытие сервиса.
Проверить timeout после успешной provider attempt: клиент учитывает unknown,
повторы ограничены и могут дать duplicate. На gateway нет второго retry loop.
Relay-only режим не должен открывать command endpoints.

## Commands 0.3

Проверить actor/conversation authorization, exact target, capabilities, heartbeat,
две конкурирующие регистрации, session replacement, expiry, replay, lease/ack,
рестарт hub/application, потерю ответа и корреляцию с исходным command ID.
При недоступном audit/replay storage приём закрывается. Display name не даёт права.
Read-only status не запускает shell. Unknown outcome не превращается в success.

Broadcast и изменяющие команды не принимаются в scope минимальной версии;
их partial failure/idempotency проверяются при отдельном расширении.
