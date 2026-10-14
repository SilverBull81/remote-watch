# Руководство пользователя

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-202944

Начните с клиента. Если нужна только отправка уведомлений, серверную часть можно пропустить.

| Задача | Инструкция |
| --- | --- |
| Установка, logging, sync/async, жизненный цикл | [CLIENT.md](CLIENT.md) |
| Telegram, ntfy, прямая доставка и gateway | [NOTIFICATIONS.md](NOTIFICATIONS.md) |
| callbacks, права, команды в чате и reconnect | [COMMANDS.md](COMMANDS.md) |
| Файлы по дням и ротация | [LOCAL_LOGGING.md](LOCAL_LOGGING.md) |
| Форматы JSON и соответствие идентификаторов | [CONFIGURATION.md](CONFIGURATION.md) |
| Deploy, запуск и управление gateway | [GATEWAY.md](GATEWAY.md) |
| HTTPS, Caddy, доверие CA | [TLS.md](TLS.md) |
| Windows-служба, tray и systemd | [SERVICES.md](SERVICES.md) |
| Health, doctor, журналы и устранение проблем | [OPERATIONS.md](OPERATIONS.md) |

## Примеры настроек

В [examples/](examples/) лежат синтетические значения. Замените адреса, Identity,
actor/chat IDs и секреты перед запуском. Имена `token_env` — ссылки на окружение,
а не значения токенов. Реальные настройки храните в `*.local.json` вне Git.

- [Notification gateway](examples/notification_gateway.example.json),
  [вариант с literal token](examples/notification_gateway.inline.example.json).
- [Telegram command gateway](examples/command_gateway.telegram.example.json),
  [закрытый ntfy command gateway](examples/command_gateway.ntfy.example.json).
- [Command client](examples/command_client.example.json).
- [Gateway server](examples/gateway_server.example.json).
- [Client doctor](examples/gateway_doctor.client.example.json).
- [Caddyfile](examples/Caddyfile.example), [nginx](examples/gateway-nginx.conf).

На сервере проще получить согласованный набор файлов через deploy. Он также создаёт
клиентские шаблоны, иконку, launchers, `START_HERE.md` и `SERVICE.md`.

Технические контракты, проверки и правила сопровождения находятся в
[development/](development/README.md). Нереализованные возможности перечислены
только в [BACKLOG.md](BACKLOG.md).
