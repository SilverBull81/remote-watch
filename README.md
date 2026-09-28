# Remote Watch

Version 1.0.7

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260928-222548

Python-библиотека для стандартного логирования, уведомлений на телефон
и адресных команд приложениям на удалённых серверах.

**Состояние: 0.1 завершена; первая итерация 0.2, версия 0.2.0.dev1 для личного использования.** Обычный logger передаёт выбранные
уведомления через ограниченные очереди в каналы фонового runtime. Реализованы
подготовка текста, маршрутизация, ограниченные повторы с full jitter и retry-after,
TTL, sync/async запуск и остановка, счётчики по получателям и ограниченный atexit.
По умолчанию разрешены три попытки; `max_attempts=1` отключает повторы.
Core собирается без runtime-зависимостей; адаптеры подключают aiohttp через extras.
RemoteWatcher подключает доставку и необязательные console/rotating-file handlers
к обычному logger. Добавлены relay-клиент и wire-контракт; серверный gateway
и исполнение удалённых команд ещё не реализованы. Длительный полевой smoke
проверяет возможности 0.1 на реальных серверах и телефоне.

## Установка для локальной разработки

Целевой Python — 3.10+. Текущая проверка выполнена на Windows с Python 3.12.2;
runtime-проверки на 3.10 и Linux ещё нужны. Распространение пока закрыто:
по решению владельца версия 0.1 используется только лично, публичной публикации нет.
Открытая лицензия потребуется при отдельном решении о распространении.

```powershell
python -m pip install -e ".[dev,telegram,ntfy]"
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
Рекомендуемый интерфейс подключения и sync/async-примеры: [WATCHER.md](docs/WATCHER.md).
Подключение Telegram/ntfy и явный тест реальной отправки: [ADAPTERS.md](docs/ADAPTERS.md).
Проверка одной командой с локальным credentials-файлом: [SMOKE.md](docs/SMOKE.md).
Настройка токена и закрытой темы ntfy: [NTFY_SETUP.md](docs/NTFY_SETUP.md).

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
Получение smoke Telegram и ntfy на Android подтверждено пользователем. Для ntfy
используется бесплатный аккаунт без резервирования темы; закрытый доступ не подтверждён.
Все серверные регионы и доставка при длительном простое Android ещё требуют проверки.

## Документация

| Документ | Назначение |
| --- | --- |
| [PROJECT_BRIEF.md](PROJECT_BRIEF.md) | Задача, границы релизов и критерии результата |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Потоки данных, модели, гарантии и lifecycle |
| [CHANNELS.md](docs/CHANNELS.md) | ntfy, Matrix, альтернативы и условия выбора |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Планируемые настройки и исходные лимиты |
| [WATCHER.md](docs/WATCHER.md) | RemoteWatcher, локальные журналы и примеры приложений |
| [REVIEW_0_1.md](docs/REVIEW_0_1.md) | Итоговое ревью и оставшиеся внешние проверки |
| [RUNTIME.md](docs/RUNTIME.md) | Работающий путь logging → очереди → тестовые каналы |
| [ADAPTERS.md](docs/ADAPTERS.md) | Telegram/ntfy, токены, ошибки HTTP и реальная отправка |
| [FIELD_SMOKE.md](docs/FIELD_SMOKE.md) | Суточный сценарий на трёх серверах и отчёт |
| [RELAY.md](docs/RELAY.md) | Relay-клиент, wire-контракт и границы текущей итерации |
| [SMOKE.md](docs/SMOKE.md) | Одна пробная отправка выбранному сервису |
| [NTFY_SETUP.md](docs/NTFY_SETUP.md) | Закрытый topic, права отправителя/телефона и токен |
| [COMMANDS.md](docs/COMMANDS.md) | Пользовательские callbacks, partial и расширенный контракт |
| [GATEWAY.md](docs/GATEWAY.md) | Необязательный relay и отдельный command hub |
| [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Порядок реализации и результаты этапов |
| [VALIDATION.md](docs/VALIDATION.md) | Приёмка, тестовые сценарии и статус проверки |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Tooling, Git и рабочий процесс |
| [CODE_STYLE.md](docs/CODE_STYLE.md) | Оформление будущего кода и документов |
| [ADR](docs/adr/README.md) | Обоснование архитектурных решений |
| [AGENTS.md](AGENTS.md) | Правила работы в репозитории |

Следующая итерация — серверный gateway с auth/ACL и ограничением нагрузки, затем
завершение 0.2 и минимальный контур команд. Подключение
реального приложения отложено до готовности команд, чтобы менять его интерфейс за один раз.
