# Исполнение пользовательских команд

Version 1.0.4

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261005-213218

## Реализовано в 0.3.4.dev1

`CommandDispatcher` связывает неизменяемый `CommandRegistry` с `CommandClient`.
`RemoteWatcher(..., command_client=client)` явно включает этот исполнитель вместе
с уведомлениями. Без `command_client` словарь остаётся только регистрацией;
настройки outbound relay сами командный контур не включают.

Проверены функции, bound methods, callable-объекты, `partial`, async callbacks,
`CommandSpec` с контекстом и проверкой аргументов. Имена определяет приложение;
встроенных `status`, `resume_load` или `suspend_load` нет. Отдельные ACL, проверка
свежести и долговременная фиксация разрешения описаны в [COMMAND_HUB.md](COMMAND_HUB.md).
В 0.3.5 реализованы Telegram/ntfy sources, командный JSON/CLI и конечный smoke.
Реальная проверка с телефона остаётся отдельной приёмкой: [COMMAND_SOURCES.md](COMMAND_SOURCES.md).

## Подключение

Ниже подготовка объектов без запуска сети и без открытия SQLite. В рабочем
приложении используются собственный token, постоянный путь журнала и проверяемый
HTTPS origin. Установка HTTP-зависимостей: `pip install "remote-watch[commands]"`.

```python
import secrets
from functools import partial
from pathlib import Path
from threading import Event

from remote_watch import CommandRegistry, Identity, RemoteWatcher, WatcherConfig
from remote_watch.adapters.command_http import HttpsCommandTransport
from remote_watch.commands.client import CommandClient
from remote_watch.commands.protocol import CommandRegistration, describe_commands
from remote_watch.commands.sqlite_store import SQLiteCommandStore
from remote_watch.commands.storage import StoreRole

load_stop_event = Event()

def change_loading(*, stopped: bool, event: Event) -> None:
    """Request a state change; the application's worker observes the event."""
    event.set() if stopped else event.clear()

def check_loading() -> str:
    """Report the requested state, not completion of the application's work."""
    return "Остановка запрошена" if load_stop_event.is_set() else "Работа разрешена"

commands = CommandRegistry.from_callbacks({
    "resume_load": partial(change_loading, stopped=False, event=load_stop_event),
    "suspend_load": partial(change_loading, stopped=True, event=load_stop_event),
    "check_load": check_loading,
})
identity = Identity(service="loader", environment="test", region="ru",
                    host="example-vm", instance_id="one")
session_id = secrets.token_hex(16)  # Новый при каждом запуске процесса.
registration = CommandRegistration(identity=identity, session_id=session_id,
                                   capabilities=describe_commands(commands))
store = SQLiteCommandStore(Path("command-client.sqlite"), owner_id="loader-one",
                          generation=session_id, role=StoreRole.CLIENT)
transport = HttpsCommandTransport("https://gateway.example.invalid",
                                 "synthetic_example_token_" + "x" * 32)
client = CommandClient(registration, transport, store)
watcher = RemoteWatcher(WatcherConfig(identity=identity, commands=commands),
                        command_client=client)
```

Для синхронного приложения — `with watcher:` либо `start()` / `stop()`.
Для асинхронного — `async with watcher:` либо `await astart()` / `await astop()`.
Пример выше намеренно их не вызывает: рабочий hub и его разрешения настраиваются
отдельно. Чтобы видеть исключения startup, не подавляйте их в приложении.

Пять полей Identity должны точно совпадать с регистрацией клиента. Capabilities
должны совпадать с `describe_commands(config.commands)`, включая scopes и timeout.
`owner_id` и путь SQLite постоянны, `session_id` и `generation` новые на запуск.
Hub использует собственный журнал и новый `epoch`. Нельзя разделять один объект
клиента между несколькими исполнителями или вызывать его lifecycle параллельно
с lifecycle watcher. Владение запуском/остановкой остаётся у приложения.

## Где выполняется код приложения

| Режим | Сеть команд | Sync callback и валидатор | Async callback |
| --- | --- | --- | --- |
| `watcher.start()` | Отдельный командный event loop | Один отдельный рабочий поток | Отклоняется при запуске; нужен `astart()` |
| `await watcher.astart()` | Event loop приложения | Один отдельный рабочий поток | Тот же loop, который вызвал `astart()` |
| Отдельный `CommandDispatcher.start()` | Loop вызывающего приложения | Один отдельный рабочий поток | Тот же loop |

