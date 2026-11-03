# Gateway server: первый запуск

Version 1.1.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261103-090725

Комплект создан remote-watch **@@VERSION@@**. Он не устанавливает зависимости,
не запускает процессы, не регистрирует службы и не читает environment credentials.

Рабочие настройки: config/gateway_server.local.json, config/notification_gateway.local.json,
config/command_gateway.local.json; для нового managed Caddy — config/Caddyfile.
Образцы серверных и клиентских JSON и Caddyfile находятся в config/examples/.
Примеры сначала копируют в config с рабочим именем и заполняют: их относительные
пути рассчитаны на config, а не на прямой запуск из examples. Скрипты остаются в корне.
Данные, control, logs, logo и CA находятся вне config; JSON ссылаются на них через ../.

## Настройка

1. Используйте установленный venv с gateway,commands и нужными provider extras.
   Для Windows-службы добавьте service, для иконки — tray.
2. В config/gateway_server.local.json проверьте внешний HTTPS endpoint и внутренние
   loopback-порты. Выделите manager log_dir=../logs/gateway_server, не общую logs.
   Внутри появятся год/месяц и непустые ежедневные JSONL; Caddy пишет отдельно.
   application.alerts.grace_period=180 скрывает краткие сбои источников команд
   и времени только в сообщениях владельцу; health и журналы меняются сразу.
   application/alerts — вложенные объекты в этом JSON, не отдельные файлы.
   Служебные получатели задаются в application.alerts.destinations; [] отключает их.
   Плановая остановка посылает туда отдельное сообщение без cooldown/grace_period.
3. Заполните config/notification_gateway.local.json: provider settings, principals/Identity/aliases.
4. Заполните config/command_gateway.local.json: targets/Identity, отдельные principal tokens,
   sources/actor/chat. Общие права задайте в scope_sets.control; scope_set выбирается
   отдельно в principal и actor/chat access. Можно оставить явные списки scopes;
   scopes и scope_set одновременно запрещены. actor_id — ID человека, conversation_id — ID чата.
   Двум instances нужны отдельные Identity/principals/tokens. Updates читает один reader.
5. token_env — имя переменной окружения. Вместо него допустим literal token в
   закрытом локальном JSON; оба ключа одновременно запрещены. Notification и
   command gateway tokens приложения — разные случайные секреты минимум 32 символа.
6. В приложении используйте соответствующие config/examples/*client* примеры. command_client JSON
   читается load_command_client; notification_client — памятка для Python-настройки,
   не самостоятельный JSON loader. Client endpoint — внешний HTTPS, не 8765/8766.
7. Настройте Caddy и доверие root CA. Для существующей Caddy-службы оставьте external;
   config/examples/Caddyfile.example не заменяет рабочий Caddyfile автоматически. Для Caddy нужен
   отдельный logs/caddy и доступ его аккаунта на создание/ротацию файлов и storage.
8. Удалите неиспользуемый компонент из общего JSON. Неиспользуемые destinations
   удаляются также из разрешённых aliases. Публичный ntfy не допускается для команд.

## Переезд старой установки

Deploy отказывается создавать новый рабочий набор, если в корне найден прежний
gateway_server.local.json: code=layout_migration_required. Для просмотра нового
комплекта используйте --update-templates; он появится в templates/версия и не заменит
рабочие файлы. Не запускайте сервер из папки предложений.

Перед переносом остановите прежних владельцев и сохраните конфиги/журналы.
Windows-службу сначала stop/remove со старым путём; завершите только прежний
пользовательский pythonw-процесс с --tray и точным старым путём конфига, как описано
в SERVICE.md. Альтернатива — полный выход из сеанса Windows. Headless только скрывает
иконку, RDP disconnect оставляет контроллер работающим.
Перенесите рабочие JSON в config и сохраните **те же абсолютные пути** к SQLite,
control, логотипу, журналам и CA: относительные значения обычно получают ../.
Не заменяйте рабочие tokens/Identity/owner_id и SQLite публичными шаблонами.
Вручную обновите корневые скрипты/unit из нового комплекта, выполните check-config,
заново install/start Windows-службы с новым путём и sync-tray, затем doctor и оба канала.
Внешний Caddy требует отдельного обновления пути своей службы, если перемещён его
Caddyfile; существующий storage CA должен сохраниться. Явные внешние ссылки deploy не переносит.

## Проверка и запуск

Во всех командах ниже python означает Python **выбранного venv**.

```console
python -m remote_watch.gateway_server --config config/gateway_server.local.json --check-config
python -m remote_watch.gateway_server --config config/gateway_server.local.json --start
```

Во втором окне:

```console
python -m remote_watch.gateway_server --config config/gateway_server.local.json --status
python -m remote_watch.diagnostics.gateway_doctor --mode server --config config/gateway_server.local.json --online
```

Запустите client doctor на клиентской машине с заполненным
config/examples/gateway_doctor.client.example.json, затем тестовое уведомление и status-команду.
Server doctor проверяет внутренние listeners, не внешний Caddy/TLS.

Если PowerShell запрещает .ps1, используйте прямые Python-команды или service .cmd.
Не меняйте общесистемную ExecutionPolicy. Caddy reload работает только при уже
запущенном Caddy; для первого старта нужен run или старт его службы.

Для отдельной ошибки конфига проверьте нужный компонент:

```console
python -m remote_watch.gateway --config config/notification_gateway.local.json --check-config
python -m remote_watch.gateway.commands --config config/command_gateway.local.json --check-config
```

## Работа и обновление

Остановка: stop.ps1/.sh либо Python CLI --stop. Для 24/7 см. SERVICE.md.
При application.ui=tray меню содержит About и Stop Server. Подпись иконки —
Remote Watch Gateway. В службе это
отдельный пользовательский controller, не второй manager.
После обновления пакета выполните sync-tray.cmd. Явный перезапуск только иконки —
restart-tray.cmd; службу он не останавливает. Однократный переход со старого
контроллера 1.0.0 описан в SERVICE.md, раздел «Обновление контроллера».

Существующие JSON/SQLite/CA не заменяются deploy. Сохраняйте прежние пути и owner_id,
не запускайте одновременно старого и нового reader. Повтор deploy добавляет только
отсутствующее, --update-templates создаёт отдельные versioned proposals.
Обновления проверяйте в окне обслуживания; callbacks с UNKNOWN не повторяются.
