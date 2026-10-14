# Настройки и идентификаторы

Version 1.1.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-184253

## Какие файлы нужны

| Файл | Кто читает | Для чего |
| --- | --- | --- |
| Настройки приложения в Python | Приложение | WatcherConfig, provider factories, logging |
| `command_client.local.json` | `load_command_client` | HTTPS/Identity/token/SQLite командного клиента |
| `notification_gateway.local.json` | Notification gateway | Получатели и сервисные principals |
| `command_gateway.local.json` | Command gateway | Targets, principals/scopes, sources/actor/chat |
| `gateway_server.local.json` | Manager/deploy/service | Пути компонентов, управление процессами, UI, оповещения владельцу |
| `gateway_doctor.client.local.json` | Client doctor | Проверка внешнего маршрута и собственных прав приложения |

Публичные [примеры](examples/) — шаблоны, не рабочие credentials.
Общего JSON-загрузчика WatcherConfig нет. `notification_client.example.json` из
deploy — памятка для создания `NotificationGatewayClientConfig` в приложении,
а не отдельная команда загрузки. Command client JSON имеет настоящий загрузчик.

## Identity, alias, token и source

Полная Identity — пять полей `service/environment/region/host/instance_id`.
Они должны совпадать в приложении, notification principal и командном target.
Две одновременно работающие копии приложения различаются instance_id.

Alias notification gateway обозначает серверное назначение. Alias команды обозначает
разрешённый target. Это не provider token, не chat ID и не имя локального destination.
`destination_id` выбирается Route внутри одного watcher. `source_id` — стабильное
локальное имя reader/журнала источника; его нельзя менять для обхода старых событий.

Есть три разных вида секретов: provider token (бот/ntfy), notification gateway token
и command gateway token. Для приложения создайте отдельные случайные gateway-секреты
не короче 32 символов; используйте разные значения для разных командных principals.
Provider token приложениям за gateway не нужен. `token_env` содержит **имя** переменной,
а `token` — сам секрет. Они взаимоисключающие; repr/диагностика не должны их раскрывать.
Служба Windows не наследует произвольные переменные интерактивного PowerShell.

## Notification gateway JSON

`schema_version=1`. `destinations[]`: alias/provider/settings; settings относятся
к Telegram или ntfy. `principals[]`: name, token либо token_env, точная identity,
aliases разрешённых назначений. Чужой alias или другая Identity запрещены.
Таймауты провайдера ограничиваются сервером и остатком бюджета клиента.

Пример literal token: [notification_gateway.inline.example.json](examples/notification_gateway.inline.example.json).
Значения в примере синтетические; не используйте общедоступный placeholder как пароль.

## Command gateway JSON

`state_dir` — постоянная отдельная папка SQLite. `targets` связывает alias с Identity.
`principals[]` задаёт name, отдельный token/token_env, targets и scopes приложения.
`sources[]` задаёт source_id/provider/settings/access; access содержит actor_id,
conversation_id, targets и scopes оператора. Права двух сторон пересекаются.
`sources[].display_mode` — full/compact/text. `hub` необязателен: его defaults
подходят для первого запуска. Не добавляйте туда произвольные поля приложения.

`command_client.local.json`: schema_version, identity, endpoint, state_file,
owner_id; дополнительно token/token_env, ca_file, display_mode.
endpoint — **внешний HTTPS origin**, например `https://gateway.example.test:8443`,
без `/v1/commands`, query и credentials. Внутренний 8766 обычно не предоставляет HTTPS.
`display_mode=null` наследует source default. `owner_id` и state_file сохраняются
при обычном обновлении; не генерируйте новый owner для каждого запуска.

## Gateway server JSON

```json
{
  "schema_version": 1,
  "endpoint": "https://gateway.example.test:8443",
  "control_dir": "control",
  "log_dir": "logs/gateway_server",
  "notifications": {"config": "notification_gateway.local.json", "host": "127.0.0.1", "port": 8765},
  "commands": {"config": "command_gateway.local.json", "host": "127.0.0.1", "port": 8766},
  "caddy": {"mode": "external"},
  "application": {"ui": "headless", "icon_path": "logo/remote_watch.ico"}
}
```

Удалите notifications или commands, если компонент не нужен. Хотя бы один нужен.
Относительные пути считаются от **файла, в котором записаны**, не от cwd. Ссылка
manager на старый JSON не меняет базу относительных state_dir внутри старого JSON.

control_dir, log_dir и command state_dir должны быть раздельными. Manager назначает
своим control/log каталогам приватные права: не используйте общую папку Caddy или
приложения. Новые шаблоны задают `logs/gateway_server`; для Caddy выберите `logs/caddy`.
При чужих файлах обычный запуск завершается `log_directory_shared` до изменения ACL.

Необязательные `lifecycle`: startup_timeout=60, shutdown_timeout=30, kill_timeout=5,
heartbeat_interval=1, heartbeat_timeout=15. `restart`: max_restarts=3, window=300,
initial_delay=2, max_delay=30. Все времена — секунды. Повторы конечны; ошибки
конфигурации/прав/владения не исправляются бесконечными перезапусками.

`application`: ui=headless/tray, icon_path (относительно общего JSON),
log_max_bytes=1048576, log_backups=5; alerts содержит cooldown и destinations.
Служебные сообщения владельцу: [GATEWAY.md](GATEWAY.md).

## Проверка

```console
python -m remote_watch.gateway_server --config gateway_server.local.json --check-config
python -m remote_watch.gateway --config notification_gateway.local.json --check-config
python -m remote_watch.gateway.commands --config command_gateway.local.json --check-config
```

Это проверка структуры и ссылок без запуска, SQLite recovery или отправок провайдеру.
Она не доказывает валидность provider token, доступность HTTPS и готовность приложения.
Неизвестные/повторные ключи и неверные типы JSON отвергаются. Ошибка содержит
фиксированный code/field и безопасную подсказку, а не введённые секреты.
