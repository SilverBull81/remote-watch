# Remote Watch

Version 1.0.3

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260928-142747

Python-библиотека для стандартного логирования, уведомлений на телефон
и адресных команд приложениям на удалённых серверах.

**Состояние: фоновая доставка с повторами без сети, 0.1.0.dev2.** Обычный logger передаёт выбранные
уведомления через ограниченные очереди в каналы фонового runtime. Реализованы
подготовка текста, маршрутизация, ограниченные повторы с full jitter и retry-after,
TTL, sync/async запуск и остановка, счётчики по получателям и ограниченный atexit.
По умолчанию разрешены три попытки; `max_attempts=1` отключает повторы.
Пакет собирается без runtime-зависимостей. Готовые адаптеры Telegram/ntfy,
RemoteWatcher как удобный фасад и gateway пока не реализованы.

## Установка для локальной разработки

Целевой Python — 3.10+. Текущая проверка выполнена на Windows с Python 3.12.2;
runtime-проверки на 3.10 и Linux ещё нужны. Распространение пока закрыто:
лицензия не выбрана, публичная публикация не выполняется.

```powershell
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest -q
python -m build
```

Минимальные объекты: Identity, Notification, Delivery, DeliveryResult,
NotificationChannel, Destination, Route, RetryPolicy, RuntimeConfig, WatcherConfig.
Их создание валидирует данные, но не запускает сеть, фоновые потоки или callbacks.
Настройки и правила фабрик каналов: [CONFIGURATION.md](docs/CONFIGURATION.md).
Исполняемый пример с тестовым каналом, подключение к существующему logger и
описание ограничений: [RUNTIME.md](docs/RUNTIME.md).

## Пользовательские команды

WatcherConfig принимает `commands={"имя": callback}`; поддерживаются функции,
bound methods, `functools.partial`, callable objects и async-функции.
Для контекста запроса, аргументов, описания и политики используется CommandSpec.
Нет встроенного фиксированного списка команд и нет автоматически выданных прав.

Это регистрация локальных обработчиков. Их приём из чата, авторизация и выполнение
появятся в отдельном контуре. Примеры: [COMMANDS.md](docs/COMMANDS.md).

## Что проектируем

- Обычный `logging.Logger`: консоль, файл и выбранные удалённые назначения.
- Исходящие Telegram и ntfy в первой версии; Matrix — следующий чат для команд.
- Независимую фоновую доставку с ограниченными очередями и явными потерями.
- Пользовательские адаптеры без зависимости core от провайдеров.
- Дополнительный режим `direct`/`relay` для каждого remote-назначения.
- Отдельный command hub для общего чата и нескольких instances.

Совместимость с `fin-data.TelegramBot` не требуется. MAX в текущий план не входит.
ntfy предварительно выбран для первого альтернативного канала; сетевую доступность
и получение на Android-телефоне предстоит проверить в целевом окружении.

## Документация

| Документ | Назначение |
| --- | --- |
| [PROJECT_BRIEF.md](PROJECT_BRIEF.md) | Задача, границы релизов и критерии результата |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Потоки данных, модели, гарантии и lifecycle |
| [CHANNELS.md](docs/CHANNELS.md) | ntfy, Matrix, альтернативы и условия выбора |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Планируемые настройки и исходные лимиты |
| [RUNTIME.md](docs/RUNTIME.md) | Работающий путь logging → очереди → тестовые каналы |
| [COMMANDS.md](docs/COMMANDS.md) | Пользовательские callbacks, partial и расширенный контракт |
| [GATEWAY.md](docs/GATEWAY.md) | Необязательный relay и отдельный command hub |
| [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Порядок реализации и результаты этапов |
| [VALIDATION.md](docs/VALIDATION.md) | Приёмка, тестовые сценарии и статус проверки |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Tooling, Git и рабочий процесс |
| [CODE_STYLE.md](docs/CODE_STYLE.md) | Оформление будущего кода и документов |
| [ADR](docs/adr/README.md) | Обоснование архитектурных решений |
| [AGENTS.md](AGENTS.md) | Правила работы в репозитории |

Следующий шаг — исходящие адаптеры Telegram/ntfy и их offline contract tests.
Реальная отправка с целевых серверов и проверка на телефоне ещё впереди.
