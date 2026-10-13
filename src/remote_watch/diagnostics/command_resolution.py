# Локальная утилита разбора и явного согласования конфликтующих результатов команд.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261013-235443
#
# Функции:
# -> main(): Выбор безопасного просмотра либо явного применения решения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from remote_watch.commands.resolution import ResolutionError, export_evidence, pending_summary, reconcile


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Выбор безопасного просмотра либо явного применения решения
#------------------------------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:

    """Run explicit local journal reconciliation with no provider or callback operations.

    :param argv: Optional argument vector for tests and embedding.
    :type argv: list[str] | None
    :return: Zero on success, two on a safe diagnosed failure.
    :rtype: int
    """

    # argv — пути не выводятся; argparse выбирает только фиксированные действия.
    parser = argparse.ArgumentParser(description="Offline command outcome resolution; see COMMAND_RESOLUTION.md.")
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="Read-only bounded pending summary, no payloads.")
    listing.add_argument("--journal", type=Path, required=True)
    export = actions.add_parser("export", help="Export one COMPLETED result into a NEW private evidence file.")
    export.add_argument("--journal", type=Path, required=True)
    export.add_argument("--sequence", type=int, required=True)
    export.add_argument("--output", type=Path, required=True)
    preview = actions.add_parser("preview", help="Read-only comparison with the actual hub journal.")
    preview.add_argument("--journal", type=Path, required=True)
    preview.add_argument("--evidence", type=Path, required=True)
    apply = actions.add_parser("apply", help="Explicit operator decision; stop hub AND application first.")
    apply.add_argument("--journal", type=Path, required=True)
    apply.add_argument("--evidence", type=Path, required=True)
    apply.add_argument("--expect", required=True, help="Exact fingerprint returned by preview.")
    apply.add_argument("--confirm-owners-stopped", action="store_true",
                       help="I verified the hub and application execution owner have stopped.")
    args = parser.parse_args(argv)
    try:
        if args.action == "list":
            report = pending_summary(args.journal)
        elif args.action == "export":
            export_evidence(args.journal, args.sequence, args.output)
            report = {"status": "exported", "private_evidence": True}
        else:
            report = reconcile(args.journal, args.evidence,
                               expected=args.expect if args.action == "apply" else None,
                               owners_stopped=args.confirm_owners_stopped if args.action == "apply" else False)
    except ResolutionError as error:
        # Все ResolutionError создаются внутри утилиты с постоянными кодами.
        print(json.dumps({"status": "failed", "code": str(error)}))
        return 2
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError):
        # Нельзя печатать SQLite/JSON/OS-текст: он может содержать путь или payload.
        print(json.dumps({"status": "failed", "code": "journal_or_evidence_invalid"}))
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Запуск локальной административной утилиты
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
