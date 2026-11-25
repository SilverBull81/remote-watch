# Явная проверка TimeAPI и допуска показаний через TrustedClock без отправки уведомлений.
#
# Version 1.0.4
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261125-102159
#
# Функции:
# -> _probe(): Конечная серия запросов к выбранному источнику времени
#
# -> main(): Параметры проверки времени из командной строки


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
from pathlib import Path
from time import monotonic

from remote_watch.adapters.timeapi import TimeApiTimeSource
from remote_watch.adapters.trusted_time import RedundantTimeSource, parse_time_sources
from remote_watch.adapters.utctime import UtcTimeSource
from remote_watch.commands.time import TimeUnavailable, TrustedClock
from remote_watch.gateway.command_config import read_command_json

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Конечная серия запросов к выбранному источнику времени
#------------------------------------------------------------------------------------------------------------------
async def _probe(
    samples: int,
    interval: float,
    source_name: str = "redundant",
    config: str | None = None,
    ) -> int:

    """
    Check configured UTC sources against the default command-time policy.

    :param samples: Bounded number of independent requests.
    :type samples: int

    :param interval: Pause between requests in seconds.
    :type interval: float

    :param source_name: Explicit single provider or the redundant configured set.
    :type source_name: str

    :param config: Optional command gateway JSON path; only trusted_time is used.
    :type config: str | None

    :return: Zero if every sample was accepted, otherwise one.
    :rtype: int
    """

    # samples — число измерений; ни бесконечного цикла, ни фонового потока нет.
    # interval — пауза между измерениями, секунды.
    # source_name — позволяет отдельно проверить каждый встроенный источник.
    # config — прочие разделы не используются для регистрации или отправки сообщений.

    settings = None
    if config is not None:
        raw = read_command_json(Path(config))
        if "trusted_time" in raw and raw["trusted_time"] is None:
            raise ValueError("invalid trusted time configuration")
        settings = parse_time_sources(raw.get("trusted_time"))
    source = (TimeApiTimeSource() if source_name == "timeapi" else UtcTimeSource()
              if source_name == "utctime" else RedundantTimeSource(settings))
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
                "source": source_name,
                "elapsed_seconds": round(monotonic() - started, 4),
                "lower_utc": datetime.fromtimestamp(sample.lower_utc, timezone.utc).isoformat(),
                "upper_utc": datetime.fromtimestamp(sample.upper_utc, timezone.utc).isoformat(),
                "uncertainty_seconds": round(sample.upper_utc - sample.lower_utc, 6),
            }
        except TimeUnavailable:
            failed = True
            anchor_valid = False
            try:
                clock.bounds()
                anchor_valid = True
            except TimeUnavailable:
                pass
            row = {"sample": index + 1, "status": "time_unavailable", "source": source_name,
                   "anchor_valid": anchor_valid, "refresh_error": clock.refresh_error}

        if isinstance(source, RedundantTimeSource):
            row["time_health"] = source.stats()

        # Фиксированные поля не содержат токенов, паролей, исходных HTTP-ответов или исключений.
        print(json.dumps(row), flush=True)
        if index + 1 < samples:
            await asyncio.sleep(interval)

    return int(failed)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Параметры проверки времени из командной строки
#------------------------------------------------------------------------------------------------------------------
def main(
    argv: Sequence[str] | None = None,
    ) -> int:

    """
    Run an explicitly requested public time probe.

    :param argv: Command arguments, or None to read the process command line.
    :type argv: Sequence[str] | None

    :return: Zero for accepted samples, one for failures, or two for invalid arguments.
    :rtype: int
    """

    # argv — аргументы запуска; None означает обычный запуск через python -m.

    parser = argparse.ArgumentParser(description="Проверка источников UTC без изменения часов Windows/Linux.")
    parser.add_argument("--samples", type=int, default=3, help="Число запросов: 1–20")
    parser.add_argument("--interval", type=float, default=2.0, help="Пауза между запросами: 1–60 секунд")
    parser.add_argument("--source", choices=("redundant", "timeapi", "utctime"), default="redundant")
    parser.add_argument("--config", help="Командный JSON; используется только раздел trusted_time")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 20 or not 1 <= args.interval <= 60:
        parser.error("Допустимы 1–20 запросов с паузой 1–60 секунд.")

    if args.config is not None and args.source != "redundant":
        parser.error("--config используется только с --source redundant")
    try:
        return asyncio.run(_probe(args.samples, args.interval, args.source, args.config))
    except Exception:
        print(json.dumps({"status": "config_error"}), flush=True)
        return 2
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
