# Локальные журналы по дням с автоматическим выбором папки года и месяца.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261007-112702
#
# Константы:
# -> _MONTHS: Английские названия месяцев независимо от системной локали.
#
# Классы:
# -> DailyFileConfig: Настройки ежедневного UTF-8 журнала.
#    Интерфейс:
#    -> path_for(): Путь для времени создания записи без обращения к диску.
#    Специальные методы:
#    -> __post_init__(): Проверка настроек и фиксация корневой папки.
#
# -> _DailyFormatter: Время строки в том же часовом поясе, что и папка журнала.
#    Конструктор:
#    -> __init__(): Подготовка стандартного формата и часового пояса.
#    Интерфейс:
#    -> formatTime(): Форматирование времени записи.
#
# -> DailyFileHandler: Автоматическое переключение ежедневных файлов.
#    Конструктор:
#    -> __init__(): Открытие текущего журнала и настройка logging.
#    Интерфейс:
#    -> emit(): Запись в файл соответствующего дня без повторного открытия после close.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path

from remote_watch._validation import require_int, require_text

#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************
_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Настройки ежедневного UTF-8 журнала
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class DailyFileConfig:
    """Describe per-day append-only files without creating directories or changing a logger."""

    directory: str | Path                           # Корневая папка; закрепляется при создании настроек.
    prefix: str = "application"                     # Начало имени перед _log_YYMMDD.txt.
    timezone: tzinfo | None = None                  # Часовой пояс даты и строки; None — местное время ОС.
    level: int = logging.DEBUG                     # Минимальный уровень для файла.
    format: str = "%(asctime)s %(levelname)s %(name)s: %(message)s"  # Шаблон стандартной строки logging.

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Путь для времени создания записи без обращения к диску
    #--------------------------------------------------------------------------------------------------------------
    def path_for(
        self,
        timestamp: float,
    ) -> Path:

        """Build a deterministic calendar path using the configured timezone.

        :param timestamp: POSIX creation timestamp, normally LogRecord.created.
        :type timestamp: float

        :return: Absolute per-day path; no directories or files are created.
        :rtype: Path
        """

        # timestamp — дата события, а не время обработки отложенной записи.
        day = datetime.fromtimestamp(timestamp, tz=self.timezone)
        month = f"{day.month:02d}. {_MONTHS[day.month - 1]}"
        return self.directory / f"{day.year:04d}" / month / f"{self.prefix}_log_{day:%y%m%d}.txt"
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка настроек и фиксация корневой папки
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate portable filenames and freeze the root without filesystem mutations."""

        if not isinstance(self.directory, (str, Path)) or not str(self.directory).strip():
            raise ValueError("directory must be a nonempty string or Path")
        object.__setattr__(self, "directory", Path(self.directory).absolute())

        # Prefix всегда часть имени, а не путь: запрещаем разделители и символы,
        # недопустимые в Windows. Готовые имена остаются одинаковыми на Linux/Windows.
        require_text(self.prefix, "prefix", 128)
        if any(ord(char) < 32 or char in '<>:"/\\|?*' for char in self.prefix):
            raise ValueError("prefix must be a portable filename component")
        if self.timezone is not None and not isinstance(self.timezone, tzinfo):
            raise TypeError("timezone must be tzinfo or None")

        require_int(self.level, "level", 0)
        if not isinstance(self.format, str):
            raise TypeError("format must be a string")
        logging.Formatter(self.format)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Время строки в том же часовом поясе, что и папка журнала
#------------------------------------------------------------------------------------------------------------------
class _DailyFormatter(logging.Formatter):
    """Keep default line timestamps consistent with the file's calendar date."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: DailyFileConfig,
    ) -> None:

        """Retain the configured format and timezone.

        :param config: Validated local log configuration.
        :type config: DailyFileConfig
        """

        # config — формат строки и пояс без изменения глобального logging.Formatter.
        super().__init__(config.format)
        self._timezone = config.timezone
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Форматирование времени записи
    #--------------------------------------------------------------------------------------------------------------
    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:

        """Format the creation timestamp without depending on Formatter.converter.

        :param record: Original logging record, left unchanged.
        :type record: logging.LogRecord

        :param datefmt: Optional strftime format supplied by logging.
        :type datefmt: str | None

        :return: Timestamp in the configured calendar timezone.
        :rtype: str
        """

        # record/datefmt — стандартный протокол Formatter; пояс применяется локально.
        value = datetime.fromtimestamp(record.created, tz=self._timezone)
        if datefmt is not None:
            return value.strftime(datefmt)
        return f"{value:%Y-%m-%d %H:%M:%S},{value.microsecond // 1000:03d}"
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Автоматическое переключение ежедневных файлов
#------------------------------------------------------------------------------------------------------------------
class DailyFileHandler(logging.FileHandler):
    """Append each record to its calendar day; own one stream and never reopen after close."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: DailyFileConfig,
    ) -> None:

        """Open today's file, creating calendar directories, and apply level and formatter.

        :param config: Validated per-process daily file configuration.
        :type config: DailyFileConfig
        """

        # config — явные настройки; чужие обработчики logger не ищем и не заменяем.
        if not isinstance(config, DailyFileConfig):
            raise TypeError("config must be DailyFileConfig")
        self._config = config
        path = config.path_for(datetime.now().timestamp())
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path, mode="a", encoding="utf-8", errors="backslashreplace")
        self.setLevel(config.level)
        self.setFormatter(_DailyFormatter(config))
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запись в файл дня без повторного открытия после close
    #--------------------------------------------------------------------------------------------------------------
    def emit(
        self,
        record: logging.LogRecord,
    ) -> None:

        """Switch streams atomically under the handler lock before writing a dated record.

        :param record: Standard log record; its creation timestamp selects the destination file.
        :type record: logging.LogRecord
        """

        # record — дата создания важна при отложенной доставке через QueueListener
        # и при переходе через полночь. Запись из прошлого дописывает прошлый файл.
        # RLock общий с handle/close; защищаем также прямой вызов emit.
        self.acquire()
        try:
            if self._closed:
                return
            path = self._config.path_for(record.created)
            if str(path) != self.baseFilename:
                # Сначала открываем новый файл: отказ mkdir/open не портит старый
                # stream и не отправляет сегодняшнюю запись во вчерашний журнал.
                path.parent.mkdir(parents=True, exist_ok=True)
                new_stream = open(path, "a", encoding="utf-8", errors="backslashreplace")
                old_stream = self.stream
                try:
                    if old_stream is not None:
                        old_stream.flush()
                except BaseException:
                    new_stream.close()
                    raise

                self.stream = new_stream
                self.baseFilename = str(path)
                if old_stream is not None:
                    old_stream.close()

            # Стандартная обработка ошибок пишет только в локальную диагностику
            # logging. Никаких logger.error или обращений к мессенджерам отсюда нет.
            super().emit(record)
        except Exception:
            self.handleError(record)
        finally:
            self.release()
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch.local_logging не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
