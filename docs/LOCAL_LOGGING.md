# Ежедневные файлы стандартного логгера

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261007-120324

## Подключение через RemoteWatcher

В 0.4.1.dev11 добавлены `DailyFileConfig` и `DailyFileHandler`. Для приложения,
которое уже использует RemoteWatcher, достаточно заменить настройку `file`:

```python
from remote_watch import DailyFileConfig, RemoteWatcher

# watcher_config — существующие настройки identity, destinations и commands.
watcher = RemoteWatcher(
    watcher_config,
    logger=app_logger,
    file=DailyFileConfig(directory="log", prefix="spambot"),
)
```

Остальные параметры создания watcher, включая `command_client`, сохраняются
в конфигурации приложения. `watcher.logger` остаётся обычным Logger/LoggerAdapter.
Вызовы info/warning/error менять не нужно. Уровень самого logger по-прежнему
задаёт приложение; `DailyFileConfig.level` — отдельный фильтр файлового handler.

При запуске 7 октября 2026 года получается:

```text
log/
└── 2026/
    └── 10. October/
        ├── spambot_log_261007.txt
        └── spambot_log_261008.txt
```

Следующая запись нового дня автоматически переключает файл. Для января папка
называется `01. January`. Английские названия месяцев заданы явно: язык Windows
и `locale` Python на имена папок не влияют. Отдельного вызова `update_logger_folder`
или таймера в приложении не требуется.

## Использование без RemoteWatcher

Handler можно подключить к любому стандартному logger. Полный пример без сети:

```python
import logging

from remote_watch import DailyFileConfig, DailyFileHandler

logger = logging.getLogger("my_application")
logger.setLevel(logging.INFO)

handler = DailyFileHandler(DailyFileConfig(directory="log", prefix="my_application"))
logger.addHandler(handler)
try:
    logger.info("Приложение запущено")
finally:
    logger.removeHandler(handler)
    handler.close()
```

Настраивать handler следует один раз в точке запуска приложения. Если старый
FileHandler уже подключён, приложение само снимает и закрывает именно его,
чтобы не получить две файловые копии сообщений. Библиотека не ищет и не меняет
первый попавшийся чужой FileHandler. Сеть, Telegram и remote-watch runtime
для самостоятельного DailyFileHandler не нужны.

## Настройки и граница суток

| Поле DailyFileConfig | Значение по умолчанию и смысл |
| --- | --- |
| `directory` | Обязательная корневая папка. Относительный путь закрепляется относительно cwd при создании конфига. |
| `prefix` | `application`; имя файла — `<prefix>_log_YYMMDD.txt`. Префикс не содержит разделителей пути. |
| `timezone` | `None`: местный часовой пояс ОС. Можно передать объект `datetime.tzinfo`. |
| `level` | `logging.DEBUG`; фильтр обработчика, не настройка уровня самого logger. |
| `format` | `%(asctime)s %(levelname)s %(name)s: %(message)s`. |

Для одинаковой границы дня на серверах в разных странах можно явно выбрать
фиксированный UTC+03:00 без дополнительной зависимости от базы часовых поясов:

```python
from datetime import timedelta, timezone

daily_file = DailyFileConfig(
    directory="log",
    prefix="spambot",
    timezone=timezone(timedelta(hours=3)),
)
```

Если нужны сезонные переходы, передайте `zoneinfo.ZoneInfo` с установленными
на вашей системе данными соответствующей зоны. Настройки глобальной локали,
окружения и времени ОС обработчик не меняет.

Путь выбирается по `LogRecord.created`, а не по моменту записи на диск. Это
сохраняет дату события при использовании QueueHandler/QueueListener: поздняя
запись за вчера дописывается во вчерашний файл. При скачке часов назад действует
то же правило. Пояс применяется и к `asctime` штатного formatter этого handler.
Если приложение явно заменяет formatter через `setFormatter`, оно само задаёт
правила отображения времени своей строки. Через `daily_file.path_for(timestamp)`
можно получить ожидаемый путь без создания файла.

## Владение ресурсами и ошибки

- Создание конфига и конструктора RemoteWatcher не создаёт файлов. При `start`
  открывается текущий журнал, поэтому ошибки исходного пути выявляются до
  запуска сетевых клиентов. `astart` использует тот же жизненный цикл.
- Самостоятельный конструктор DailyFileHandler сразу создаёт нужные папки
  и открывает файл текущего дня; без записей этот файл может остаться пустым.
- Кодировка — UTF-8, режим — append, непредставимые символы экранируются через
  `backslashreplace`. Существующие файлы не обнуляются и не переименовываются.
- Смена файла и запись защищены одним lock; предыдущий дескриптор закрывается.
  Один handler поддерживает потоки одного процесса. Для разных процессов
  используйте разные prefix или directory; совместная запись не координируется.
- Если следующий путь недоступен, запись обрабатывается через стандартный
  `logging.Handler.handleError`. Она не попадает в файл чужой даты, не ставится
  в очередь повторов и не порождает удалённого сообщения. При следующем emit
  открытие пробуется снова. Старый stream остаётся пригодным при ошибке mkdir/open.
- Watcher закрывает только собственный handler. После `close` даже запоздавший
  вызов emit не открывает журнал повторно. Чужие обработчики продолжают работать.

Это календарное разбиение, без удаления старых дней и без ограничения размера
дневного файла. Retention/архивация остаются политикой приложения. Если нужны
файлы ограниченного размера с фиксированным числом копий, используйте прежний
`RotatingFileConfig`. Локальная запись синхронна, как у обычного FileHandler;
гарантии и очереди удалённой доставки от неё не меняются.
