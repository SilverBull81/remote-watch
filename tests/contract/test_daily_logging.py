# Проверки ежедневных журналов, смены даты, владения файлами и интеграции с watcher.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261007-112702
#
# Тесты:
# -> record_at(): Запись logging с явно заданным временем создания.
# -> test_calendar_paths(): Границы календаря, английские месяцы и закрепление пути.
# -> test_invalid_settings(): Ошибки настроек до создания файлов.
# -> test_daily_switch_and_close(): Переключение вперёд/назад, append и окончательное закрытие.
# -> test_switch_failure(): Отказ нового пути не перенаправляет запись в старый файл.
# -> test_concurrent_records(): Потоки не смешивают записи разных дней.
# -> test_watcher_daily_lifecycle(): Владение handler, rollback и синхронный/async lifecycle.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from remote_watch import DailyFileConfig, DailyFileHandler, Identity, RemoteWatcher, WatcherConfig


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запись logging с явно заданным временем создания
#------------------------------------------------------------------------------------------------------------------
def record_at(
    timestamp: float,
    text: str,
) -> logging.LogRecord:

    """Build a real LogRecord with a deterministic creation timestamp.

    :param timestamp: POSIX time selected by the test.
    :type timestamp: float

    :param text: Distinct message used to detect loss and wrong-file routing.
    :type text: str

    :return: Standard record without notification or command metadata.
    :rtype: logging.LogRecord
    """

    # timestamp/text — контролируемые значения без подмены глобальных часов.
    record = logging.LogRecord("daily.test", logging.INFO, __file__, 1, text, (), None)
    record.created = timestamp
    return record
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Границы календаря, английские месяцы и закрепление пути
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("month", range(1, 13))
def test_calendar_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    month: int,
) -> None:

    """Check portable English calendar paths and an explicit timezone across midnight.

    :param tmp_path: Temporary root, separate from application log directories.
    :type tmp_path: Path

    :param monkeypatch: Scoped current-directory replacement.
    :type monkeypatch: pytest.MonkeyPatch

    :param month: Month whose full English name must appear regardless of locale.
    :type month: int
    """

    # Относительный путь закрепляется при создании конфига; смена cwd его не меняет.
    monkeypatch.chdir(tmp_path)
    config = DailyFileConfig(directory="log", prefix="spambot", timezone=timezone(timedelta(hours=3)))
    monkeypatch.chdir(tmp_path.parent)
    months = ("January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December")
    stamp = datetime(2028, month, 1, tzinfo=timezone.utc).timestamp()
    path = config.path_for(stamp)
    expected = tmp_path / "log" / "2028" / f"{month:02d}. {months[month-1]}"
    assert path == expected / f"spambot_log_28{month:02d}01.txt"
    assert not (tmp_path / "log").exists()

    # На UTC ещё декабрь, но в заданном поясе уже январь следующего года.
    stamp = datetime(2027, 12, 31, 22, 30, tzinfo=timezone.utc).timestamp()
    relative = config.path_for(stamp).relative_to(config.directory).as_posix()
    assert relative == "2028/01. January/spambot_log_280101.txt"
    local = DailyFileConfig(directory=tmp_path, prefix="local")
    assert local.path_for(stamp).name == f"local_log_{datetime.fromtimestamp(stamp):%y%m%d}.txt"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибки настроек до создания файлов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("values", [{"directory": ""}, {"directory": 7}, {"prefix": ""}, {"prefix": "../bad"},
    {"prefix": "bad\\name"}, {"prefix": "bad:name"}, {"prefix": "bad\x00name"}, {"prefix": "*"},
    {"prefix": 8}, {"timezone": "UTC"}, {"level": True}, {"level": -1}, {"format": 8}, {"format": "%("}])
def test_invalid_settings(
    tmp_path: Path,
    values: dict[str, object],
) -> None:

    """Reject malformed configuration without touching the filesystem.

    :param tmp_path: Root whose contents must remain empty.
    :type tmp_path: Path

    :param values: One invalid field override.
    :type values: dict[str, object]
    """

    # values — неверные типы, шаблоны и компоненты пути.
    with pytest.raises((TypeError, ValueError)):
        DailyFileConfig(**{"directory": tmp_path, **values})
    assert list(tmp_path.iterdir()) == []
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Переключение вперёд/назад, append и окончательное закрытие
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("boundary", [(2027, 12, 31), (2028, 1, 31), (2028, 2, 28), (2028, 2, 29)])
def test_daily_switch_and_close(
    tmp_path: Path,
    boundary: tuple[int, int, int],
) -> None:

    """Keep dated Unicode messages and timezone formatting correct across calendar boundaries.

    :param tmp_path: Per-test file root.
    :type tmp_path: Path

    :param boundary: Date immediately before midnight, including leap-year boundaries.
    :type boundary: tuple[int, int, int]
    """

    # boundary — переход суток/месяца/года; после новой даты приходит запоздавшая запись.
    zone = timezone(timedelta(hours=3))
    before = datetime(*boundary, 23, 59, 59, tzinfo=zone)
    after = before + timedelta(seconds=2)
    config = DailyFileConfig(directory=tmp_path, prefix="спам", timezone=zone)
    handler = DailyFileHandler(config)
    try:
        handler.handle(record_at(before.timestamp(), "Вчера"))
        previous = handler.stream
        handler.handle(record_at(after.timestamp(), "Сегодня"))
        assert previous.closed and handler.baseFilename == str(config.path_for(after.timestamp()))
        handler.handle(record_at(before.timestamp(), "Поздняя запись"))
        text = config.path_for(before.timestamp()).read_text(encoding="utf-8")
        assert "Вчера" in text and "Поздняя запись" in text and "Сегодня" not in text
        text = config.path_for(after.timestamp()).read_text(encoding="utf-8")
        assert str(after.date()) in text and "Сегодня" in text and "Вчера" not in text
    finally:
        handler.close()

    # Поздний прямой emit после close не создаёт ни каталога, ни нового дескриптора.
    future = datetime(2035, 1, 1, tzinfo=zone).timestamp()
    handler.emit(record_at(future, "После закрытия"))
    assert not config.path_for(future).exists() and handler.stream is None
    with_again = DailyFileHandler(config)
    try:
        with_again.handle(record_at(after.timestamp(), "Повторный запуск"))
    finally:
        with_again.close()
    assert "Сегодня" in config.path_for(after.timestamp()).read_text(encoding="utf-8")
    assert "Повторный запуск" in config.path_for(after.timestamp()).read_text(encoding="utf-8")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ нового пути не перенаправляет запись в старый файл
