# Gateway server: первый запуск

Version 1.0.6

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-212332

Комплект создан remote-watch **@@VERSION@@**. Он не устанавливает зависимости,
не запускает процессы, не регистрирует службы и не читает environment credentials.

## Настройка

1. Используйте установленный venv с gateway,commands и нужными provider extras.
   Для Windows-службы добавьте service, для иконки — tray.
2. В gateway_server.local.json проверьте внешний HTTPS endpoint и внутренние
   loopback-порты. Выделите manager log_dir=logs/gateway_server, не общую logs.
3. Заполните notification_gateway.local.json: provider settings, principals/Identity/aliases.
4. Заполните command_gateway.local.json: targets/Identity, отдельные principal tokens,
   sources/actor/chat/scopes. actor_id — ID человека, conversation_id — ID чата.
   Двум instances нужны отдельные Identity/principals/tokens. Updates читает один reader.
5. token_env — имя переменной окружения. Вместо него допустим literal token в
   закрытом локальном JSON; оба ключа одновременно запрещены. Notification и
   command gateway tokens приложения — разные случайные секреты минимум 32 символа.
6. В приложении используйте соответствующие clients/* примеры. command_client JSON
   читается load_command_client; notification_client — памятка для Python-настройки,
   не самостоятельный JSON loader. Client endpoint — внешний HTTPS, не 8765/8766.
7. Настройте Caddy и доверие root CA. Для существующей Caddy-службы оставьте external;
   Caddyfile.example не заменяет рабочий Caddyfile автоматически. Для Caddy нужен
   отдельный logs/caddy и доступ его аккаунта на создание/ротацию файлов и storage.
8. Удалите неиспользуемый компонент из общего JSON. Неиспользуемые destinations
   удаляются также из разрешённых aliases. Публичный ntfy не допускается для команд.

## Проверка и запуск

Во всех командах ниже python означает Python **выбранного venv**.

```console
python -m remote_watch.gateway_server --config gateway_server.local.json --check-config
python -m remote_watch.gateway_server --config gateway_server.local.json --start
```

Во втором окне:

```console
python -m remote_watch.gateway_server --config gateway_server.local.json --status
python -m remote_watch.diagnostics.gateway_doctor --mode server --config gateway_server.local.json --online
```

Запустите client doctor на клиентской машине с заполненным
clients/gateway_doctor.client.example.json, затем тестовое уведомление и status-команду.
Server doctor проверяет внутренние listeners, не внешний Caddy/TLS.

Если PowerShell запрещает .ps1, используйте прямые Python-команды или service .cmd.
Не меняйте общесистемную ExecutionPolicy. Caddy reload работает только при уже
запущенном Caddy; для первого старта нужен run или старт его службы.

Для отдельной ошибки конфига проверьте нужный компонент:

```console
python -m remote_watch.gateway --config notification_gateway.local.json --check-config
python -m remote_watch.gateway.commands --config command_gateway.local.json --check-config
```

## Работа и обновление

Остановка: stop.ps1/.sh либо Python CLI --stop. Для 24/7 см. SERVICE.md.
При application.ui=tray меню содержит About и Stop gateway server. В службе это
отдельный пользовательский controller, не второй manager.
После обновления пакета выполните sync-tray.cmd. Явный перезапуск только иконки —
restart-tray.cmd; службу он не останавливает. Однократный переход со старого
контроллера 1.0.0 описан в SERVICE.md, раздел «Обновление контроллера».

Существующие JSON/SQLite/CA не заменяются deploy. Сохраняйте прежние пути и owner_id,
не запускайте одновременно старого и нового reader. Повтор deploy добавляет только
отсутствующее, --update-templates создаёт отдельные versioned proposals.
Обновления проверяйте в окне обслуживания; callbacks с UNKNOWN не повторяются.
