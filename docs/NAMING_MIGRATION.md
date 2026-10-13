# Обновление имён в 0.4.2.dev5

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261013-170523

## Правило именования

В общих папках gateway/adapters имя модуля явно обозначает канал. Внутри
notifications/commands дополнительный префикс не нужен. Публичные классы
сохраняют смысловые имена, понятные после импорта в приложение.

Это намеренная смена Python API предварительной версии. Старых модулей,
псевдонимов классов и альтернативных ключей JSON нет. Работающий процесс
не обновляется автоматически; изменения приложения готовятся до перезапуска.

## Импорты и классы

| Прежний модуль относительно remote_watch | Новый модуль |
| --- | --- |
| notification_gateway_protocol | notifications.gateway_protocol |
| gateway.server | gateway.notification_server |
| gateway.config | gateway.notification_config |
| gateway.json_config | gateway.notification_json_config |
| adapters.telegram | adapters.telegram_notifications |
| adapters.ntfy | adapters.ntfy_notifications |

| Прежнее имя | Новое имя |
| --- | --- |
| adapters.notification_gateway.NotificationGatewayConfig | adapters.notification_gateway.NotificationGatewayClientConfig |
| gateway.Gateway | gateway.NotificationGateway |
| gateway.GatewayConfig | gateway.NotificationGatewayServerConfig |
| gateway.GatewayPrincipal | gateway.NotificationGatewayPrincipal |
| gateway.GatewayStartupError | gateway.NotificationGatewayStartupError |
| GatewayConfigError в JSON-загрузчике уведомлений | NotificationGatewayConfigError |
| load_gateway_config | load_notification_gateway_config |

NotificationGatewayRequest и NotificationGatewayChannel сохраняют имена.
TelegramConfig/TelegramChannel и NtfyConfig/NtfyChannel меняют только путь импорта.
Identity, RemoteWatcher, WatcherConfig и методы приложения остаются прежними.
Точки запуска `python -m remote_watch.gateway`, `remote_watch.gateway.commands`
и `remote_watch.gateway_server` сохраняются.

## Конфиги

Для новой установки рекомендуются gateway_server.local.json,
notification_gateway.local.json и command_gateway.local.json. Ссылки на файлы
задаются в разделах notifications/commands общего конфига сервера.
Произвольное имя уже существующего файла по-прежнему допустимо: оно не является
частью схемы. Не переносите файлы в другую папку без проверки относительных
путей CA, SQLite и каталогов состояния.

Шаблоны notification_gateway.example.json и notification_gateway.inline.example.json
заменяют прежние gateway_config.example.json и gateway_config.inline.example.json.
Имена command_gateway.telegram.example.json, command_gateway.ntfy.example.json,
command_client.example.json и gateway_server.example.json сохраняются.

В JSON командного клиента замените ключ command_display_mode на display_mode.
На командном gateway такая же замена нужна в каждом элементе sources, если
там задан этот параметр. У клиента отсутствие поля/null означает наследование
режима источника; у источника отсутствие поля означает full, null не допускается.

Python API CommandRegistration использует прежний аргумент command_display_mode.
Поле сетевых сообщений, схема 1/2, контрольные суммы и записи SQLite не меняются.
Загрузчик переводит новое имя настройки в прежнее поле регистрации. Уведомления
и ответы команд продолжают иметь независимые режимы отображения.

## Отдельное обновление SpamBot

В коде SpamBot сейчас используется remote_watch.commands.command_display_mode.
При его следующем обновлении загрузчик и модель конфигурации можно перевести на
remote_watch.commands.display_mode, симметрично remote_watch.notifications.display_mode.
Вызов CommandRegistration по-прежнему передаёт command_display_mode, например
`CommandRegistration(..., command_display_mode=commands.display_mode)`.

Одновременно нужны новые пути импортов и NotificationGatewayClientConfig.
Эти изменения выполняются отдельным коммитом в SpamBot: текущая задача меняет
только remote-watch. Простое обновление пакета без адаптации приложения недостаточно.

## Состояние и проверка обновления

Сохраните действующие SQLite-журналы, owner/source IDs, учётные данные и Caddy CA.
Пересоздание журналов и сертификатов для переименования не требуется. Проверьте
конфиги обоих компонентов через --check-config, затем запускайте обновлённые
процессы согласно принятому порядку эксплуатации. Для полевой проверки нужны
доставка уведомления, команда проверки и восстановление клиента после рестарта
командного gateway. Локальные тесты не заменяют эту проверку на VM.

Server deploy остаётся следующим этапом 0.4.3; в этом изменении он не реализован.
