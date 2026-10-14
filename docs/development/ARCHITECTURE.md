# Архитектура и обязательные контракты

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-202944

Это актуальная сводка решений, а не хронология этапов. Точные типы/лимиты определены
в исходниках и regression tests. Пользовательская настройка — в [руководстве](../README.md).

## Границы пакетов

| Модуль | Ответственность |
| --- | --- |
| config/events/watcher/local_logging | Типизированная конфигурация, Identity, logging/lifecycle |
| notifications | Снимок LogRecord, маршруты, очереди, retry/TTL, health и gateway wire |
| adapters | Отдельные direct providers и HTTP transport; optional dependencies |
| commands | Реестр, wire/state, время, SQLite, hub/client/dispatcher и sources |
| gateway | Notification/command server и JSON/CLI, probe protocol |
| gateway_server | Процессы, supervision, deploy/resources, service/tray |
| diagnostics | Поставляемые smoke/doctor/time/resolution утилиты |
| tests/tools | Разработка и проверки, не runtime API |

Core интегрируется со стандартным Logger через handler/композицию. Не требуется
подкласс Logger, замена logging или совместимость со старым сторонним bot API.
Root exports ограничены; внутренние имена не становятся стабильным публичным API
из-за отсутствия начального underscore. Конструкторы не запускают сеть/фоновые задачи.

## Notification delivery

LogRecord превращается в ограниченные независимые данные. Очереди ограничивают
события и незавершённые назначения, не только ожидающие coroutine. Capacity держится
также при in-flight/retry. Очередь одного получателя не блокирует остальные.
Runtime не вызывает собственный transport logging рекурсивно. Секреты/исключения
не передаются в provider payload или безопасные diagnostics без явной redaction.

TTL и deadlines охватывают всё ожидание, retries, wakeups и network. При shutdown
ожидание конечно; никакой durable outbox или гарантии хотя бы одной попытки нет.
Неопределённая отправка может дублироваться, повтор callback команды запрещён.
Direct/gateway выбирается **для назначения**, не страны или класса приложения.

Health — пассивное последнее наблюдение, stats — история, runtime_state — lifecycle.
Принятие очищает только текущую ошибку данного получателя. Drops не выдаются за
сетевую недоступность. Наблюдение не меняет политику delivery и не делает probes.
presentation full/compact/text убирает только отображаемые поля, не Identity/IDs/ACL.

Notification gateway использует POST /v1/notifications; envelope schemas 1/2/3
остаются различимыми. Явный client display override передаётся схемой 3, отсутствие
override сохраняет старый envelope. Нет downgrade-повтора POST при неизвестной схеме.
Сервер проверяет service credential, точную Identity, alias и ограниченные бюджеты.
Отдельный bounded UTC skew относится только к уведомлениям, не защищает команды
от replay и не отменяет проверку TLS.

## Команды и durable state

Actor/chat, target Identity и scopes проверяются перед admission. Команда привязана
к конкретной сессии, epoch, request и claim. Выбор по единственному **живому** instance
не используется. Имя callback и flags read_only/idempotent сами не дают прав.
Не предусмотрены произвольный shell/eval, неявный broadcast и exactly-once.

Hub/client/source SQLite имеют одного локального владельца, bounded records/streams/
audit/payload и OS locks. История source cursors/high-water сохраняется после cleanup,
чтобы старый provider event не исполнился повторно. Проверка revision перед STARTED
атомарна. Новый процесс получает новое поколение; reconnect не переоткрывает journal,
не сбрасывает cursors и не переиспользует старую generation после process restart.

Timeout callbacks не означает остановку потока/внешнего действия. Worker и execution
slot остаются заняты до фактического завершения. Старый STARTED после crash становится
UNKNOWN и удерживает неопределённость. Истечение lease/timeout не освобождает такое
исполнение. Повторяется только доставка готового результата, не вызов callback.
Sync callbacks и validators имеют один worker; async — loop async-приложения.