Уведомления сохраняют собственный поток/loop. Зависший sync callback не блокирует
их доставку и heartbeat команд. Исполнитель не накапливает очередь вызовов:
одновременно работает только один валидатор либо callback на этот dispatcher.
Для стандартного `threading.Event` подходит sync callback; для `asyncio.Event`
нужен async callback, выполняющийся в loop владельца события. Произвольные
объекты приложения не становятся потокобезопасными от регистрации в библиотеке.

Async callback обязан не блокировать loop и корректно обрабатывать отмену.
Функция, объявленная обычным `def`, должна возвращать `None` или строку;
возвращённый awaitable не переносится в другой loop и считается неверным результатом.
Рекурсивный запуск/остановка watcher из callback или валидатора запрещены:
изменяйте состояние приложения через его собственные события и команды.

## Аргументы, срок и результат

После локальной записи CLAIMED, но до запроса grant у hub, выполняется синхронный
`validate_arguments`. Он должен быть чистой проверкой без изменения приложения,
вернуть `None` либо отклонить аргументы исключением. Другой результат, исключение
или timeout дают `rejected/invalid_arguments`, без текста исключения. Без валидатора
разрешён только пустой набор аргументов. Зависший валидатор продолжает занимать
единственный рабочий поток даже после отклонения запроса.

После сохранённого STARTED исполнитель одноразово потребляет ticket и повторно
проверяет срок непосредственно в рабочем потоке/корутине перед вызовом. Ожидание
очереди потока не продлевает разрешение. Истёкшее разрешение даёт `unknown/timeout`
без вызова callback. Срок — минимум оставшихся сроков команды, регистрации и
выданного ограничения исполнения; собственные UTC-часы VM здесь не используются.

`None` означает, что callback нормально вернулся; строка до 4096 байт UTF-8 —
ограниченный текст результата. Другое значение даёт `unknown/invalid_result`,
исключение — `unknown/callback_error`; его сообщение и traceback наружу не выходят.
Вызов `Event.set()` означает только установку события. Это не подтверждение,
что рабочий процесс уже прекратил загрузку или закончил отдельную операцию.

Заявления `read_only` и `idempotent` не проверяют код функции и не разрешают её
автоматический повтор. Библиотека повторяет только сохранение/доставку результата.

## Timeout, доставка результата и остановка

При timeout сохраняется `unknown`, но фактически работающий обработчик продолжает
занимать слот. Async-задача получает отмену; sync-поток принудительно не прерывается.
Следующая команда не запускается до фактического завершения. Поздний успешный ответ
не заменяет уже зафиксированный `unknown`.

Клиент отдельно подтверждает hub факт окончания через `/v1/commands/release`.
Это `CommandClaim` с прежними ref, claim_id и digest, а не новый результат и не
новое разрешение. Hub проверяет владельца и возвращает квитанцию сохранённого
терминального результата. Клиентский журнал сохраняет необходимость повторить
это подтверждение даже при потере ответа. Отправка ответа в чат сама по себе
не доказывает окончание callback и не снимает блокировку исполнения.

Для `busy`, `unavailable`, `capacity` повтор ограниченной операции хранения или
доставки выполняется через 0,5 с по умолчанию. Новый callback не запускается при
недоставленном результате. Прочие ошибки прекращают приём; безопасный код виден
в `watcher.command_stats.last_error`. Следите также за `closed` и `running`.
После потери сессии/epoch автоматической перерегистрации и повторного вызова нет.

Остановка запрещает новые вызовы и ограничивает ожидание исполнителя пятью
секундами по умолчанию. `CommandDispatcher.close()` возвращает `False`, если работа
ещё не завершилась. Sync-обёртка дополнительно ждёт свой поток не более шести
секунд; уведомления останавливаются по собственным пределам. После закрытия журнала
позднее завершение callback уже не освобождает запись автоматически.

Ограниченное ожидание библиотеки не является принудительным завершением Python:
зависший поток может удерживать процесс, а async callback, игнорирующий отмену, —
завершение event loop. Обработчики обязаны иметь собственные конечные ожидания.

## Восстановление и ручной разбор

Начиная с 0.4.1.dev7 текущее состояние отделено от истории отказов:

```python
stats = watcher.command_stats
if stats is not None:
    ready = stats.ready
    last_incident = (stats.last_error_stage, stats.last_error)
    poll_error = stats.health.poll.current_error
    poll_recoveries = stats.health.poll.recoveries
    heartbeat_error = stats.health.heartbeat.current_error
    pending_result = stats.health.has_pending_results
```

`last_error` и `last_error_stage` сохраняют последний инцидент даже после успешной
работы. Для текущей индикации используйте `ready`, а для причины — `stats.health`.
Те же наблюдения доступны напрямую через `CommandClient.health`.
Модели `CommandHealth` и `StageHealth` находятся в `remote_watch.commands.health`.

