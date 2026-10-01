# Контракт команд 0.3.1: сообщения, состояния и время

Version 1.0.4

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261001-153531

## Реализовано в 0.3.0.dev1

Три независимых от сети модуля, после реорганизации: commands.protocol
(модели/JSON), commands.state (монотонные сроки/переходы), commands._confirmation
(прототип одноразового подтверждения).
Они входят в core без дополнительных зависимостей, не запускают фоновые задачи,
не читают токены, не обращаются к провайдерам и не вызывают callbacks.
Регистрация через словарь/partial и CommandSpec сохраняется без изменения API.

Этот документ описывает контракт контура. В 0.3.0.dev2 реализованы
[SQLite и транзакции](COMMAND_STORAGE.md), [свежесть без второго подтверждения](COMMAND_TIME.md).
Права, hub и HTTPS endpoints реализованы в 0.3.3.dev1: [COMMAND_HUB.md](COMMAND_HUB.md).
Автоматический executor и Telegram source относятся к 0.3.4/0.3.5.
Создание CommandGrant или CommandContext само по себе не выдаёт права.
Их происхождение, аутентификация и атомарная фиксация проверяются CommandHub/CommandClient.

## Сообщения и пределы

Каждое сообщение — UTF-8 JSON с ровно тремя полями: schema_version=1, kind и payload.
Кодирование: encode_command; разбор: decode_command. Схема не связана с relay 1/2.
Каждый вид имеет точный набор полей, включая nullable/default-поля; неизвестные,
пропущенные и повторные ключи, NaN/Infinity, bool вместо числа версии и некорректный
Unicode отклоняются. Ошибка декодирования фиксированная, без исходных данных.
Ответы транспорта должны декодироваться как ожидаемый тип, а не только как любой
CommandMessage; затем проверяется корреляция. HTTP-статус не заменяет результат.

| kind / модель | Поля payload | Смысл |
| --- | --- | --- |
| registration / CommandRegistration | identity, session_id, capabilities | Команды одного запуска приложения, без callback |
| session / CommandSession | identity, session_id, hub_epoch, remaining_ttl | Регистрация в конкретном запуске hub |
| request / CommandRequest | ref, source_id, source_event_id, actor_id, conversation_id, name, arguments | Неизменяемое намерение и сведения проверенного source |
| claim / CommandClaim | ref, claim_id, request_digest | Единственная попытка; повтор сохраняет claim_id |
| grant / CommandGrant | request, claim_id, remaining_ttl, execution_timeout | Разрешение после устойчивой фиксации начала попытки |
| result / CommandResult | ref, claim_id, outcome, text, reason | Итог callback или неизвестный исход |
| receipt / CommandReceipt | ref, claim_id, result_digest | Квитанция о сохранении именно этого результата |

CommandRef содержит identity, session_id, hub_epoch, command_id. Identity имеет
все пять существующих полей. Capability содержит name, required_scope, timeout,
read_only, idempotent, accepts_arguments. metadata берётся из CommandRegistry
через describe_commands; она не является ACL. Локальный scope нельзя понижать
по присланным данным. Регистрация другого живого session_id с той же Identity
в будущем hub должна отклоняться, а не незаметно вытеснять работающий процесс.

| Ограничение | Значение |
| --- | --- |
| Полное сообщение | 65536 байт UTF-8 |
| Capabilities регистрации | 1–64, имена уникальны |
| Имя команды | `[a-z][a-z0-9_]{0,63}`, как у CommandSpec |
| Scope | 128 байт UTF-8 |
| source_id | 1–128 ASCII букв, цифр, `_`, `-` |
| source_event_id, actor_id, conversation_id | 128 байт UTF-8 каждый |
| session_id, hub_epoch, command_id, claim_id, confirmation nonce | 32 строчные hex-цифры; генерация из 128 случайных бит |
| Хеш запроса/результата | SHA-256, 64 строчные hex-цифры |
| Относительные сроки | Положительные конечные числа до 300 секунд; bool запрещён |
| Подтверждение изменяющей команды | Не более 30 секунд на часах hub |
| Аргументы | До 32; имя 64, значение 1024, суммарно 4096 байт UTF-8 |
| Текст результата | До 4096 байт UTF-8 |

Проверка формата nonce не доказывает случайность: при реальном запуске использовать
secrets.token_hex(16), не счётчик. Хеш фиксирует содержимое, но не заменяет TLS/auth.
Идентификатор source_event_id сохраняется для уникального ключа source_id + event ID
в будущем журнале; повтор Telegram update не должен порождать новый command_id.

Существующий CommandSpec может описывать более долгий локальный callback;
его экспорт в этот сетевой контракт отклоняется, если timeout больше 300 секунд.
Увеличение локальных лимитов не расширяет wire автоматически.

## Исполняемый пример моделей без исполнения команд

