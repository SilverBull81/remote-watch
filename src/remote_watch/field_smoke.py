# Длительная проверка доставки с разных серверов и журналом результатов без секретов.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260929-185913
#
# Классы:
# -> FieldConfig: Продолжительность и отправитель полевой проверки.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек и ограничений.
#
# -> _Journal: Ограниченный буфер безопасных сведений о попытках.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска работы.
#    Интерфейс:
#    -> record(): Добавление результата в ограниченный буфер.
#    -> flush(): Запись накопленных результатов вне рабочего потока.
#
# -> _ObservedChannel: Канал с записью результатов без доступа к диску из worker.
#    Конструктор:
#    -> __init__(): Подготовка состояния без запуска работы.
#    Интерфейс:
#    -> open(): Открытие канала в принадлежащем ему цикле событий.
#    -> send(): Одна попытка отправки без собственного цикла повторов.
#    -> close(): Закрытие канала и освобождение временных данных.
#
# Функции:
# -> _utc(): Текущее время UTC для сопоставления серверов.
# -> _write(): Запись одной строки отчёта с немедленным flush.
# -> _redact(): Удаление известного тестового секрета из удалённого текста.
# -> _sample(): Локальные записи и одно контрольное уведомление.
# -> _run(): Ограниченная по времени проверка с настоящим runtime.
# -> _destination(): Подготовка выбранного адаптера из локальных настроек.
# -> main(): Разбор параметров явного запуска и безопасные сообщения об ошибках.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
import logging
import math
import os
import platform
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from functools import partial
from importlib.metadata import version
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from ._validation import require_number
from .channels import NotificationChannel
from .config import Destination, RetryPolicy, Route, RuntimeConfig, WatcherConfig
from .delivery import Delivery, DeliveryResult, DeliveryStatus
from .events import Identity
from .smoke import _load_settings, _Parser
from .watcher import RemoteWatcher, RotatingFileConfig


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Продолжительность и отправитель полевой проверки
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class FieldConfig:
    """Bound a finite field run; phone display remains a manual observation."""

    identity: Identity                  # Явные сведения о сервере и экземпляре проверки.
    duration: float = 86400             # Продолжительность наблюдения, секунды.
    interval: float = 900               # Интервал между контрольными сообщениями, секунды.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек и ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject infinite runs and excessive sample counts before network startup."""

        if not isinstance(self.identity, Identity):
            raise TypeError("identity must be Identity")
        require_number(self.duration, "duration")
        require_number(self.interval, "interval")

        if self.duration > 604800 or self.interval < 1 or math.ceil(self.duration / self.interval) > 1000:
            raise ValueError("field run requires at most seven days and 1000 samples, interval >= 1 second")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ограниченный буфер безопасных сведений о попытках
#------------------------------------------------------------------------------------------------------------------
class _Journal:
    """Buffer bounded attempt diagnostics; only the application thread writes files."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Prepare a bounded buffer without allocating files or worker threads."""

        self._lock = threading.Lock()
        self._records: deque[dict[str, object]] = deque()
        self.dropped = 0
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Добавление результата в ограниченный буфер
    #--------------------------------------------------------------------------------------------------------------
    def record(
        self,
        record: dict[str, object],
        ) -> None:

        """Append one sanitized diagnostic or count its loss without waiting for disk.

        :param record: Sanitized diagnostic record.
        :type record: dict[str, object]
        """

        # record - безопасные поля одной записи отчёта.

        with self._lock:
            if len(self._records) >= 1024:
                self.dropped += 1
            else:
                self._records.append(record)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запись накопленных результатов вне рабочего потока
    #--------------------------------------------------------------------------------------------------------------
    def flush(
        self,
        stream: TextIO,
        ) -> None:

        """Drain a snapshot of diagnostics outside the worker and its buffer lock.

        :param stream: Caller-owned output stream.
        :type stream: TextIO
        """

        # stream - поток отчёта, открытый вызывающим кодом.

        with self._lock:
            records = tuple(self._records)
            self._records.clear()
        for record in records:
            _write(stream, record)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Канал с записью результатов без доступа к диску из worker