Каждый из четырёх этапов `poll`, `heartbeat`, `storage`, `result` содержит
`current_error`, `http_status`, `failures`, `transient_failures`, `recoveries`.
Временные коды — busy/unavailable/capacity. Повторный отказ увеличивает счётчик,
успешная операция соответствующего этапа снимает его текущую временную ошибку
и увеличивает recoveries один раз. Передача одного исключения через несколько
уровней клиента/dispatcher не увеличивает счётчики повторно.

Успех callback не восстанавливает heartbeat или доставку результата. Result считается
восстановленным только после совпавшей квитанции hub, локального подтверждения и
проверки журнала; `has_pending_results` сохраняет неопределённость, если эта проверка
не завершилась. При полной порции в 1000 записей признак остаётся консервативно True.
Timeout SQLite не освобождает поток и не означает восстановления storage: нужна
фактически завершившаяся следующая операция. История ограничена счётчиками и последними
кодами; тексты команд, аргументы, credentials и исходные исключения в ней отсутствуют.

`ready` означает наблюдаемую исправность этапов при действующей сессии и отсутствии
неподтверждённого результата. Занятый callback отдельно виден через `running`;
готовность не обещает немедленного исполнения новой команды. До start и после начала
shutdown ready=False. Stale_session исключает дальнейшую работу этой сессии; успешный
поздний heartbeat не возрождает её. Автоматической перерегистрации нет. Чтение снимков
не обращается к сети/SQLite, не меняет историю и допустимо из другого потока.

`CommandClient.pending()` возвращает до 1000 ожидающих записей; `lookup(ref)`
позволяет проверить конкретную запись, в том числе уже подтверждённую. Это методы
работающего клиента: после остановки используется новый владелец журнала с новой
generation, а не повторное открытие старого объекта.

При восстановлении `reconcile()` подтверждает заведомо неисполненные EXPIRED;
затем они могут очищаться по retention. `flush_results()` вызывает эту сверку
и доставляет сохранённые результаты текущей сессии. Старые STARTED становятся UNKNOWN и
сохраняются вместе с возможным активным исполнением. Их нельзя автоматически
освобождать по истечении срока или запускать заново. Разбор требует проверки
состояния приложения; готового административного CLI для этого пока нет.

В 0.3.4 JSON-схема сообщений остаётся 1; добавилась операция `release`.
Обновляйте командный hub и клиент вместе до 0.3.4.dev1 или совместимой версии.
Отката к неподтверждённому освобождению на старом hub нет.

Локальные проверки не обращаются в Telegram, не читают credentials и не меняют
настоящие приложения. Результаты сборки и тестов: [VALIDATION.md](VALIDATION.md).

## Причина отказа синхронного запуска

С 0.4.1.dev6 синхронный watcher.start передаёт в __cause__ ошибки
CommandError("unavailable") **новое безопасное исключение**. Исходное исключение,
его traceback, context/cause, notes, пути и сообщения не копируются. Это исправляет
поведение dev4/dev5, где обычная печать traceback могла раскрыть исходный текст.

При точном типе CommandError сохраняются проверенные code/http_status. Известные
категории представлены стандартными ModuleNotFoundError/ImportError,
SSLCertVerificationError/SSLError, TimeoutError/ConnectionError, sqlite3.Error,
StoreError, OSError и ValueError с фиксированным текстом. Неизвестная ошибка
превращается в RuntimeError("command startup failed"); произвольное имя её класса
также не переносится. Имя отсутствующего модуля, errno и verify_code не копируются:
эти атрибуты нельзя использовать как прежние подробности первичного исключения.
Проверяется вся стандартно отформатированная цепочка, включая подставные приватные
сообщения, notes и вложенную причину. Это граница синхронного командного потока;
произвольные исключения приложения вне неё не получают такой гарантии.

HTTP-адаптер сохраняет числовой CommandError.http_status и точный известный код
из JSON-ответа gateway: например, stale_session остаётся stale_session при HTTP 409.
Принимается только объект с единственным полем code, без дублей ключей, до 256 байт,
при application/json без Content-Encoding и с подходящим HTTP-статусом. Чужие,
повреждённые, слишком большие или зависшие ответы оставляют классификацию по статусу.
Перенаправлений и скрытых повторов нет. После получения заголовков статус не теряется
даже при timeout/ошибке тела; неверный ответ HTTP 200 даёт invalid с http_status=200,
а ошибка его чтения — unavailable с тем же статусом.

Код верхней ошибки и поведение rollback сохранены. Отказ командного старта
по-прежнему останавливает watcher уведомлений; callback не повторяется.
