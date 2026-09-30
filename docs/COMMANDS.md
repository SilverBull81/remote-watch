# Пользовательские команды

Version 1.0.3

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260930-193650

## Объём первой рабочей 0.3

30.09.2026 владелец подтвердил: нужны и проверка состояния, и resume_load /
suspend_load. Read-only status — первый шаг проверки общего механизма, а не
граница всей 0.3. В 0.3.0.dev1 добавлен [контракт wire/состояний/времени](COMMAND_PROTOCOL.md).
Ниже описана существующая регистрация; сетевое исполнение ещё не добавлено. Направление: [ADR 0009](adr/0009-command-scope-and-safety.md),
порядок реализации: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

## Что уже работает

Приложение само определяет все имена и обработчики. Реализованы CommandSpec,
CommandRegistry, CommandContext и параметр commands у WatcherConfig.
Конфигурация копирует состав реестра, проверяет дубликаты, типы и сигнатуры,
но не вызывает callbacks, не запускает сеть и не предоставляет удалённый доступ.

RemoteWatcher и NotificationRuntime принимают WatcherConfig при создании.
В 0.1 они обеспечивают исходящую доставку; наличие commands не запускает приём
или выполнение команд. Реальное приложение будет подключено после минимального
контура команд, по решению владельца от 28.09.2026.

## Простой словарь и partial

Для команд без аргументов словарь остаётся самым коротким вариантом. Существующие
on_start_load_cmd/on_stop_load_cmd можно привязать partial, как в приложении
пользователя. Следующий самостоятельный пример использует непосредственно Event:

```python
from functools import partial
from threading import Event

from remote_watch import Identity, WatcherConfig

load_stop_event = Event()
check_event = Event()
config = WatcherConfig(
    identity=Identity(
        service="loader", environment="production", region="ru",
        host="loader-host", instance_id="loader-01",
    ),
    commands={
        "resume_load": partial(Event.clear, load_stop_event),
        "suspend_load": partial(Event.set, load_stop_event),
        "check_load": partial(Event.set, check_event),
    },
)
assert set(config.commands) == {"resume_load", "suspend_load", "check_load"}
assert not load_stop_event.is_set()
```

Поддерживаются обычные функции, bound methods, partial, callable objects и async
callbacks. Обработчик словаря вызывается в форме callback(), поэтому все обязательные
аргументы должны быть привязаны. Регистрация проверяет сигнатуру через inspect,
не вызывая обработчик. Для callable без доступной сигнатуры нужна явная Python-обёртка.
Генераторы и классы как command callbacks не принимаются.
Источник поведения inspect: [Python documentation](https://docs.python.org/3.10/library/inspect.html#inspect.Signature.bind).

Registry сохраняет ссылки на callbacks, а не сериализует функции и привязанные Event.
Состав реестра неизменяем; состояние, захваченное callback, по-прежнему принадлежит
приложению и может меняться. Изменение исходного словаря не меняет зарегистрированный набор.

## Когда нужен CommandSpec

Для описания команды, контекста, аргументов и политики используйте явную спецификацию.

```python
from collections.abc import Mapping

from remote_watch import CommandContext, CommandRegistry, CommandSpec

def validate_arguments(arguments: Mapping[str, str]) -> None:
    """Require a bounded count argument."""
    if set(arguments) != {"count"} or not 1 <= int(arguments["count"]) <= 100:
        raise ValueError("count must be between 1 and 100")

def check_load(context: CommandContext) -> str:
    """Return a correlated application-specific status."""
    return f"{context.identity.instance_id}: requested {context.arguments['count']} entries"

commands = CommandRegistry(specs=(
    CommandSpec(
        name="check_load", callback=check_load, takes_context=True,
        validate_arguments=validate_arguments,
        description="Проверить состояние загрузки",
        required_scope="load:read", timeout=5,
        read_only=True, idempotent=True,
    ),
))
```

Такой registry передаётся как commands при создании WatcherConfig. Смешанный набор
строится из CommandSpec; простой вариант для одной команды — спецификация только
с name и callback. Декоратор регистрации возможен позже как удобство поверх того же
контракта; отдельная система классов команд пока не нужна.

| Поле | Значение |
| --- | --- |
| name | Точное имя, `[a-z][a-z0-9_]{0,63}`; без `/`, пробелов и скрытых aliases |
| callback | Локальная функция или callable; не сериализуется, исключён из repr |
| takes_context | False: callback(); True: callback(context) |
| validate_arguments | Синхронная функция mapping строк → None либо ValueError |
| required_scope | По умолчанию command:<name>; это требование, а не выданное право |
| timeout | Положительный конечный срок выполнения; default 10 секунд |
| read_only / idempotent | Явные заявления приложения; оба False по умолчанию |

Если validate_arguments отсутствует, будущий dispatcher должен отклонять любые
аргументы удалённого запроса. Валидатор допускается только с takes_context=True;
он проверяет разрешённые имена, обязательность, диапазоны и формат. Неизвестные
аргументы не должны незаметно игнорироваться. Приведение к доменному типу делает
приложение после проверки; автоматическое доверие аннотациям callback не предусмотрено.
Context хранит только именованные строки: максимум 32 аргумента, 64 байта имени,
1024 байта значения и 4096 байт суммарных имён/значений в UTF-8.

## Что должен обеспечить будущий dispatcher

Сначала аутентификация actor/conversation, ACL и target/session, expiry/replay,
проверка аргументов и аудит; затем вызов зарегистрированного обработчика.
Отсутствие разрешения закрывает доступ. Наличие CommandSpec не включает command source.
CommandContext сам по себе тоже не доказывает аутентификацию.

Все пользовательские команды допустимы к регистрации уже сейчас. Read-only status
одному instance — первый сетевой приёмочный сценарий, не встроенное ограничение имён.
Связь с мессенджером не нужна в сигнатуре callback, команды принадлежат приложению.

Async callback должен исполняться в согласованном с приложением loop. Синхронные
callbacks не исполняются в loop доставки уведомлений. Точный executor/thread-affinity
контракт фиксируется до dispatcher; не следует обещать безопасную работу любого
обработчика в произвольном потоке. Синхронный callback нельзя принудительно остановить
по timeout. Отмена/таймаут могут оставить неизвестный исход операции.

read_only, idempotent, timeout и required_scope пока являются проверенными
регистрационными метаданными, а не реализованным enforcement. Конструктор не может
доказать идемпотентность функции. Автоматического retry команд, отправки ответа,
hub polling, ACL storage и command execution в каркасе нет.
