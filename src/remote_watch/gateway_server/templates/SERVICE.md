# Служба gateway server

Version 1.0.5

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261015-001857

## Windows

Нужны отдельный venv и extras service, а для UI — tray. Запускайте .cmd из этой
папки либо указывайте Python venv и абсолютный путь к общему JSON.
Install/remove требуют административного PowerShell обычного оператора.

1. Задайте собственный log_dir=logs/gateway_server. Общая logs и Caddy logs не подходят.
2. Сохраните JSON/SQLite/CA и штатно остановите прежнего foreground владельца.
3. Выполните install-service.cmd --dry-run, затем install-service.cmd.
4. Запустите start-service.cmd и service-status.cmd; дополнительно status/doctor.
5. Проверьте с клиента HTTPS, права, уведомление и status-команду.

Прямой вариант (python — интерпретатор venv):

```console
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --install --dry-run
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --install
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --start
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --status
```

Default name RemoteWatchGateway, виртуальный account NT SERVICE\RemoteWatchGateway,
без пароля, automatic start. Install создаёт остановленную службу и выдаёт scoped
права на venv/configs и свои writable dirs; глобальный Python не перенастраивается.
При другом администраторе явно укажите --operator-sid обычного оператора.
Переменные token_env интерактивного окна служба автоматически не наследует.

Остановка и удаление:

```console
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --stop
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --remove --dry-run
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --remove
```

Также есть stop-service.cmd/remove-service.cmd. Remove сохраняет JSON/SQLite/CA;
повторный install использует прежние пути. Не удаляйте manager.lock и не сбрасывайте
ACL для обхода service_manager_running. Access/I/O имеют отдельные коды, не доказывают
живой manager. При service_install_partial часть регистрации/grants могла сохраниться.

## Tray

application.ui=headless/tray управляет иконкой. Служба всегда headless/Session 0;
отдельный controller пользователя запускается при входе, start и sync-tray.
Если нужно: sync-tray.cmd или service CLI --tray из обычного сеанса оператора.
Повторный controller не создаёт второй manager. При остановке иконка скрывается,
при running возвращается. Меню: About и Stop gateway server; Exit tray нет.
Выход пользователя/RDP disconnect не останавливает службу. Tooltip — SCM state,
не проверка всей сети. При ui=headless controller скрывает иконку и ждёт смены режима.

### Обновление контроллера

После обновления пакета выполните sync-tray.cmd: начиная с 1.0.1 он заменяет
контроллер другой версии, оставляя уже актуальный экземпляр. Для явной замены той
же версии используйте restart-tray.cmd/.ps1 или прямую команду из venv:

```console
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --restart-tray
```

Это только операция UI, без SCM stop/start, изменения автозапуска, gateway и журналов.
Контроллер добровольно закрывает GUI и освобождает свой lock, затем запускает замену.
Новый процесс не создаёт второго manager. restart_requested/launch_requested —
запросы, не доказательство готовности GUI; already_running — версия уже совпала.
Ожидание CLI ограничено восемью секундами: restart_timeout не приводит к убийству
процесса; уже принятый запрос может завершиться позже. Проверьте About или повторите sync-tray.

Для контроллера из 1.0.0 и старше автоматический переход невозможен: старый код
не поддерживает запрос. При restart_required один раз завершите только свой
pythonw-процесс remote_watch.gateway_server.service с --tray и путём этого конфига,
затем повторите sync-tray. Не завершайте service host/manager, Caddy или все python.
Простой RDP disconnect контроллер не обновляет. Работающая служба сохраняется.
При application.ui=headless команда не включает иконку вопреки настройке.

## Crash-проверка: только отдельный тестовый deployment

Подготовьте отдельные папку/venv/SQLite/control/logs/порты и имя RemoteWatchGatewayTest.
Не используйте production bot reader; проще начать с notifications-only deployment.
Во всех service-командах указывайте --name RemoteWatchGatewayTest. Задайте
restart.max_restarts=1, window=300, initial_delay=2, max_delay=2.

В административном PowerShell найдите актуальный host и его детей:

```powershell
$testService = Get-CimInstance Win32_Service -Filter "Name='RemoteWatchGatewayTest'"
if ($testService.State -ne 'Running' -or $testService.ProcessId -eq 0) { throw 'Test service is not running' }
Get-CimInstance Win32_Process | Where-Object {
    $_.ParentProcessId -eq $testService.ProcessId -and
    $_.CommandLine -match 'remote_watch\.gateway_server\.child'
} | Select-Object ProcessId, ParentProcessId, ExecutablePath
```

Сверьте путь тестового venv. Завершите только выбранный свежий child PID через
Stop-Process -Id <test-child-pid> -Force. Ожидаются restarting/restarts=1, новый PID
и ready. Для второго отказа заново найдите PID того же компонента в пределах window:
ожидается restart_limit без вечных повторов. В notifications-only child однозначен.

Для crash host начните новый штатный тестовый запуск, снова получите ProcessId
службы через SCM и завершите только этот свежий PID. Первый SCM restart — примерно
через 60 секунд, второй — 300, затем NONE; qfailure показывает настройки.
Не используйте старый PID, убийство всех python.exe или production имя службы.
Проверьте owner alerts, неизменность данных и отсутствие callback replay.
Смерть host сама не отправит сообщение; UNKNOWN после прерванного callback сохраняется.

## Linux

В unit замените CHANGE_ME User/Group, проверьте venv/config/workdir и права.
Install-systemd.sh вызывается root, не перезаписывает unit, включает autostart,
но не запускает сервер. Затем systemctl start remote-watch-gateway.service.
Для удаления — remove-systemd.sh с остановленным сервером; data/CA сохраняются.
Type=exec/SCM running означает запуск процесса, готовность проверяйте doctor с клиента.