Одна heartbeat/reconnect задача после успешного начального старта: bounded jittered
backoff, один candidate nonce, ожидание завершения poll/callback и исходов. Новая
сессия открывается только после урегулирования результатов. Поздний результат старого
epoch получает ACK лишь при точном совпадении уже durable исхода и текущих правах
principal на Identity; не может записать новый исход или освободить execution.
Conflicts/UNKNOWN блокируют восстановление, а не скрыто меняют результат.

Административная resolution — только offline list/export/preview/apply, остановленные
владельцы, точный client COMPLETED против hub UNKNOWN. SQLite schema 2 сохраняет
отдельное acceptance, fencing старых handles, оригинальный UNKNOWN/owner/cursors.
Схема 1 мигрирует на open, старые runtimes схему 2 отвергают. Нет HTTP admin endpoint,
ручного локального ACK, удаления журналов или повторного callback.

Command wire schema 2 расширяет registration/request/grant явным display mode;
отсутствие mode сохраняет schema 1/hash. Results/receipts не меняются. Режим ответа
берётся из исходного durable request, не более новой регистрации.

## Время и transport

TrustedClock опирается на явный trusted UTC interval, монотонный остаток, max age
120 секунд, uncertainty/drift. Выбран TimeAPI UTC JSON с явным допущением accuracy
одна секунда, не обещанием точности провайдера. Нет fallback на часы ОС или иной origin.
Ещё действительный anchor сохраняется во время refresh; фактический отказ/cancel
его инвалидирует, что отражается в time_reason.

HTTP не сериализует произвольные exception text/notes/traceback. Ошибки ограничены
allowlisted code/status/reason. TLS локально сохраняет error_kind/verify_code/
tls_reason; code 10 не доказывает failing depth. Проверки сертификата/имени обязательны.
Непонятный ответ сервера не преобразуется в разрешение команды. Authentication probe
не использует business endpoints и не продлевает lease.

StoreWorker имеет один реальный executor operation. Hub contention проходит через
bounded FIFO (default 64, максимум 256) с общим admission/execution timeout.
Отменённая ожидающая операция не передаётся в store; уже выполняющаяся операция держит gate
до реального завершения. Клиент/source допускают немедленный busy; busy_reason
отличает причины, но не меняет старый верхнеуровневый command status.
Journal summaries читаются на той же executor lane; ошибка наблюдения не меняет
исход операции/commit. Status читает только кэш, age не обнуляется чтением.

## Процессы, deployment и службы

Manager владеет только свежими process handles, не PID из старого файла. Windows x64
использует kill-on-close Jobs, Linux — gated parent-death bootstrap. Child стартует
под защитой ОС до получения settings. Startup имеет rollback, shutdown — сроки,
restart — конечный бюджет; config/auth/ownership/storage не лечатся restart.

Control — private numeric loopback, per-generation/per-child tokens и защищённые файлы.
control/logs/state/CA не должны перекрываться. Перед изменением ACL своей log_dir
manager отвергает чужие файлы. Это проверка использования выделенной папки, не
защита от администратора, умышленно меняющего filesystem после проверки.

Deploy offline, dry-run без записи, existing files не перезаписывает, proposals
отдельны. Не открывает journals, не читает environment secrets и не запускает service.
Managed Caddy требует явного filesystem storage и проверяет local admin API;
external не принадлежит manager. Публичный HTTPS проверяется с клиента отдельно.

Windows service binding включает binary/config/account/оператора. Виртуальный
service account без пароля, минимальные ACL, partial grants не сбрасываются remove.
Проверка installer держит точный read-only byte lock старого manager.lock; отказ
доступа не равен занятому lock. Существующие grants проверяются на всём нужном дереве.
GUI — отдельный controller пользователя, служба всегда Session 0/headless. UI не
создаёт второй manager и не останавливает службу при выходе пользователя.

## Изменение контрактов

При доработках сохраняйте эти инварианты и wire/hash/schema совместимость либо
явно проектируйте миграцию. Обновляйте этот документ и соответствующие тесты,
не создавайте новую хронологию в README. Открытые изменения — [BACKLOG](../BACKLOG.md).
