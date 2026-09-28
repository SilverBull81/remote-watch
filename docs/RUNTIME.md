# Фоновая отправка уведомлений

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260928-131628

## Реализовано в 0.1.0.dev1

Обычный `logging.Logger` или `LoggerAdapter` передаёт записи в `NotificationHandler`.
Обработчик готовит независимые данные уведомления и пробует поместить их в ограниченную
входную очередь. `PolicyRouter` в фоновом потоке выбирает получателей. Для каждого
получателя есть своя ограниченная очередь и один асинхронный исполнитель.

В этом этапе выполняется **одна попытка** на доставку. У каждого Destination необходимо
явно указать `RetryPolicy(max_attempts=1)`. Значение 3 по умолчанию в модели описывает
следующий этап; текущий runtime отклоняет его при создании. Повторы, backoff,
исполнение retry-after, async lifecycle и сетевые адаптеры ещё не реализованы.

## Полный пример без сети

Пример работает после установки пакета и не требует токенов. MemoryChannel — адаптер
приложения без наследования от классов библиотеки; он сохраняет доставки для проверки.
Хранить все сообщения в списке допустимо в этом коротком примере, но не в рабочем
адаптере с неограниченным временем жизни.

```python
import logging

from remote_watch import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Identity,
    NotificationRuntime,
    RetryPolicy,
    Route,
    WatcherConfig,
)


class MemoryChannel:
    """Collect deliveries for an offline demonstration."""

    def __init__(self) -> None:

        self.deliveries: list[Delivery] = []

    async def open(self) -> None:

        """Prepare a channel without network resources."""

    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Record one delivery and report acceptance."""

        self.deliveries.append(delivery)
        return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)

    async def close(self) -> None:

        """Close a channel without external resources."""


first, second = MemoryChannel(), MemoryChannel()
identity = Identity(
    service="quotes", environment="test", region="test-region",
    host="test-host", instance_id="loader-1",
)
config = WatcherConfig(
    identity=identity,
    destinations=(
        Destination(destination_id="first", channel_factory=lambda: first,
                    retry=RetryPolicy(max_attempts=1)),
        Destination(destination_id="second", channel_factory=lambda: second,
                    retry=RetryPolicy(max_attempts=1)),
    ),
    routes=(Route(destination_ids=("first", "second"), required_tags=("load",)),),
)

# Уровень INFO выбран приложением: notify=True не обходит уровень самого logger.
logger = logging.Logger("example.loader", level=logging.INFO)
console = logging.StreamHandler()
logger.addHandler(console)
runtime = NotificationRuntime(config)
logger.addHandler(runtime.handler)

try:
    with runtime:
        logger.info("Только локальный журнал")
        logger.info("Загрузка началась", extra={"notify": True, "tags": ["load"]})
        logger.error("Ошибка другого процесса", extra={"tags": ["other"]})
finally:
    logger.removeHandler(runtime.handler)
    runtime.handler.close()
    logger.removeHandler(console)
    console.close()

# Выход из контекста ожидает принятые доставки в пределах shutdown_timeout.
assert len(first.deliveries) == len(second.deliveries) == 1
assert first.deliveries[0].notification.identity == identity
assert runtime.stats().accepted == 2
```

## Подключение к приложению

`NotificationRuntime(config, redactor=None, clock=utc_now)` создаёт локальное состояние
и единственный `runtime.handler`, но не запускает потоки и не вызывает фабрики каналов.
`session_id` создаётся отдельно для каждого runtime; `config.identity` всегда берётся
из настроек. Повторный `logger.addHandler(runtime.handler)` не дублирует этот объект.
Подключать один runtime следует в одной точке иерархии logging.

`runtime.handler.setFormatter(...)` задаёт текст удалённого сообщения. Formatter
получает копию LogRecord; exception и stack_info готовятся отдельно. `redactor(text)`
вызывается для сообщения и описания исключения перед усечением и очередью. Редактор
обязан быть синхронным, быстрым и возвращать строку. Ошибка подготовки, неправильные
метаданные или превышение общего размера отклоняют уведомление и увеличивают
`normalization_failed`. Текст исходной ошибки не печатается.

Разрешённые метаданные записи: `notify`, `topic`, `tags`, `correlation_id`, `trace_id`.
Произвольные extra-поля не переносятся. Они не могут заменить service, environment,
region, host, instance_id, session_id или адрес получателя. Для удаления секретов
из метаданных приложение должно готовить безопасные значения само; редактор текста
не распознаёт произвольные секреты автоматически.

`notify=False` запрещает отправку. `notify=True` снимает только порог уровня в правилах;
тема, все обязательные метки и сведения о приложении всё равно должны совпасть.
Без notify по умолчанию подходят уровни ERROR и выше. Правила объединяются, один
destination_id выбирается один раз. Пустой набор правил не выбирает получателей.
Стандартные уровни и фильтры logger/handler продолжают действовать раньше этой логики.

Консоль и файл остаются под управлением приложения. Runtime не вызывает basicConfig,
не меняет чужие handlers, уровни или propagation и не управляет локальной ротацией.
Сеть выполняется только в рабочем потоке. Форматирование и обычная краткая синхронизация
logging остаются в потоке приложения; обещания нулевой задержки вызова logger нет.

## Очереди и сроки

