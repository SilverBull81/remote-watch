# Локальные журналы

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-184253

RemoteWatcher добавляет только явно запрошенные локальные handlers и удаляет
при остановке только свои. Можно оставить уже настроенные handlers приложения.
Если logger отсекает DEBUG, файловый handler уровня DEBUG не восстановит эту запись.

## Консоль

`ConsoleConfig(level=logging.INFO)` задаёт уровень и формат своего console handler.
Не передавайте его, если консоль уже настроена приложением.

## Ротация по размеру

```python
from remote_watch import RotatingFileConfig

file_config = RotatingFileConfig(path="log/application.log", max_bytes=10 * 1024 * 1024,
                               backup_count=3)
```

Передайте `file=file_config` в RemoteWatcher. Это обычная ротация по размеру,
а не распределение по календарю. Несколько процессов не должны писать в один файл.

## Распределение по дням

```python
from datetime import timedelta, timezone
from remote_watch import DailyFileConfig

file_config = DailyFileConfig(directory="log", prefix="my-app",
                            timezone=timezone(timedelta(hours=3)))
```

Пример пути: `log/2026/01. January/my-app_log_260115.txt`.
Месяц всегда содержит двухзначный номер и английское название, независимо от locale.
`record.created` определяет день записи; formatter по умолчанию использует ту же timezone.
Без timezone применяется локальный часовой пояс ОС. При необходимости IANA-зоны
используйте `zoneinfo.ZoneInfo`; наличие базы timezone зависит от окружения Python/ОС.

Следующая запись другого дня переключает файл; отдельного midnight-таймера нет.
Старые файлы открываются на добавление, архивы автоматически не удаляются.
Относительный directory фиксируется при создании настроек и не переезжает после chdir.
У handler один открытый stream; переключение защищено для потоков, но координации
нескольких процессов нет. После close handler не открывается снова.

`DailyFileHandler` можно подключить непосредственно к logging без RemoteWatcher.
Самостоятельный handler необходимо самостоятельно закрывать. Наличие локальных
файлов не влияет на доставку и не является notification outbox.