#------------------------------------------------------------------------------------------------------------------
class _ObservedChannel:
    """Delegate one provider attempt and capture only safe result fields."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        factory: Callable[[], NotificationChannel],
        journal: _Journal,
        provider: str,
        ) -> None:

        """Create the actual channel lazily in the runtime worker.

        :param factory: Zero-argument lazy channel factory.
        :type factory: Callable[[], NotificationChannel]

        :param journal: Bounded diagnostic buffer.
        :type journal: _Journal

        :param provider: Provider selected by the test.
        :type provider: str
        """

        # factory - ленивая фабрика настоящего канала.
        # journal - буфер сведений о попытках.
        # provider - сервис, выбранный для проверки.

        self._channel = factory()
        self._journal = journal
        self._provider = provider
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие канала в принадлежащем ему цикле событий
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Open the underlying client in its owning loop."""

        await self._channel.open()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправки без собственного цикла повторов
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Observe one attempt without adding retries or swallowing cancellation.

        :param delivery: Immutable delivery attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        began = time.monotonic()
        result = DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="attempt_interrupted")
        try:
            result = await self._channel.send(delivery)
            if not isinstance(result, DeliveryResult):
                raise TypeError("invalid channel result")
            return result
        finally:
            # Не записываем настройки канала, адрес чата, токен, payload или текст исключения.
            safe = result if isinstance(result, DeliveryResult) else DeliveryResult(status=DeliveryStatus.UNKNOWN)
            self._journal.record({
                "kind": "attempt", "utc": _utc(), "provider": self._provider,
                "event_id": delivery.notification.event_id, "delivery_id": delivery.delivery_id,
                "sample": delivery.notification.correlation_id, "attempt": delivery.attempt,
                "status": safe.status.value, "reason": safe.reason_code,
                "http_status": safe.http_status, "provider_code": safe.provider_code,
                "message_bytes": safe.message_bytes, "request_bytes": safe.request_bytes,
                "elapsed_seconds": round(time.monotonic() - began, 3),
            })
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие канала и освобождение временных данных
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Close the actual client, including after incomplete startup."""

        await self._channel.close()
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Текущее время UTC для сопоставления серверов
#------------------------------------------------------------------------------------------------------------------
def _utc() -> str:

    """Return an explicit UTC timestamp for cross-server comparison.

    :return: The value described by this operation.
    :rtype: str
    """

    return datetime.now(timezone.utc).isoformat()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запись одной строки отчёта с немедленным flush
#------------------------------------------------------------------------------------------------------------------
def _write(
    stream: TextIO,
    record: dict[str, object],
    ) -> None:

    """Write a complete JSONL record and flush it for inspection during the run.

    :param stream: Caller-owned output stream.
    :type stream: TextIO

    :param record: Sanitized diagnostic record.
    :type record: dict[str, object]
    """

    # stream - поток отчёта, открытый вызывающим кодом.
    # record - безопасные поля одной записи отчёта.

    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Удаление известного тестового секрета из удалённого текста
#------------------------------------------------------------------------------------------------------------------
def _redact(text: str) -> str:

    """Remove only the known synthetic marker used by this test.

    :param text: Text to shorten.
    :type text: str

    :return: The value described by this operation.
    :rtype: str
    """

    # text - исходный текст.

    return text.replace("FIELD_TEST_SECRET", "[удалено]")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Локальные записи и одно контрольное уведомление
#------------------------------------------------------------------------------------------------------------------
def _sample(
    logger: logging.Logger,
    run_id: str,
    number: int,
    ) -> str:

    """Exercise local-only records and one remote-eligible record per sample.

    :param logger: Ordinary application logger.
    :type logger: logging.Logger

    :param run_id: Unique field run identifier.
    :type run_id: str

    :param number: One-based sample number.
    :type number: int

    :return: The value described by this operation.
    :rtype: str
    """

    # logger - обычный logger проверяемого приложения.
    # run_id - общий идентификатор запуска проверки.
    # number - номер контрольного сообщения.

    label = f"run={run_id} seq={number}"
    logger.info("%s LOCAL_ONLY_INFO", label)
    logger.error("%s LOCAL_ONLY_SUPPRESSED", label, extra={"notify": False})
    case = (number - 1) % 4
    extra: dict[str, object] = {"correlation_id": str(number), "topic": "field", "tags": ("synthetic",)}

    # Префикс номера находится в начале текста: он должен пережить усечение длинного сообщения.
    if case == 0:
        kind = "error"
        logger.error("%s case=error Контроль доставки: кириллица и emoji 📡", label, extra=extra)
    elif case == 1:
        kind = "forced_info"
        logger.info("%s case=forced_info Явное уведомление INFO", label, extra={**extra, "notify": True})
    elif case == 2:
        kind = "exception_redaction"
        try:
            raise RuntimeError("Синтетическое исключение FIELD_TEST_SECRET")
        except RuntimeError:
            logger.exception("%s case=exception_redaction Проверка исключения", label, extra=extra)
    else:
        kind = "long_text"
        logger.error("%s case=long_text %s", label, "Длинный текст 📡 " * 1200, extra=extra)
    return kind
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ограниченная по времени проверка с настоящим runtime
#------------------------------------------------------------------------------------------------------------------
def _run(
    config: FieldConfig,
    destinations: tuple[Destination, ...],
    output: Path,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ) -> int:

    """Run a finite real runtime scenario and persist a sanitized attempt journal.

    :param config: Validated settings.
    :type config: FieldConfig

    :param destinations: One or two configured providers.
    :type destinations: tuple[Destination, ...]

    :param output: New JSONL report path.
    :type output: Path

    :param monotonic: Application scheduling clock.
    :type monotonic: Callable[[], float]

    :param sleep: Application wait function.
    :type sleep: Callable[[float], None]

    :return: The value described by this operation.
    :rtype: int
    """

    # config - проверенные настройки выбранного сценария.
    # destinations - один или два выбранных получателя.
    # output - путь к новому локальному отчёту.
    # monotonic - часы расписания контрольных сообщений.
    # sleep - ожидание следующего шага расписания.

    if not destinations or len(destinations) > 2:
        raise ValueError("field smoke requires one or two destinations")
    if output.suffix != ".jsonl" or output.with_suffix(".log").exists():
        raise ValueError("use a new .jsonl output path")

    # Файл отчёта создаём эксклюзивно: повторный запуск не затирает уже собранные наблюдения.
    output.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    journal = _Journal()
    observed = tuple(replace(item, channel_factory=partial(
        _ObservedChannel, item.channel_factory, journal, item.destination_id,
    )) for item in destinations)
    logger = logging.getLogger("remote_watch.field." + run_id)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    watcher = RemoteWatcher(WatcherConfig(
        identity=config.identity, destinations=observed,
        routes=(Route(destination_ids=tuple(item.destination_id for item in observed),
                      topic="field", required_tags=("synthetic",)),),
        runtime=RuntimeConfig(ingress_capacity=64, shutdown_timeout=15),
    ), logger=logger, file=RotatingFileConfig(path=output.with_suffix(".log"),
                                             max_bytes=1024 * 1024, backup_count=2), redactor=_redact)
    samples = 0
    interrupted = False
    failure = False

    with output.open("x", encoding="utf-8", newline="\n") as stream:
        print(f"Полевой smoke: run={run_id}; отчёт: {output}")
        _write(stream, {"kind": "run", "schema_version": 1, "run_id": run_id, "utc": _utc(),
                        "identity": asdict(config.identity), "duration_seconds": config.duration,
                        "interval_seconds": config.interval, "providers": [d.destination_id for d in observed],
                        "python": platform.python_version(), "system": platform.system(),
                        "package_version": version("remote-watch")})
        try:
            with watcher:
                started = monotonic()
                due = started
                while monotonic() - started < config.duration:
                    now = monotonic()
                    if now >= due:
                        samples += 1
                        kind = _sample(logger, run_id, samples)
                        _write(stream, {"kind": "sample", "utc": _utc(), "sample": samples, "case": kind,
                                        "expected_per_provider": 1})
                        print(f"seq={samples} case={kind}: передано в logging.", flush=True)
                        # После паузы процесса не рассылаем накопившиеся номера залпом.
                        due = now + config.interval
                    journal.flush(stream)
                    sleep(min(0.5, max(0.0, due - monotonic()),
                              max(0.0, started + config.duration - monotonic())))
        except KeyboardInterrupt:
            interrupted = True
        except Exception:
            failure = True
        finally:
            # На выходе из with runtime уже завершил попытку очистки. Здесь только локальная запись.
            journal.flush(stream)
            stats = {d.destination_id: asdict(watcher.runtime.stats(d.destination_id)) for d in observed}
            complete = samples > 0 and not failure and not interrupted and journal.dropped == 0
            complete = complete and all(
                s["accepted"] == samples and not s["close_failed"] and not s["adapter_errors"]
                for s in stats.values()
            )
            _write(stream, {"kind": "summary", "utc": _utc(), "samples": samples, "stats": stats,
                            "runtime_state": watcher.runtime.state.value, "diagnostic_drops": journal.dropped,
                            "interrupted": interrupted, "failed": failure,
                            "provider_acceptance_complete": complete, "phone_display_verified": False})

    print(f"Полевой smoke завершён: samples={samples}; полный приём сервисами={complete}.")
    print("Получение, задержки и дубликаты на телефоне сопоставьте с номерами run/seq в отчёте.")
    return 130 if interrupted else 0 if complete else 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка выбранного адаптера из локальных настроек
#------------------------------------------------------------------------------------------------------------------
def _destination(
    provider: str,
    settings: dict[str, object],
    token_env: str,
    ) -> Destination:

    """Build provider settings using the same local credential aliases as one-message smoke.

    :param provider: Provider selected by the test.
    :type provider: str

    :param settings: Local provider settings.
    :type settings: dict[str, object]

    :param token_env: Temporary service credential environment name.
    :type token_env: str

    :return: The value described by this operation.
    :rtype: Destination
    """

    # provider - сервис, выбранный для проверки.
    # settings - настройки провайдера из локального файла.
    # token_env - имя временной переменной с токеном.

    from .adapters.ntfy import NtfyConfig
    from .adapters.telegram import TelegramConfig

    policy = RetryPolicy(max_attempts=3, ttl=120)
    if provider == "telegram":
        config = TelegramConfig(token_env=token_env, chat_id=settings.get("chat_id", settings.get("chat")),
                                endpoint=settings.get("endpoint", "https://api.telegram.org"),
                                message_thread_id=settings.get("message_thread_id"),
                                allow_http=settings.get("allow_http", False))
    else:
        config = NtfyConfig(token_env=token_env, topic=settings.get("topic", settings.get("chat")),
                           endpoint=settings.get("endpoint", "https://ntfy.sh"),
                           allow_http=settings.get("allow_http", False))
    return config.destination(provider, retry=policy, outstanding_capacity=16)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Разбор параметров явного запуска и безопасные сообщения об ошибках
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Run only an explicitly requested field scenario with bounded duration and safe errors.

    :param argv: Explicit command arguments or None for process arguments.
    :type argv: Sequence[str] | None

    :return: The value described by this operation.
    :rtype: int
    """

    # argv - аргументы команды либо параметры процесса.

    parser = _Parser(description="Длительная проверка Remote Watch с локальным отчётом.")
    parser.add_argument("provider", choices=("telegram", "ntfy", "both"))
    parser.add_argument("--credentials", type=Path, default=Path("credentials.local.json"))
    parser.add_argument("--region", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--duration", type=float, default=86400, help="Продолжительность в секундах")
    parser.add_argument("--interval", type=float, default=900, help="Интервал в секундах")
    parser.add_argument("--output", type=Path)
    try:
        args = parser.parse_args(argv)
        config = FieldConfig(identity=Identity(service="remote-watch-field", environment="field-test",
                                               region=args.region, host=args.host, instance_id=args.instance_id),
                             duration=args.duration, interval=args.interval)
        providers = ("telegram", "ntfy") if args.provider == "both" else (args.provider,)
        output = args.output or Path("runs") / ("field-" + uuid4().hex + ".jsonl")
        with ExitStack() as cleanup:
            destinations = []
            for provider in providers:
                settings = _load_settings(args.credentials, provider)
                token_env = "REMOTE_WATCH_FIELD_" + uuid4().hex.upper()
                os.environ[token_env] = settings["token"]
                cleanup.callback(os.environ.pop, token_env, None)
                destinations.append(_destination(provider, settings, token_env))
            return _run(config, tuple(destinations), output)
    except (ValueError, TypeError, OSError, ImportError, RecursionError):
        print("Ошибка настройки field smoke. Проверьте параметры и локальный файл; см. docs/FIELD_SMOKE.md.")
        return 2
    except KeyboardInterrupt:
        print("Проверка прервана; проверьте наличие итоговой строки summary в отчёте.")
        return 130
    except Exception:
        print("Проверка завершилась с ошибкой; подробности скрыты. Проверьте локальный отчёт.")
        return 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явный запуск полевой проверки
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