- `ingress_capacity` ограничивает уведомления, ожидающие маршрутизации.
- `outstanding_capacity` каждого получателя учитывает очередь вместе с активной попыткой.
- Переполнение отбрасывает новую запись/доставку, включая CRITICAL, без ожидания места.
- Исполнителей ровно по одному на получателя; задачи не создаются на весь будущий поток сообщений.
- Межпоточные пробуждения объединяются: в очереди loop не более одного ожидающего callback пробуждения.
- Маршрутизатор обрабатывает до 64 уведомлений за порцию, затем уступает loop отправителям.

Срок считается от начала подготовки записи handler, включая время форматирования.
UTC сохраняется в Notification, а локальные сроки и ожидания используют монотонные часы.
`expires_at` общего уведомления использует наибольший TTL получателей; конкретная доставка
ограничена меньшим из него и TTL своего получателя. Очередь и ожидание попытки расходуют TTL.
Часы `clock` можно подменить для воспроизводимых дат; они не заменяют часы asyncio.

Время send ограничено минимумом оставшегося TTL и attempt_timeout. connect_timeout —
обязанность конкретного сетевого адаптера: общий протокол не управляет его соединением.
Timeout, исключение или неправильный ответ дают неизвестный исход, а не доказанную
неотправку. Временный/постоянный отказ и rate limit на этом этапе завершают доставку
без повторов. Принятие сервисом не доказывает показ уведомления на телефоне.

## Запуск и остановка

`start()` создаёт один фоновый поток и собственный asyncio loop. Фабрики, open, send
и close вызываются в этом потоке. Каждому получателю нужен отдельный экземпляр канала.
Повторный start в RUNNING безопасен. `stop()` до start закрывает runtime без запуска.
Повторный stop после завершения безопасен; CLOSED и FAILED нельзя перезапустить.
Вне RUNNING записи отклоняются с причиной `not_running`, без форматирования.

При stop приём атомарно закрывается, принятая работа обрабатывается до срока завершения,
затем остаток отменяется и клиенты закрываются. Из общего бюджета резервируется
меньшая величина из 0.1 секунды и 20% для отмены/закрытия; аналогичный резерв есть
при запуске для отката частично открытых клиентов. Срок не начинается заново для
каждого клиента. Отмена активной попытки учитывается отдельно от ещё не начатой доставки.

Stop из самого канала лишь запрашивает остановку, без join собственного потока.
Stop снаружи ждёт завершения worker. Если чужой код блокирует loop или не исполняет
отмену, stop ограничивает ожидание, переводит runtime в FAILED и выдаёт TimeoutError.
Принудительно завершить такой Python-код библиотека не может; оставшийся поток daemon
не удерживает выход процесса. Фабрики должны быстро возвращать клиент, а async-методы
обязаны поддерживать отмену. Синхронные SDK и собственные скрытые повторы не подходят.

`with runtime:` — синхронный контекстный менеджер. Async lifecycle, atexit и helper
для владения локальными handlers будут отдельными этапами. Сейчас приложение явно
вызывает stop/контекстный менеджер и снимает handler при необходимости.
Передавать живой runtime между процессами или использовать его после fork нельзя.

## Наблюдение за результатом

`runtime.stats()` возвращает неизменяемый RuntimeStats со счётчиками от момента создания.
Это безопасная копия под блокировкой, без секретов и текстов ошибок. Счётчики агрегированы
по всем получателям; расширенные показатели по получателям планируются в шаге 3.

| Счётчик | Единица и смысл |
| --- | --- |
| admitted | Уведомления, принятые входной очередью |
| suppressed | Записи, исключённые notify, контекстом/namespace или отсутствием подходящего правила |
| normalization_failed | Записи с ошибкой подготовки или размера |
| not_running | Записи, отклонённые до запуска/при остановке; также гонка stop с подготовкой |
| ingress_overflow | Уведомления, отклонённые полной входной очередью |
| routed | Доставки, принятые очередями получателей |
| destination_overflow | Доставки, отклонённые заполненным получателем |
| accepted | Попытки, принятые сервисом |
| failed_attempts | Попытки с известным отказом или rate limit |
| unknown_attempts | Попытки с неизвестным исходом, включая timeout и сбой адаптера |
| adapter_errors | Исключения/неверные ответы адаптера; входят также в unknown_attempts |
| expired | Доставки, срок которых истёк до начала send |
| shutdown_discarded_events | Остаток входной очереди при остановке |
| shutdown_discarded_deliveries | Остаток очередей получателей до начала send |
| shutdown_unknown | Активные send, отменённые при остановке |
| startup_failed / close_failed | Неудачные запуски runtime / закрытия каналов |

Один event может породить несколько доставок, поэтому admitted и accepted имеют разные
единицы. Suppressed пересекается с admitted для событий, исключённых маршрутизатором.
Записи, отфильтрованные самим logging до handler, вообще не входят в статистику runtime.
В этом этапе нет диагностического sink: вместо сообщений об ошибках доступны счётчики.

Handler исключает namespace `remote_watch.internal` и все записи контекста доставки,
включая сторонние библиотеки в рабочем loop и рекурсивные вызовы редактора/Formatter.
Контекст общий для runtime, поэтому логи одного канала не уходят через другой runtime.
Если адаптер сам создаёт отдельный поток, он обязан передать контекст или направлять
его диагностику во внутренний namespace; произвольные чужие потоки автоматически
не распознаются. Собственные неограниченные фоновые задачи адаптера контракту не соответствуют.

Критерии последующих этапов: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md),
общие гарантии: [ARCHITECTURE.md](../ARCHITECTURE.md).
