# Конечная проверка команд с телефона на безопасном тестовом состоянии.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261002-143102
#
# Классы:
# -> SmokeApplication: Безопасное приложение для проверки команд с телефона.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> status(): Проверка флага подставной загрузки.
#    -> suspend_load(): Приостановка подставной загрузки.
#    -> resume_load(): Разрешение только подставной загрузки.
#    -> report(): Ограниченный отчёт о тестовом состоянии.
#    Служебные методы:
#    -> _record(): Учёт результата подставного обработчика.
#
# Функции:
# -> run_smoke(): Конечный запуск тестового приложения и сохранение отчёта.
# -> main(): Разбор параметров и явный запуск выбранного режима.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import json
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from time import monotonic, sleep

from remote_watch import CommandRegistry, RemoteWatcher, WatcherConfig
from remote_watch.gateway.command_config import CommandConfigError, load_command_client
from remote_watch.gateway.commands import CommandParser


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Безопасное приложение для проверки команд с телефона
#------------------------------------------------------------------------------------------------------------------
class SmokeApplication:
    """Expose harmless application-owned commands over an isolated in-memory state."""


    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Create a local loading flag and a bounded observation history."""

        self.paused = threading.Event()
        self._lock = threading.Lock()
        self._history: list[str] = []
        self._count = 0
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка флага подставной загрузки
    #--------------------------------------------------------------------------------------------------------------
    def status(self) -> str:

        """Report the synthetic state without observing a real application.

        :return: Human-readable state of the synthetic loading flag.
        :rtype: str
        """

        return self._record("status")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Приостановка подставной загрузки
    #--------------------------------------------------------------------------------------------------------------
    def suspend_load(self) -> str:

        """Pause only the synthetic loading flag.

        :return: Confirmation that the synthetic loading flag pauses work.
        :rtype: str
        """

        self.paused.set()
        return self._record("suspend")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Разрешение только подставной загрузки
    #--------------------------------------------------------------------------------------------------------------
    def resume_load(self) -> str:

        """Resume only the synthetic loading flag.

        :return: Confirmation that the synthetic loading flag permits work.
        :rtype: str
        """

        self.paused.clear()
        return self._record("resume")
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Ограниченный отчёт о тестовом состоянии
    #--------------------------------------------------------------------------------------------------------------
    def report(self) -> dict[str, object]:

        """Return bounded nonsecret observations without command IDs or connection settings.

        :return: Bounded nonsecret smoke observations and their sequence verdict.
        :rtype: dict[str, object]
        """

        with self._lock:
            history = tuple(self._history)
            expected = ("status:running", "suspend:paused", "status:paused", "resume:running")
            verified = any(history[index:index + 4] == expected for index in range(max(0, len(history) - 3)))
            return {"kind": "command_smoke", "callback_count": self._count,
                    "sequence_verified": verified, "observations": list(history)}
    #--------------------------------------------------------------------------------------------------------------


    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Учёт результата подставного обработчика
    #--------------------------------------------------------------------------------------------------------------
    def _record(
        self,
        name: str,
    ) -> str:

        """Record one callback completion with the observed test flag.

        :param name: Application command or smoke observation name.
        :type name: str

        :return: Human-readable current test flag after recording its observation.
        :rtype: str
        """

        # name — имя пользовательской команды либо наблюдения smoke.

        state = "paused" if self.paused.is_set() else "running"
        with self._lock:
            self._count += 1
            self._history.append(name + ":" + state)
            self._history[:] = self._history[-64:]
        # Ответ описывает флаг тестового приложения, а не окончание реальной загрузки.
        return "Тестовая загрузка приостановлена." if self.paused.is_set() else "Тестовая загрузка разрешена."
    #--------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Конечный запуск тестового приложения и сохранение отчёта
#------------------------------------------------------------------------------------------------------------------
def run_smoke(
    config: Path,
    duration: float,
    report: Path | None = None,
) -> int:

    """Run a finite explicit field scenario and always stop the owned watcher.

    :param config: Explicit credentials, access rules and finite limits.
    :type config: Path

    :param duration: Relative local lifetime in seconds.
    :type duration: float

    :param report: Optional path for a nonsecret finite smoke report.
    :type report: Path | None

    :return: Process exit status: zero for a completed successful check.
    :rtype: int
    """

    # config — явные учётные данные, права и конечные пределы.
    # duration — относительный срок на локальных часах, секунды.
    # report — необязательный путь безопасного итогового отчёта smoke.

    if not 5 <= duration <= 3600:
        raise ValueError("smoke duration outside limits")
    application = SmokeApplication()
    registry = CommandRegistry.from_callbacks({"status": application.status, "check_load": application.status,
        "resume_load": application.resume_load, "suspend_load": application.suspend_load})
    client = load_command_client(config, registry)
    watcher = RemoteWatcher(WatcherConfig(identity=client.registration.identity, commands=registry),
                            command_client=client)

    try:
        watcher.start()
        print("Smoke готов. Отправьте /rw адрес status, suspend_load, status, resume_load — по одной команде.",
              flush=True)
        deadline = monotonic() + duration
        while monotonic() < deadline:
            sleep(min(0.2, max(0, deadline - monotonic())))
    finally:
        watcher.stop()
        result = application.report()
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        print(payload, end="", flush=True)
        if report is not None:
            report.write_text(payload, encoding="utf-8")
    return 0 if result["sequence_verified"] else 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Разбор параметров и явный запуск выбранного режима
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Run an explicitly configured harmless command test without printing private settings.

    :param argv: Explicit CLI arguments, or None for the process arguments.
    :type argv: Sequence[str] | None

    :return: Process exit status: zero for a completed successful check.
    :rtype: int
    """

    # argv — параметры CLI; None читает аргументы процесса.

    parser: argparse.ArgumentParser = CommandParser(description="Конечный smoke команд тестового приложения.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    try:
        return run_smoke(args.config, args.duration, args.report)
    except KeyboardInterrupt:
        return 130
    except CommandConfigError as error:
        print(f"Command smoke: code={error.code} field={error.field}. {error.hint}", flush=True)
        return 2
    except Exception:
        print("Command smoke: операция не завершена; значения и детали скрыты.", flush=True)
        return 2
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Явный запуск CLI
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
