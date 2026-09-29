# Проверки и критерии приёмки

Version 1.1.2

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260929-190055

## Статус

В 0.2.0.dev5 добавлены числовая диагностика ntfy, строгая relay schema 2 с сохранением
schema 1 по умолчанию, ранняя проверка wire-пределов и настоящий TLS на loopback.
Полный набор на Windows/Python 3.12.2: **460 passed, 2 skipped**, Ruff без ошибок.
Тестовое окружение build/dev5-env содержит cryptography из dev extra; закрытые ключи
генерируются в tmp_path, настройки доверия ОС не меняются.

Новые проверки охватывают перенос чисел ntfy → gateway → relay → field journal,
размеры реального HTTP JSON, отказ приватным/недопустимым значениям, совместимость
формата v1, корреляцию версии v2, rejection до POST при увеличенных локальных лимитах.
ntfy_diagnostic проверен на подставном канале: точные размеры, десять попыток максимум,
досрочная остановка по квоте, ошибки open/close, отмена, границы интервала,
эксклюзивный отчёт и удаление временного токена.

TLS: доверенный CA, недоверенный CA, неверное имя, истёкший срок, HTTP на TLS-порту,
путь CLI --cert/--key, отсутствующий/неверный/несоответствующий ключ. Реальные
ntfy-публикации, VM компании, внешний proxy и выпуск публичного сертификата здесь
не проверялись. Причина прежнего long text остаётся открытой до диагностического запуска.

Wheel/sdist dev5 собраны и установлены в чистые build/validation-dev5-core и
build/validation-dev5-extras. В обоих выполнены 11 Python-примеров документации,
JSON check-config, проверка размеров diagnostic и формата ответов relay 1/2;
с extras выполнен HTTP-запрос установленного RelayChannel к Gateway. В этих
рабочих окружениях cryptography отсутствует. Проверены архивы, BOM/CRLF, ссылки,
grammar Python 3.10 и отсутствие реальных credentials. Это не runtime-тест Python 3.10.

Реализован путь от logging до Telegram/ntfy с повторами и RemoteWatcher в 0.1.0. Проверяются модели,
настройки, команды, подготовка записей, маршрутизация, ограниченные очереди и worker.
Проверены retry scheduler, sync/async lifecycle и оба HTTP-адаптера. Gateway
реализован и проверен offline в 0.2.0.dev3. Пользователь подтвердил оба smoke на Android. Доставка со всех
целевых серверов проверена коротким сценарием с обнаруженными отказами;
длительная фоновая работа телефона ещё не проверена.
Библиотечная матрица проверена на Windows/Python 3.12; полевой сценарий — отдельно.

## JSON-конфигурация gateway 0.2.0.dev4

На Windows/Python 3.12.2 полный прогон: **410 passed, 2 skipped**; Ruff без ошибок.
Проверены JSON обоих провайдеров, отсутствие чтения токенов при настройке, схема,
размеры, неизвестные поля, дубликаты, неверные права и безопасные ошибки CLI.
Оба пути запуска — JSON и Python-фабрика — передают обычную GatewayConfig.
Готовый пример четырёх приложений проверен также через настоящий gateway на loopback:
один listener обслуживает четыре получателя, чужой alias/Identity отклоняется,
ожидание первого получателя не мешает трём остальным. Провайдеры подставные.

Wheel/sdist 0.2.0.dev4 собраны; выполнена установка в чистые окружения
build/validation-json-core без extras и build/validation-json-extras с extras
gateway/relay/telegram/ntfy. В обоих проверены 11 Python-примеров документации,
чтение JSON и --check-config без токенов; с extras — локальный HTTP-запрос relay
к gateway. JSON-пример входит в sdist, реальные локальные конфигурации и credentials
исключены. Проверены BOM/CRLF, ссылки, grammar Python 3.10 и отсутствие секретов.

Реальный HTTPS на Windows, выпуск/продление сертификата и запуск после перезагрузки
пока не проверены; инструкция [GATEWAY_TLS.md](GATEWAY_TLS.md) описывает подготовку.

## Gateway 0.2.0.dev3

Серверный процесс, auth/ACL, ограничения приёма и частоты, одна provider attempt,
проверка expiry и управляемый shutdown реализованы. Инструкция:
[GATEWAY_SERVER.md](GATEWAY_SERVER.md).

Полный прогон на Windows/Python 3.12.2: **379 passed, 2 skipped**; Ruff без ошибок.

Wheel/sdist собраны; проверены чистые окружения build/validation-gateway-core и
build/validation-gateway-extras. Без extras импорт и CLI help работают, start объясняет
необходимость gateway extra. С extras выполнен запрос установленного RelayChannel
к установленному Gateway с fake provider. Выполнены 11 Python-блоков документации;
проверены состав архивов, BOM/CRLF, ссылки, grammar Python 3.10 и отсутствие credentials.

- Offline проверяет токен, дубликаты Authorization, точные identity/aliases, строгий JSON,
  размер с Content-Length и chunked, запрет сжатия/Expect, отсутствие command endpoints.
- Проверены независимые общая/per-principal/per-alias ёмкости и ограничения частоты;
  лишние запросы не ждут в очереди. Медленное тело получает 408 и освобождает слот.
- Истёкший UTC срок не продлевается допуском часов. Provider timeout, ошибка и
  недопустимый relay-result дают UNKNOWN; сервер не выполняет повтор.
- Ошибка/отмена startup освобождает частично открытый канал. Проверены отмена
  активного HTTP-клиента, общий shutdown deadline, параллельный close каналов
  и независимость cleanup от отмены ожидающего владельца.
- Сквозной тест использует настоящие RemoteWatcher, RelayChannel, Gateway,
  TelegramChannel и NtfyChannel с подставными HTTP endpoints. Telegram идёт через
  gateway, ntfy напрямую. После потери первого ответа повтор может дать дубликат.
- Реальные credentials и внешние provider endpoints в этой итерации не используются.
  Региональный deployment, TLS/proxy и Unix SIGTERM остаются отдельными проверками.

## Исправление 0.2.0.dev2

- Разобраны три коротких полевых отчёта от 29.09.2026: Telegram 0/4 на двух
  российских серверах и 4/4 в Латвии; ntfy 3/4 везде. Подробности и повторный
  сценарий: [FIELD_SMOKE.md](FIELD_SMOKE.md). Это Windows/Python 3.12.2, 3.12.7,
  3.12.10, а не подтверждение совместимости с Linux/Python 3.10.
- Исправлена упаковка ntfy JSON: UTF-8 без ASCII-экранирования, независимые пределы
  текста 4096 байт и всего JSON 8192 байта, проверка размера метаданных при настройке.
- Offline: 320 passed, 2 skipped; Ruff без ошибок. Новые проверки используют настоящий
  aiohttp и loopback: короткий текст, длинная кириллица/emoji, JSON-экранирование,
  большие метаданные; слишком большие настройки отвергаются до создания клиента.
- Wheel/sdist собраны; установка без extras и с telegram/ntfy/relay проверена в двух
  чистых окружениях. Выполнены десять Python-примеров документации, проверены отсутствие
  сетевых действий при импорте, lifecycle и изоляция optional-зависимостей.
- Две явные попытки отправить длинный синтетический текст из среды разработки,
  включая повтор вне sandbox, получили transient_failure / http_temporary.
  Реальный приём исправленного long text этим запуском не подтверждён;
  требуется повторный короткий сценарий на серверах пользователя.

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
