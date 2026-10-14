# Кооперативная замена пользовательского трея через его отдельную блокировку и закрытые файлы.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-212332
#
# Функции:
# -> prepare_launch(): Проверка владельца и ограниченное ожидание его добровольного выхода
#
# -> publish_owner(): Публикация версии и поколения готового контроллера
#
# -> restart_requested(): Проверка запроса только для собственного поколения
#
# -> clear_owner(): Удаление записи до освобождения блокировки текущим владельцем
#
# -> _read_record(): Ограниченное чтение служебной записи без перехода по ссылкам
#
# -> _write_record(): Атомарная замена маленькой записи внутри закрытого каталога


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from secrets import token_hex
from time import monotonic, sleep

from remote_watch.commands.source_store import ProcessLock
from remote_watch.commands.storage import StoreConflict

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка владельца и ограниченное ожидание его добровольного выхода
#------------------------------------------------------------------------------------------------------------------
def prepare_launch(
    path: Path,
    package_version: str,
    *,
    restart: bool = False,
    timeout: float = 8,
    ) -> str:

    """
    Request replacement without killing processes or changing service state.

    :param path: Existing controller lock in the operator's private session directory.
    :type path: Path

    :param package_version: Installed version expected from the replacement controller.
    :type package_version: str

    :param restart: Replace even a controller of the same installed version.
    :type restart: bool

    :param timeout: Finite wait for the current owner to release its lock.
    :type timeout: float

    :return: Fixed launch status, not proof that a newly spawned GUI is ready.
    :rtype: str
    """

    # path/package_version — каталог закрыт вызывающим кодом; PID из файлов не используются.
    # restart/timeout — повторный sync той же версии ничего не перезапускает; ожидание конечно.

    if path.is_symlink() or path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("tray_control_path")

    guard = ProcessLock(path)
    try:
        guard.open()
    except StoreConflict:
        pass
    else:
        guard.close()
        return "launch_requested"

    # Запись не является доказательством жизни: выше уже проверена настоящая OS-блокировка.
    # Старый контроллер не знает этого протокола. Не убиваем его и не обещаем замену.
    previous = _read_record(path.with_suffix(".owner.json"))
    if previous is None:
        return "restart_required"
    if not restart and previous["version"] == package_version:
        return "already_running"

    _write_record(path.with_suffix(".restart.json"), previous)
    deadline = monotonic() + timeout
    while True:
        try:
            guard.open()
        except StoreConflict:
            current = _read_record(path.with_suffix(".owner.json"))
            if (current is not None and current["generation"] != previous["generation"]
                    and current["version"] == package_version):
                return "already_running"
            if monotonic() >= deadline:
                return "restart_timeout"
            sleep(min(.05, max(0, deadline - monotonic())))
        else:
            guard.close()
            return "restart_requested"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Публикация версии и поколения готового контроллера
#------------------------------------------------------------------------------------------------------------------
def publish_owner(
    path: Path,
    generation: str,
    package_version: str,
    ) -> None:

    """
    Publish only after the GUI is ready and while holding the controller lock.

    :param path: Controller lock whose current owner performs this write.
    :type path: Path

    :param generation: Random lifetime identifier, independent of OS process identifiers.
    :type generation: str

    :param package_version: Version captured when this controller started.
    :type package_version: str
    """

    # path/generation/package_version — запись не содержит config paths, SID, токенов или PID.

    _write_record(path.with_suffix(".owner.json"), {"generation": generation, "version": package_version})
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка запроса только для собственного поколения
#------------------------------------------------------------------------------------------------------------------
def restart_requested(
    path: Path,
    generation: str,
    ) -> bool:

    """
    Ignore stale restart requests left by an earlier controller lifetime.

    :param path: Current controller lock, isolated by service, config and session.
    :type path: Path

    :param generation: Random identifier of the controller receiving the request.
    :type generation: str

    :return: Whether this exact generation has been asked to exit.
    :rtype: bool
    """

    # path/generation — прежний запрос не должен закрыть уже обновлённую иконку.

    record = _read_record(path.with_suffix(".restart.json"))
    return record is not None and record["generation"] == generation
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Удаление записи до освобождения блокировки текущим владельцем
#------------------------------------------------------------------------------------------------------------------
def clear_owner(
    path: Path,
    generation: str,
    ) -> None:

    """
    Remove only this lifetime's status while its process still owns the lock.

    :param path: Controller lock still held by the caller.
    :type path: Path

    :param generation: Lifetime identifier to compare before removing the record.
    :type generation: str
    """

    # path/generation — lock и restart-файл не удаляем; чужую запись не трогаем.

    target = path.with_suffix(".owner.json")
    record = _read_record(target)
    if record is not None and record["generation"] == generation:
        target.unlink(missing_ok=True)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Ограниченное чтение служебной записи без перехода по ссылкам
#------------------------------------------------------------------------------------------------------------------
def _read_record(
    path: Path,
    ) -> dict[str, str] | None:

    """
    Read one bounded private record and reject unexpected structure without diagnostics leakage.

    :param path: Fixed sidecar path derived from the controller lock.
    :type path: Path

    :return: Valid generation and version, or None for an absent or unusable record.
    :rtype: dict[str, str] | None
    """

    # path — не читаем ссылки, большие файлы или произвольные поля от старого/чужого формата.

    try:
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            return None
        with path.open("rb") as stream:
            raw = stream.read(513)
        if len(raw) > 512:
            return None
        value = json.loads(raw)
        if (type(value) is not dict or set(value) != {"generation", "version"}
                or type(value["generation"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", value["generation"])
                or type(value["version"]) is not str
                or not re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,63}", value["version"])):
            return None
        return value
    except (OSError, ValueError, UnicodeError):
        return None
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Атомарная замена маленькой записи внутри закрытого каталога
#------------------------------------------------------------------------------------------------------------------
def _write_record(
    path: Path,
    record: dict[str, str],
    ) -> None:

    """
    Replace a bounded private sidecar without exposing a partial JSON file to another process.

    :param path: Fixed record path inside an already protected operator directory.
    :type path: Path

    :param record: Generation and installed-version fields owned by this protocol.
    :type record: dict[str, str]
    """

    # path/record — временный файл создаём исключительно; replace не пишет сквозь целевую ссылку.

    temporary = path.with_name(path.name + "." + token_hex(16) + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(record, separators=(",", ":")).encode("ascii"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль _tray_control не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
