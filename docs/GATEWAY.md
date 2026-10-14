# Развёртывание gateway

Version 1.0.8

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-184253

## Когда он нужен

Gateway централизует доступ к провайдерам и авторизацию нескольких приложений.
Один сервер обслуживает много Identity, чатов и ntfy topics. Четыре приложения
не требуют четырёх серверов: добавьте отдельные principals/targets/aliases.
Уведомления и команды имеют независимые токены, права, процессы и журналы.
При прямой отправке уведомлений сервер не нужен; для приёма встроенных команд нужен
command gateway. Термина relay в текущем API больше нет.

```text
Application -- notifications HTTPS --> notification gateway --> Telegram / ntfy
Application <-- commands HTTPS ------> command gateway <-----> messenger
                          Caddy: общий внешний HTTPS endpoint
```

Manager `gateway_server` запускает собственные дочерние процессы, принимает их
heartbeat, делает ограниченные восстановления и общую остановку. Внешний Caddy
при external остаётся отдельным процессом/службой. Не запускайте параллельно
отдельный reader или второй manager над теми же SQLite/bot token.

## Создание комплекта

Установите в venv `remote-watch[gateway,commands,telegram,ntfy]` из проверенного
репозитория/артефакта. Для Windows-службы добавьте `service`, для UI — `tray`.
Укажите **Python этого venv**, который должен запускать сервер:

```console
python -m remote_watch.gateway_server.deploy --target ./gateway --endpoint https://gateway.example.test:8443 --python /absolute/path/to/venv/bin/python --dry-run
```

На Windows `--python` выглядит как `C:\Apps\RemoteWatch\.venv\Scripts\python.exe`.
Находясь в нужном venv, можно не задавать --python: используется текущий интерпретатор.
Проверьте план и повторите без --dry-run. Выберите `--components notifications`,
`commands` или `both` (default). Deploy не устанавливает пакеты/службы, не открывает
SQLite, не разрешает environment credentials и не запускает процессы.

В комплекте: общий JSON, конфиги компонентов, клиентские примеры, стандартный ICO
в logo/, Caddyfile.example, start/status/stop/check/doctor launchers, Windows service
launchers и инструкции, Linux unit/scripts. В новые JSON прописан путь к ICO.

## Подключение существующей установки

```console
python -m remote_watch.gateway_server.deploy --target ./gateway --endpoint https://gateway.example.test:8443 --notifications-config /absolute/path/notification_gateway.local.json --commands-config /absolute/path/command_gateway.local.json --dry-run
```

Старые JSON и их относительные state/SQLite пути сохраняются. Существующие файлы
не перезаписываются, CA не создаётся заново. `--update-templates` записывает
**отдельные** версионные предложения и безопасный отчёт различий; не заменяет
рабочие настройки. Перед миграцией планово остановите прежних владельцев и сохраните
журналы. Резервную копию SQLite делайте при остановленных владельцах/по согласованной
процедуре SQLite, не случайным копированием одного файла при активном WAL.

## Настройка и первый запуск

1. Заполните provider settings и principals notification gateway.
2. Заполните command targets, principals, source actor/chat/scopes, если нужны команды.
3. Согласуйте Identity, aliases и отдельные gateway tokens с приложениями.
4. Проверьте общий JSON и отдельные компоненты через --check-config.
5. Настройте [HTTPS](TLS.md), сохраняя явное CA storage и отдельную папку логов Caddy.
6. Запустите manager, проверьте status и server/client doctor, затем реальную
   тестовую отправку и безопасную команду status.

```console
python -m remote_watch.gateway_server --config gateway_server.local.json --start
python -m remote_watch.gateway_server --config gateway_server.local.json --status
python -m remote_watch.gateway_server --config gateway_server.local.json --stop
```

--start работает в foreground до остановки. Для 24/7 используйте [службу](SERVICES.md).
Windows `.ps1` может блокироваться ExecutionPolicy; прямой venv Python CLI и `.cmd`
не требуют изменения общесистемной политики. Не надо отключать её ради первого старта.

При отдельном запуске без manager существуют `python -m remote_watch.gateway` и
`python -m remote_watch.gateway.commands`. Параметры `--host 127.0.0.1 --port 8765`
или 8766 и `--allow-loopback-http` разрешают HTTP только для локального reverse proxy.
Это низкоуровневый режим с самостоятельным надзором, не дополнительный процесс
рядом с уже управляющим тем же компонентом manager.

## External и managed Caddy

External (default) подходит для уже работающей Caddy-службы. Manager её не запускает,
не перезапускает, не останавливает и не проверяет публичный ingress. Не заменяйте
рабочий Caddyfile сгенерированным примером без осмотра маршрутов и storage.

Managed требует явных executable/config/data_dir и соответствующих deploy-параметров.
Manager проверяет filesystem storage, запускает собственный snapshot config и
наблюдает локальный admin API. Это не проверка доступности с клиентской машины.
У одного Caddy-процесса один владелец; нельзя одновременно управлять им через SCM
и через managed child. Смена аккаунта/пути не должна создавать новый root CA.

## Оповещения владельцу

Настройте отдельные `application.alerts.destinations` в общем JSON. Получатели
приложений автоматически владельцами сервера не становятся:

```json
{
  "ui": "tray",
  "icon_path": "logo/remote_watch.ico",
  "alerts": {
    "cooldown": 60,
    "destinations": [{"alias": "operator", "provider": "telegram",
      "settings": {"token_env": "GATEWAY_OWNER_BOT_TOKEN", "chat_id": 123456789, "display_mode": "text"}}]
  }
}
```

Это содержимое `application`, не весь server JSON. Допустим ntfy. Каналы прямые,
не обращаются к собственному gateway; максимум восемь назначений, по 16 ожидающих
сообщений, одна попытка отправки. Cooldown объединяет близкие переходы одного компонента.

Сообщения на английском идут при замеченных manager failed/degraded/restarting
и последующем ready. Обычные start/stop и первый ready без сбоя молчат.
External Caddy, недоступность всей VM, смерть самого manager и ошибки бизнес-логики
приложения автоматически этим механизмом не покрываются. Для них нужен внешний
наблюдатель. `alerts=enabled` не подтверждает доставку; смотрите `alert_delivery`.

## Что сохранять

Сохраняйте общий/связанные JSON, постоянные client/hub/source SQLite, owner_id,
Identity, bot-reader cursors, CA storage и доверенный root на клиентах.
Обычное обновление не требует их удаления. Manager закрывает только собственные
процессы; PID из старого status не используется для убийства чужого процесса.
UNKNOWN не исчезает от restart и не разрешает повтор callback.