```python
from functools import partial
from secrets import token_hex
from threading import Event

from remote_watch import CommandRegistry, Identity
from remote_watch.commands.protocol import (
    CommandClaim, CommandGrant, CommandRef, CommandRegistration, CommandRequest,
    CommandSession, decode_command, describe_commands, encode_command, message_digest,
)

stop_event = Event()
registry = CommandRegistry.from_callbacks({"suspend_load": partial(stop_event.set)})
identity = Identity(service="loader", environment="test", region="ru", host="vm", instance_id="one")
session_id, hub_epoch = token_hex(16), token_hex(16)
registration = CommandRegistration(identity=identity, session_id=session_id,
                                   capabilities=describe_commands(registry))
assert decode_command(encode_command(registration)) == registration

request = CommandRequest(
    ref=CommandRef(identity=identity, session_id=session_id, hub_epoch=hub_epoch, command_id=token_hex(16)),
    source_id="telegram-main", source_event_id="123", actor_id="42", conversation_id="-123",
    name="suspend_load",
)
claim = CommandClaim(ref=request.ref, claim_id=token_hex(16), request_digest=message_digest(request))
grant = CommandGrant(request=request, claim_id=claim.claim_id, remaining_ttl=30, execution_timeout=10)
assert grant.matches(claim)
assert not stop_event.is_set()
```

Пример создаёт данные и проверяет корреляцию. Он не аутентифицирует actor и
не является способом обойти подтверждение или вызвать callback.

## Время без сравнения UTC разных VM

В wire нет created_at/expires_at для авторизации команд. Hub владеет монотонным
сроком принятого намерения и не продлевает его при retry. В remaining_ttl grant
он передаёт минимум оставшихся сроков команды и действующей регистрации.
execution_timeout дополнительно ограничен этим остатком и timeout обработчика.
Клиент использует execution_timeout как бюджет B для начала и ожидания callback,
фиксируя собственные t_send и t_receive; локальная политика может только сократить B.

CommandDeadline.from_response(B, t_send, t_receive, epoch) задаёт локальное
expires_at = t_send + B. В момент t остаток равен max(0, expires_at − t).
Вычитается весь round trip, включая ожидание long poll; это может дать ранний
отказ, но не добавляет времени. Callback не начинает работу, если остаток нулевой.
Сравнение с UTC клиента или UTC другого сервера вообще не используется.
Крайнее значение deadline считается истёкшим. Смена hub_epoch и наблюдение
времени раньше t_receive дают нулевой остаток.

```python
from remote_watch.commands.state import CommandDeadline

deadline = CommandDeadline.from_response(10, 100, 103, hub_epoch)
assert deadline.remaining(103, hub_epoch) == 7
assert deadline.remaining(110, hub_epoch) == 0
assert deadline.remaining(103, token_hex(16)) == 0
```

Deadline локален: его нельзя передавать по сети, сохранять для восстановления
в другом процессе или строить заново из прежнего полного TTL при перезапуске.
Регистрация имеет отдельный локальный deadline, который не обновляется от любого
grant. Для перехода к исполнению должны оставаться положительными оба срока.
Heartbeat продлевает только регистрацию, а не старую команду.
UTC может храниться для отображения/audit, но не увеличивать право на исполнение.

## Старые сообщения Telegram: уточнение требования

После реализации прототипа владелец выбрал resume/suspend **одним сообщением**,
без отдельного подтверждения. Описанный ниже CommandChallenge уже реализован,
но не подключён к runtime и больше не является обязательным сценарием 0.3.

Согласованная замена реализована в 0.3.0.dev2: отметка провайдера сравнивается
с достоверным интервалом UTC на hub. Учитываются погрешность, дрейф и срок годности
показания, после приёма используется только оставшийся монотонный срок.
Стандартный максимальный возраст — 120 с, оценка времени годна 300 с.
Точные правила, HTTPS-источник и ограничения: [COMMAND_TIME.md](COMMAND_TIME.md).
Конкретный origin выбирается явно и требует полевой проверки; автоматически
доверять Date произвольного сайта нельзя. Дата Telegram не доказывает время
набора текста на телефоне без сети. UTC приложений в проверке не участвует.

### Реализованный прототип подтверждения

Даже независимые часы приложения не позволяют отличить свежее намерение
от старого сообщения, задержанного перед hub. Исходный вариант 0.3.1
для read_only=False использовал одноразовое подтверждение. Это описание
сохранённого прототипа, а не выбранного владельцем интерфейса.

Первоначальная команда создаёт только ограниченный CommandChallenge. Hub выдаёт
случайный nonce, связанный с полным запросом, source, actor, conversation и epoch;
пользователь подтверждает показанное намерение в течение 30 секунд. Возможный UI —
кнопка; конкретный Telegram parser относится к 0.3.5. confirm_command не принимает
один nonce как самостоятельное право: сверяет actor/chat/source и хеш всего запроса.
Повтор, чужой пользователь, изменённые аргументы, истечение и смена epoch дают отказ.

