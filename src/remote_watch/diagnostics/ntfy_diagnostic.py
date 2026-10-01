# Короткая проверка разных размеров ntfy с безопасным отчётом о каждой попытке.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Функции:
# -> _delivery(): Подготовка уведомления с точным размером текста.
# -> _run(): Конечная серия отправок с безопасным отчётом.
# -> main(): Явный запуск диагностики ntfy одной командой.
#
# Константы:
# -> CASES: Десять сочетаний алфавита и размера текста перед усечением.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import os
import platform
from collections.abc import Sequence
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from remote_watch._validation import require_number
from remote_watch.adapters._common import render
from remote_watch.adapters.ntfy import NtfyChannel, NtfyConfig
from remote_watch.config import RetryPolicy
from remote_watch.diagnostics.smoke import _load_settings, _notification, _Parser
from remote_watch.notifications.delivery import Delivery, DeliveryResult, DeliveryStatus
from remote_watch.relay_protocol import DIAGNOSTIC_FIELDS

CASES = tuple((alphabet, size) for alphabet in ("ascii", "unicode") for size in (512, 3500, 4095, 4096, 5000))


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подготовка уведомления с точным размером текста
#------------------------------------------------------------------------------------------------------------------
def _delivery(
    alphabet: str,
    size: int,
    sample: int,
    run_id: str,
) -> Delivery:

    """Construct an exact rendered UTF-8 size before the adapter applies truncation.

    :param alphabet: Synthetic text alphabet.
    :type alphabet: str

    :param size: Maximum chunk size in bytes.
    :type size: int

    :param sample: One-based sample number.
    :type sample: int

    :param run_id: Synthetic run identifier.
    :type run_id: str

    :return: Synthetic bounded delivery for the selected ntfy payload size.
    :rtype: Delivery
    """

    # alphabet - алфавит синтетического текста.
    # size - размер читаемой части, байт.
    # sample - номер образца, начиная с единицы.
    # run_id - идентификатор диагностического запуска.

    event = replace(_notification(), message=f"ntfy-size run={run_id} seq={sample} {alphabet}\n")
    delivery = Delivery(notification=event, destination_id="ntfy", delivery_id=f"{run_id}-{sample}")
    remaining = size - len(render(delivery).encode("utf-8"))
    if remaining < 0:
        raise ValueError("diagnostic label exceeds the sample size")
    pattern = "Plain text " if alphabet == "ascii" else "Текст 📡 "
    count, tail = divmod(remaining, len(pattern.encode("utf-8")))
    return replace(delivery, notification=replace(event, message=event.message + pattern * count + "." * tail))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Конечная серия отправок с безопасным отчётом
#------------------------------------------------------------------------------------------------------------------
async def _run(
    settings: dict[str, object],
    output: Path,
    interval: float,
) -> int:

    """Send a finite series with no retries and retain only approved diagnostic fields.

    :param settings: Private local settings, never logged.
    :type settings: dict[str, object]

    :param output: New report path.
    :type output: Path

    :param interval: Seconds between attempts.
    :type interval: float

    :return: Zero when the diagnostic completes successfully, otherwise its failure exit code.
    :rtype: int
    """

    # settings - локальные настройки, не включаемые в отчёт.
    # output - путь нового файла отчёта.
    # interval - пауза между попытками в секундах.

    require_number(interval, "interval")
    if not 5 <= interval <= 60:
        raise ValueError("diagnostic interval must be between five and sixty seconds")

    run_id = uuid4().hex
    token_env = "REMOTE_WATCH_DIAGNOSTIC_" + run_id.upper()
    token = settings.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("missing diagnostic credential")
    config = NtfyConfig(topic=settings.get("topic", settings.get("chat")), token_env=token_env,
                        endpoint=settings.get("endpoint", "https://ntfy.sh"),
                        allow_http=settings.get("allow_http", False))
    channel = NtfyChannel(config, retry=RetryPolicy(max_attempts=1))
    accepted = attempted = 0
    completed = False

    # Отчёт открывается до установки временного токена и начала сети. Существующий
    # файл не перезаписывается; его путь и локальные настройки не попадают в записи.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"kind": "run", "schema_version": 1, "run_id": run_id,
            "package_version": version("remote-watch"), "python": platform.python_version(),
            "system": platform.system(), "planned": len(CASES), "interval_seconds": interval}) + "\n")
        stream.flush()
        os.environ[token_env] = token
        try:
            await asyncio.wait_for(channel.open(), 5)
            for sample, (alphabet, size) in enumerate(CASES, 1):
                if sample > 1:
                    await asyncio.sleep(interval)
                delivery = _delivery(alphabet, size, sample, run_id)
                try:
                    result = await asyncio.wait_for(channel.send(delivery), 11)
                except asyncio.TimeoutError:
                    result = DeliveryResult(status=DeliveryStatus.UNKNOWN, reason_code="diagnostic_timeout")
                attempted += 1
                accepted += result.status is DeliveryStatus.PROVIDER_ACCEPTED
                record = {"kind": "attempt", "sample": sample, "alphabet": alphabet,
                    "rendered_bytes": size, "status": result.status.value, "reason": result.reason_code,
                    **{name: getattr(result, name) for name in DIAGNOSTIC_FIELDS}}
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                print(f"seq={sample} {alphabet} size={size}: {result.status.value}; "
                      f"HTTP={result.http_status}; code={result.provider_code}; "
                      f"message={result.message_bytes}; JSON={result.request_bytes}", flush=True)

                # Квота и права не зависят от длины. Не расходуем остальные запросы
                # после такого отказа; неизвестные исходы никогда не повторяем сами.
                if result.http_status in (401, 403, 429) or result.status is DeliveryStatus.RATE_LIMITED:
                    break
            completed = attempted == len(CASES)
        finally:
            os.environ.pop(token_env, None)
            try:
                await asyncio.wait_for(channel.close(), 5)
            finally:
                stream.write(json.dumps({"kind": "summary", "attempted": attempted, "accepted": accepted,
                    "completed": completed, "provider_acceptance_complete": completed and accepted == attempted,
                    "phone_display_verified": False}) + "\n")
    return 0 if completed and accepted == attempted else 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Явный запуск диагностики ntfy одной командой
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Run only explicitly requested diagnostic publications, without revealing local settings.

    :param argv: Explicit command arguments or None.
    :type argv: Sequence[str] | None

    :return: Zero on success, otherwise a documented nonzero process exit code.
    :rtype: int
    """

    # argv - аргументы запуска либо текущая командная строка.

    parser = _Parser(description="Десять пробных ntfy-уведомлений разной длины, без повторов.")
    parser.add_argument("--credentials", type=Path, default=Path("credentials.local.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--interval", type=float, default=10)
    try:
        args = parser.parse_args(argv)
        settings = _load_settings(args.credentials, "ntfy")
        output = args.output or Path("runs") / ("ntfy-size-" + uuid4().hex + ".jsonl")
        return asyncio.run(_run(settings, output, args.interval))
    except KeyboardInterrupt:
        print("Диагностика прервана; смотрите незавершённый отчёт.")
        return 130
    except Exception:
        print("Диагностика не завершена. Проверьте extra ntfy, локальные настройки и новый путь отчёта.")
        return 2
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явный запуск диагностики ntfy
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
