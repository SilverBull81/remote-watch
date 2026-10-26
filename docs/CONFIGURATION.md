# Настройки и идентификаторы

Version 1.1.3

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261026-124820

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

## Почему у командного gateway три раздела

`targets`, `principals` и `sources` относятся к `command_gateway.local.json`.
Внешний `gateway_server.local.json` задаёт пути компонентов и параметры приложения-сервера.

| Раздел | Что задаёт | Пример ограничения |
| --- | --- | --- |
| targets | Короткий alias и точную Identity приложения | one и two — разные экземпляры, выбор по единственному живому не допускается |
| principals | Сервисный токен приложения, разрешённые Identity и верхнюю границу его команд | Приложение не может зарегистрироваться от имени чужого target или объявить лишние полномочия |
| sources | Telegram-бота либо ntfy reader и права отправителей actor/chat/target/scopes | Одному пользователю разрешён status, другому — также resume/suspend |

Две проверки прав намеренные: principal ограничивает полномочия приложения,
а `sources.access` — человека в конкретном чате. Регистрация команды проверяет
её `required_scope` по principal; при обращении из мессенджера он проверяется
по ACL отправителя и точному target. Разрешение одной стороны не заменяет другую.
Это позволяет использовать разные права в двух чатах одного приложения или
один чат для нескольких приложений без неявного расширения доступа.
Повторять одинаковые списки необязательно: начиная с 1.1.0 можно определить
именованный набор в `scope_sets` и явно выбрать его в каждом правиле.

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
длиной 32–512 символов из набора `[A-Za-z0-9_-]`, без пробелов; используйте разные
значения для разных командных principals. Это не токены Telegram-ботов.
При ошибке поля token сначала сверяйте секрет с соответствующим клиентом: замена
только на одной стороне приведёт к отказу авторизации.
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
`principals[]` задаёт name, отдельный token/token_env, targets и scopes либо scope_set приложения.
`sources[]` задаёт source_id/provider/settings/access; access содержит actor_id,
conversation_id, targets и scopes либо scope_set оператора. Права двух сторон пересекаются.
`sources[].display_mode` — full/compact/text. `hub` необязателен: его defaults
подходят для первого запуска. Не добавляйте туда произвольные поля приложения.

### Именованные наборы прав

`scope_sets` — необязательный раздел **командного gateway**, доступный с 1.1.0.
Это словарь обычных списков scopes, например:

```json
{
  "scope_sets": {
    "control": ["command:status", "command:suspend_load", "command:resume_load"],
    "observe": ["command:status"]
  }
}
```

В `principals[]` и `sources[].access[]` укажите **ровно одно** поле:

- `"scope_set": "control"` — ссылка на определение из того же файла;
- `"scopes": ["command:status"]` — прежний явный список, по-прежнему поддерживается.

Оба варианта можно смешивать в разных правилах одного конфига. Например, principal
приложения выбирает `control`, а пользователь в чате — `observe`: ему будет доступен
только status. Само наличие `control` в словаре никому не даёт этих прав.

Имена наборов имеют вид `[a-z][a-z0-9_-]{0,63}`. Допускается до 64 наборов;
в списке до 128 элементов и до 64 различных непустых scopes, каждый не длиннее
128 байт UTF-8. Повторы scopes сворачиваются в множество. Пустой словарь допустим,
пустой набор — нет. Проверяются все определения, включая пока не используемые.

Неизвестная ссылка, неверный тип, одновременные `scopes`/`scope_set` даже с null
или отсутствие обоих полей приводят к отказу проверки. Нет наследования, вложенных
ссылок, объединения наборов, шаблонов `*` или автоматического выбора прав по principal.
Набор разворачивается при загрузке; его имя не попадает в командный wire или SQLite.
Имена наборов и неизвестные значения не печатаются в ошибках: определения обозначаются
позициями `scope_sets[0]`, ссылки — полями вроде `principals[0].scope_set`.

`schema_version` остаётся 1. Старый конфиг с `scopes` менять не обязательно; новый
с `scope_sets` требует gateway 1.1.0+. В клиентском JSON, `CommandSpec.required_scope`,
doctor client JSON и notification gateway формат не меняется: ссылки там не поддерживаются.

### Переход существующего конфига

1. Обновите пакет gateway до 1.1.0+; сохраните копию рабочего командного JSON.
2. Перенесите повторяющийся список в `scope_sets.control` на верхнем уровне файла.
3. В выбранных principals и access замените список `scopes` на `scope_set: "control"`.
   Для ограниченного пользователя можно определить отдельный `observe`; не расширяйте его права.
4. Выполните `--check-config` для командного gateway либо всего gateway server.
5. Планово перезапустите gateway. Настройки приложений-клиентов менять не нужно.

Сохраните targets/Identity, credentials, source_id и state_dir. Журналы и курсоры
не удаляются и не сбрасываются. Повторный deploy сохраняет существующие конфиги;
новый шаблон не переписывает рабочие правила автоматически.

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
