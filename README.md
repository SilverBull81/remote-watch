# Remote Watch

Version 1.2.7

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261002-174542

Python-библиотека для стандартного логирования, уведомлений на телефон
и адресных команд приложениям на удалённых серверах.

**Состояние: базовые 0.1/0.2 завершены; код этапов 0.3.1–0.3.5 реализован.
Telegram smoke с одним и двумя приложениями подтверждён владельцем.
Пакет 0.3.6.dev1 для личного использования.** Обычный logger передаёт выбранные
уведомления через ограниченные очереди в каналы фонового runtime. Реализованы
подготовка текста, маршрутизация, ограниченные повторы с full jitter и retry-after,
TTL, sync/async запуск и остановка, счётчики по получателям и ограниченный atexit.
По умолчанию разрешены три попытки; `max_attempts=1` отключает повторы.
Core собирается без runtime-зависимостей; адаптеры подключают aiohttp через extras.
RemoteWatcher подключает доставку и необязательные console/rotating-file handlers
к обычному logger. Добавлены relay-клиент и серверный outbound gateway с auth/ACL.
В gateway JSON можно задать token прямо в локальном файле либо использовать token_env.
Запуск и границы размещения: [GATEWAY_SERVER.md](docs/GATEWAY_SERVER.md).
Прямая доставка принята по длительному RU/LV-сценарию; relay RU → LV → Telegram
подтверждён пользователем на телефоне. Длительный mixed и автозапуск VM пока
не проверены. [Итоги и остаток](docs/REVIEW_0_1_0_2.md).
Добавлены [модели, wire и правила исполнения команд](docs/COMMAND_PROTOCOL.md).
Добавлены [проверка свежести без второго подтверждения](docs/COMMAND_TIME.md)
и [постоянный журнал SQLite](docs/COMMAND_STORAGE.md). Реализованы отдельные
[hub, HTTPS-клиент и права команд](docs/COMMAND_HUB.md). [Исполнитель пользовательских callbacks](docs/COMMAND_EXECUTION.md)
подключён к RemoteWatcher. Добавлены [Telegram, закрытый ntfy, JSON/CLI и command smoke](docs/COMMAND_SOURCES.md).
Поддержаны проверка состояния и resume/suspend одним сообщением.
[Промежуточный review и оставшиеся шаги](docs/REVIEW_0_1_0_3.md).

## Установка для локальной разработки

Целевой Python — 3.10+. Матрица Windows/Linux × Python 3.10/3.12 × core/extras
прошла все восемь jobs GitHub Actions; результаты — в [VALIDATION.md](docs/VALIDATION.md).
Распространение пока закрыто:
по решению владельца версия 0.1 используется только лично, публичной публикации нет.
Открытая лицензия потребуется при отдельном решении о распространении.

```powershell
python -m pip install -e ".[dev,telegram,ntfy]"
python -m ruff check .
python tools/check_style.py
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

Это регистрация локальных обработчиков. Отдельные авторизация и HTTPS-обмен уже
реализованы в CommandHub/CommandClient. Для исполнения передайте отдельный
command_client в RemoteWatcher. Входящие Telegram/ntfy-команды и запуск
нескольких экземпляров описаны в [COMMAND_SOURCES.md](docs/COMMAND_SOURCES.md).
Примеры регистрации: [COMMANDS.md](docs/COMMANDS.md).

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
30.09.2026 владелец принял базовую прямую доставку после примерно 12 часов
полевого наблюдения RU/LV и проверки Android. Результаты и границы —
в [FIELD_SMOKE.md](docs/FIELD_SMOKE.md); основной relay RU → LV также подтверждён,
длительный mixed-сценарий остаётся отдельной эксплуатационной проверкой.

## Документация

| Документ | Назначение |
| --- | --- |
| [PROJECT_BRIEF.md](PROJECT_BRIEF.md) | Задача, границы релизов и критерии результата |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Потоки данных, модели, гарантии и lifecycle |
| [CHANNELS.md](docs/CHANNELS.md) | ntfy, Matrix, альтернативы и условия выбора |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Типизированные настройки и исходные лимиты |
| [WATCHER.md](docs/WATCHER.md) | RemoteWatcher, локальные журналы и примеры приложений |
| [REVIEW_0_1.md](docs/REVIEW_0_1.md) | Итоговое ревью и оставшиеся внешние проверки |
| [RUNTIME.md](docs/RUNTIME.md) | Работающий путь logging → очереди → тестовые каналы |
| [ADAPTERS.md](docs/ADAPTERS.md) | Telegram/ntfy, токены, ошибки HTTP и реальная отправка |
| [FIELD_SMOKE.md](docs/FIELD_SMOKE.md) | Суточный сценарий на трёх серверах и отчёт |
| [RELAY.md](docs/RELAY.md) | Relay-клиент, wire-контракт и границы текущей итерации |
| [SMOKE.md](docs/SMOKE.md) | Одна пробная отправка выбранному сервису |
| [NTFY_SETUP.md](docs/NTFY_SETUP.md) | Закрытый topic, права отправителя/телефона и токен |
| [COMMAND_HUB.md](docs/COMMAND_HUB.md) | Hub, HTTPS-клиент, права, сроки и границы этапа 0.3.3 |
| [COMMANDS.md](docs/COMMANDS.md) | Пользовательские callbacks, partial и расширенный контракт |
| [GATEWAY.md](docs/GATEWAY.md) | Необязательный relay и отдельный command hub |
| [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Порядок реализации и результаты этапов |
| [VALIDATION.md](docs/VALIDATION.md) | Приёмка, тестовые сценарии и статус проверки |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Tooling, Git и рабочий процесс |
| [CODE_STYLE.md](docs/CODE_STYLE.md) | Оформление будущего кода и документов |
| [ADR](docs/adr/README.md) | Обоснование архитектурных решений |
| [AGENTS.md](AGENTS.md) | Правила работы в репозитории |

В 0.3.6 добавлены контроль фоновых задач команд, готовность доверенного времени
и понятные безопасные ошибки конфигурации. Следующие шаги —
[серверное приложение, deploy и диагностика](docs/GATEWAY_OPERATIONS_PLAN.md).
Реальные командные проверки закрытого ntfy отложены владельцем; исходящие ntfy
уведомления уже проверены. Внедрение в настоящее приложение и эксплуатационные
проверки reboot/восстановления остаются отдельными задачами.

## Структура и проверка времени в 0.3.0.dev3

Код сгруппирован в notifications, commands, gateway, adapters и diagnostics.
Основные импорты из remote_watch и прежние команды запуска сохранены; прямые
импорты перенесённых модулей обновляются по [ARCHITECTURE.md](ARCHITECTURE.md).
Для команд выбран TimeAPI; проверка установленной библиотеки:

```powershell
python -m remote_watch.diagnostics.time_probe
```

Проверка не меняет часы ОС и не отправляет уведомления.
Допущения точности и состояние интеграции: [COMMAND_TIME.md](docs/COMMAND_TIME.md).