#------------------------------------------------------------------------------------------------------------------
def test_switch_failure(tmp_path: Path) -> None:

    """Keep the previous stream usable after a new directory cannot be created.

    :param tmp_path: Root containing a synthetic path conflict.
    :type tmp_path: Path
    """

    # Блокируем папку будущего года обычным файлом: воспроизводимо и без chmod.
    config = DailyFileConfig(directory=tmp_path, timezone=timezone.utc, format="%(message)s")
    handler = DailyFileHandler(config)
    failures = Mock()
    handler.handleError = failures
    now = datetime.now(timezone.utc).timestamp()
    future = datetime(2040, 1, 1, tzinfo=timezone.utc).timestamp()
    (tmp_path / "2040").write_text("block", encoding="utf-8")
    try:
        handler.handle(record_at(now, "before"))
        old_stream = handler.stream
        handler.handle(record_at(future, "wrong day"))
        assert failures.call_count == 1 and handler.stream is old_stream and not old_stream.closed
        handler.handle(record_at(now, "after"))
        assert config.path_for(now).read_text(encoding="utf-8").splitlines() == ["before", "after"]
        (tmp_path / "2040").unlink()
        handler.handle(record_at(future, "recovered"))
        assert old_stream.closed
        assert config.path_for(future).read_text(encoding="utf-8").strip() == "recovered"
    finally:
        handler.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Потоки не смешивают записи разных дней
#------------------------------------------------------------------------------------------------------------------
def test_concurrent_records(tmp_path: Path) -> None:

    """Route concurrent adjacent-day records without loss, duplicates or cross-file contamination.

    :param tmp_path: Root containing two synthetic daily logs.
    :type tmp_path: Path
    """

    # Потоки намеренно чередуют даты: нужен общий lock на смену файла и запись.
    config = DailyFileConfig(directory=tmp_path, timezone=timezone.utc, format="%(message)s")
    handler = DailyFileHandler(config)
    days = [datetime(2028, 1, day, tzinfo=timezone.utc).timestamp() for day in (1, 2)]
    records = [record_at(days[index % 2], f"entry-{index}") for index in range(100)]
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(handler.handle, records))
    finally:
        handler.close()
    for offset, stamp in enumerate(days):
        lines = config.path_for(stamp).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 50 and set(lines) == {f"entry-{i}" for i in range(offset, 100, 2)}
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Владение handler, rollback и синхронный/async lifecycle
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["sync", "async", "failure"])
def test_watcher_daily_lifecycle(
    tmp_path: Path,
    mode: str,
) -> None:

    """Create local handlers only at startup and preserve caller-owned logging resources.

    :param tmp_path: Root created lazily by watcher startup.
    :type tmp_path: Path

    :param mode: Sync/async startup or a deterministic filesystem failure.
    :type mode: str
    """

    # mode/tmp_path — проверяем реальную интеграцию без каналов, сети и credentials.
    logger = logging.Logger("application", logging.INFO)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    config = DailyFileConfig(directory=tmp_path / "new", prefix="test", format="%(message)s")
    identity = Identity(service="daily", environment="test", region="local", host="test", instance_id="one")
    watcher = RemoteWatcher(WatcherConfig(identity=identity), logger=logger, file=config)
    assert logger.handlers == [foreign] and not config.directory.exists()
    if mode == "failure":
        config.directory.write_text("block", encoding="utf-8")
        with pytest.raises(OSError):
            watcher.start()
        assert logger.handlers == [foreign] and not foreign._closed
        return

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка обработчика в запущенном watcher
    #--------------------------------------------------------------------------------------------------------------
    def write() -> Path:

        """Return the actual current path after one logger call.

        :return: Current file path owned by the daily handler.
        :rtype: Path
        """

        handler = next(h for h in logger.handlers if isinstance(h, DailyFileHandler))
        logger.debug("filtered")
        logger.info("written")
        return Path(handler.baseFilename)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Асинхронный жизненный цикл
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> Path:

        """Run the same daily file lifecycle through the asynchronous facade.

        :return: File produced by the active watcher.
        :rtype: Path
        """

        async with watcher:
            return write()
    #--------------------------------------------------------------------------------------------------------------

    if mode == "async":
        path = asyncio.run(scenario())
    else:
        with watcher:
            watcher.start()
            path = write()
    assert logger.handlers == [foreign] and not foreign._closed
    assert path.read_text(encoding="utf-8").strip() == "written"
    path.rename(path.with_suffix(".closed"))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_daily_logging не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
