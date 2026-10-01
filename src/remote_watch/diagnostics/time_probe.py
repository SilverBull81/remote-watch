# Явная проверка TimeAPI и допуска показаний через TrustedClock без отправки уведомлений.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-195308
#
# Функции:
# -> main(): Разбор параметров и запуск конечной серии запросов.
# -> _probe(): Измерение интервала UTC и проверка его пригодности.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from time import monotonic

from remote_watch.adapters.timeapi import TimeApiTimeSource
from remote_watch.commands.time import TimeUnavailable, TrustedClock


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Конечная серия запросов к выбранному источнику времени
#------------------------------------------------------------------------------------------------------------------
async def _probe(
    samples: int,
    interval: float,
) -> int:

    """Check public TimeAPI samples against the default command-time policy.

    :param samples: Bounded number of independent requests.
    :type samples: int

    :param interval: Pause between requests in seconds.
    :type interval: float

    :return: Zero if every sample was accepted, otherwise one.
    :rtype: int
    """

    # samples — число измерений; ни бесконечного цикла, ни фонового потока нет.
    # interval — пауза между измерениями, секунды.

    source = TimeApiTimeSource()
    clock = TrustedClock()
    failed = False

    for index in range(samples):
        # Проверяем именно библиотечный путь: TLS, JSON и политику ширины интервала.
        # UTC машины не используется ни для оценки точности, ни для допуска показания.
        started = monotonic()
        try:
            await clock.refresh(source)
            sample = clock.bounds()
            row = {
                "sample": index + 1,
                "status": "accepted",
                "source": "timeapi.io",
                "elapsed_seconds": round(monotonic() - started, 4),
                "lower_utc": datetime.fromtimestamp(sample.lower_utc, timezone.utc).isoformat(),
                "upper_utc": datetime.fromtimestamp(sample.upper_utc, timezone.utc).isoformat(),
                "uncertainty_seconds": round(sample.upper_utc - sample.lower_utc, 6),
                "assumed_accuracy_seconds": 1.0,
            }
        except TimeUnavailable:
            failed = True
            row = {"sample": index + 1, "status": "time_unavailable", "source": "timeapi.io"}

        # Фиксированные поля не содержат credentials, сырых HTTP-ответов или исключений.
        print(json.dumps(row), flush=True)
        if index + 1 < samples:
            await asyncio.sleep(interval)

    return int(failed)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Параметры проверки времени из командной строки
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Run an explicitly requested public time probe.

    :param argv: Command arguments, or None to read the process command line.
    :type argv: Sequence[str] | None

    :return: Zero for accepted samples, one for failures, or two for invalid arguments.
    :rtype: int
    """

    # argv — аргументы запуска; None означает обычный запуск через python -m.

    parser = argparse.ArgumentParser(description="Проверка TimeAPI без изменения часов Windows/Linux.")
    parser.add_argument("--samples", type=int, default=3, help="Число запросов: 1–20")
    parser.add_argument("--interval", type=float, default=2.0, help="Пауза между запросами: 1–60 секунд")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 20 or not 1 <= args.interval <= 60:
        parser.error("Допустимы 1–20 запросов с паузой 1–60 секунд.")

    return asyncio.run(_probe(args.samples, args.interval))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явная проверка доступности и пригодности времени
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    # Русская справка и результат должны читаться и при перенаправлении вывода в файл.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