Принятие подтверждения должно атомарно записать consumed и создать READY-команду
в постоянном хранилище. Сам ConfirmationDecision только предлагает эту запись.
Давнее исходное сообщение может создать запрос подтверждения, но не запустить
изменение без свежего ответа пользователя. Нельзя автоматически подтверждать
намерение на основе его имени или idempotent=True. Для простого словаря оба
флага False; команды проверки без подтверждения требуют явного read_only=True.

Для read-only в первой итерации допускается ответ на задержанный запрос текущего
состояния; это не исторический снимок. Источник сохраняет cursor/уникальный event ID,
а старые callbacks подтверждения никогда не принимаются в новой epoch.
После рестарта незавершённые challenges закрываются; остаток времени не восстанавливается.

## Переходы и запись перед действием

CommandRecord — неизменяемая запись. advance_command возвращает CommandTransition;
он ничего не сохраняет и не исполняет. Для CLAIM/START проверяются точная сессия,
request_digest, единственный claim_id и оба локальных срока. START требует grant,
который соответствует этому claim и не допускает более длинного локального срока.

| Исходное состояние | Действие | Новое состояние | Побочное действие |
| --- | --- | --- | --- |
| READY | CLAIM | CLAIMED | Нет |
| CLAIMED | повтор того же CLAIM | CLAIMED | Нет |
| CLAIMED | START с grant | STARTED | Только после успешного atomic commit |
| STARTED | повтор CLAIM/START | STARTED | Повтор callback запрещён |
| READY/CLAIMED | отказ | REJECTED | Нет |
| READY/CLAIMED | истечение срока | EXPIRED | Нет |
| STARTED | timeout | UNKNOWN | Новое исполнение запрещено; текущий callback может продолжаться |
| STARTED | сохранение результата | COMPLETED/UNKNOWN | Нет нового вызова |
| Терминальное состояние | повтор | То же состояние/результат | Нет |

На hub STARTED фиксируется **до отправки grant**, на клиенте — **до callback**.
Если запись на одной стороне не удалась, её следующий шаг не выполняется.
Два конкурентных перехода из одного снимка могут оба предложить start_callback=True:
только победитель atomic compare-and-swap имеет право исполнить действие.
Эта транзакция реализована в SQLiteCommandStore; простое использование моделей без журнала
не даёт crash-safe защиты от повторов. Repr не содержит args, actor/chat или текста ответа.

При recover_command READY/CLAIMED становятся EXPIRED, STARTED становится UNKNOWN,
терминальные записи остаются неизменными. Новая epoch/session не продолжает
старую операцию. Уже выданное разрешение у временно отключённого клиента нельзя
мгновенно отозвать: оно может быть использовано в пределах прежнего локального
срока, пока клиент не узнал о смене epoch. Поэтому STARTED при восстановлении —
UNKNOWN, а не «точно не исполнялось». Новое поколение hub не выдаёт второе разрешение.

Поздний результат допускается, пока запись ещё STARTED. Если UNKNOWN уже устойчиво
сохранён, противоречащий результат отклоняется; уточнение фактического состояния —
отдельная команда приложения. Повтор совпадающего терминального результата возвращает
тот же итог. Квитанция связывается с result_digest; потеря квитанции ведёт к повтору
передачи результата, но не callback. Результат за пределами текущей сессии требует
отдельной аутентифицированной сверки журнала в будущем runtime.

## Контракт результата callback

callback_result принимает уже возвращённое значение, не вызывает обработчик.
None или str до 4096 UTF-8 байт дают completed. Это «callback завершился»,
а не «приложение завершило загрузку». При None будущий renderer использует
нейтральное сообщение; текст str — явное сообщение автора приложения.
Результат стороннего объекта не преобразуется через str/repr. Неверный тип,
Unicode или переполнение дают unknown / invalid_result: callback мог уже изменить состояние.
Исключение после начала callback будущий executor отразит как unknown / callback_error;
текст исключения не включается. Таймаут — unknown / timeout.
Отказ до вызова — rejected с denied/invalid_arguments; истечение — expired.

CommandReceipt подтверждает сохранение результата, не доставку ответа в мессенджер
и не выполнение бизнес-операции. exactly-once и автоматический повтор callback
не обещаются даже при idempotent=True. Количество действительно активных callbacks
учитывается отдельно от терминального UNKNOWN: timeout не освобождает рабочий слот.

## Следующая граница

0.3.2 реализован в [SQLiteCommandStore](COMMAND_STORAGE.md): постоянные позиции
источника, команды/результаты, аудит, атомарные переходы, пределы и восстановление.
0.3.3.dev1 добавляет auth/ACL и HTTPS-транспорт; executor/loop и Telegram остаются
этапами 0.3.4/0.3.5.
До этого RemoteWatcher.commands по-прежнему только регистрирует callbacks.
