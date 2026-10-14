# Служба Remote Watch Gateway

Version 1.0.2

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-164418

## Windows: установка без изменения Caddy и журналов

Все команды выполняются на gateway VM из папки этого комплекта. Нужен установленный
venv, не исходный checkout. Установите remote-watch с extras
`gateway,commands,telegram,ntfy,service` и дополнительно `tray`, если нужна иконка.
В C:\Work\RemoteWatch используйте именно .venv\Scripts\python.exe.
Проверка конфига и dry-run не подтверждают права будущего аккаунта или доставку.

1. В gateway_server.local.json оставьте реальные прежние ссылки на конфиги,
   control_dir и state_dir. Для существующей службы Caddy — mode=external.
   **log_dir задайте logs/gateway_server**, отдельную папку только операционных
   логов manager. Общая logs уже используется Caddy: менять её DACL нельзя.
   Установщик откажет с service_shared_logs, если выбрана общая папка или в
   log_dir лежат посторонние файлы. Старые логи не переносите и не удаляйте.
   В application задайте `"ui": "tray"` либо `"ui": "headless"`.
   Иконка — `"icon_path": "logo/remote_watch.ico"`.
2. Проверьте --check-config; штатно остановите foreground gateway_server.
   Отдельные старые notification/command процессы тоже не должны владеть listeners/БД.
   Caddy не останавливайте. Сделайте резервную копию остановленных журналов.
3. Откройте PowerShell **от имени администратора своего обычного пользователя**.
   Для dry-run запустите `cmd /c install-service.cmd --dry-run`.
   Для установки — `cmd /c install-service.cmd`.
   PowerShell ExecutionPolicy не мешает .cmd и прямому Python CLI; менять её не нужно.
4. Установка создаёт **остановленную** службу RemoteWatchGateway с автозапуском.
   Аккаунт — NT SERVICE\RemoteWatchGateway, без пароля. Ему выдаются RX на venv
   и чтение явных конфигов, запись на control/logs/command state/managed CA.
   Глобальный Python/base DLL должен уже разрешать чтение этому аккаунту;
   утилита не меняет ACL всего C:\Python или корпоративные политики.
   Переменные token_env должны быть доступны службе, а не только вашему PowerShell.
5. Запустите `cmd /c start-service.cmd`, затем `cmd /c service-status.cmd`.
   RUNNING — запущен manager; полную готовность смотрите через обычные status/doctor.
   Проверьте doctor --mode server --online, реальное уведомление и команду.
6. При ui=tray start-service.cmd запросит контроллер в текущем сеансе оператора.
   Он также автоматически запускается при входе этого пользователя. Если нужен
   отдельный запуск: .venv\Scripts\pythonw.exe -I -B -m
   remote_watch.gateway_server.service --config gateway_server.local.json --tray
   (одной строкой). При другом администраторе запустите GUI в сеансе оператора.

Контроллер не запускает второй сервер. В меню только About и Stop gateway server.
После SCM stopped/удаления службы иконка исчезает, после следующего запуска
возвращается обычно за две секунды. Контроллер остаётся в фоне: он нужен для
автоматического возврата иконки и не управляет callback/SQLite. Выход пользователя
его завершает, но не останавливает службу. Повторный запуск не создаёт дубль.

ui=headless скрывает иконку. sync-tray.cmd обновляет Run-запись; при ui=tray также
запрашивает контроллер сейчас, без рестарта службы. tray=launch_requested не обещает
готовность GUI; tray=operator_session_required требует сеанса выбранного оператора.
Ошибка GUI не отменяет принятый SCM start. При запуске через services.msc нужен
уже работающий контроллер или последующий вход оператора.

После обновления библиотечных файлов старый запущенный контроллер не обновится
сам. Завершите только pythonw с аргументом --tray этого venv или выйдите и войдите
в Windows, затем используйте start-service/sync-tray. Не завершайте --run/child.
Icon path читается при запуске контроллера; остальные серверные настройки требуют restart.

### Повторный install и ошибки control

Если после remove версия dev1/dev2 выдаёт service_manager_running при service-status
service_not_found, сначала обновите venv до 0.4.5.dev3 или новее. Dev3 проверяет прежний
manager.lock через чтение и ту же блокировку ОС, без удаления файла или изменения
рабочих журналов. Уже достаточные права service SID повторно не переписываются.
Повторите install-service.cmd --dry-run, install-service.cmd, start-service.cmd,
service-status.cmd и doctor. JSON/SQLite/CA остаются по прежним путям.

stage=ownership различает service_manager_running (занят lock), service_control_access
(нет доступа к файлу) и service_control_io (другая ошибка). При service_install_partial
смотрите stage/winerror. Не обходите ошибки удалением control, SQLite или сбросом ACL.

## Прямой CLI и права оператора

Пример одной команды без выполнения PowerShell-скрипта:

```powershell
C:\Work\RemoteWatch\.venv\Scripts\python.exe -I -B -m remote_watch.gateway_server.service --config C:\Work\RemoteWatch\gateway_server.local.json --install --dry-run
```

Остальные действия: --install, --start, --stop, --status, --remove, --sync-tray,
--tray. --run предназначен только для SCM. По умолчанию выбран SID текущего
пользователя. Если администрирование выполняется под другим аккаунтом, укажите
--operator-sid S-1-5-21-... обычного оператора **во всех командах**. Его профиль
должен быть загружен. SID узнаётся в его обычном PowerShell командой whoami /user.
Пароли/токены в CLI не передавайте. Не выбирайте другое имя, чтобы обойти ownership.

Оператор получает query/start/stop, но не смену binary/account службы. Локальный
control/log доступ ограничен аккаунтом службы, System и чтением оператора.
Рекурсивные ACL через junction/symlink/UNC отклоняются. При частичной ошибке
установки новая служба удаляется, выданные ACL могут остаться: это явно отражается
code=service_install_partial. Не удаляйте SQLite/control вручную ради установки.
Существующая одноимённая служба не перезаписывается.

После crash процесса SCM допускает два restart (60/300 с), затем прекращает их;
счётчик сбрасывается после суток без новых падений, таймер сам не запускает службу.
Временный startup timeout/недоступность сети после полного rollback manager также
используют этот конечный бюджет. Ошибка конфигурации/прав/ownership не запускает цикл.
Компоненты сохраняют отдельные прежние бюджеты manager. Для жёстко зависшего stop
host имеет конечную защиту; Event Log отмечает service_shutdown_timeout.

## Остановка, удаление, обновление

1. `cmd /c stop-service.cmd`; проверяйте service-status до stopped.
2. `cmd /c remove-service.cmd` из повышенного PowerShell. Работающую/чужую службу
   утилита не удаляет. Удаляется только собственная Run-запись оператора.
3. SQLite, lock-файлы, конфиги, ACL, логи и Caddy CA остаются на месте.
   Для возврата к foreground оператору может потребоваться вернуть ACL:
   это отдельное явное административное действие, не автоматический reset.
4. Для обновления библиотеки при неизменном venv/config достаточно stop → pip upgrade
   → start. Для смены пути Python/config/operator — remove/install; данные не переносить.

При отказе запуска смотрите Windows Event Viewer → Windows Logs → Application
и logs/gateway_server/gateway_server.jsonl. Служебный Event Log содержит только фиксированную
категорию; безопасные детали конфига даст обычный --check-config.
--status службы работает и при сломанном связанном JSON; doctor использует
прежнее локальное управление, не регистрирует командную сессию.

## Linux/systemd

Подготовьте deploy на самой Linux-машине с её venv; Windows unit paths не подходят.
В service/remote-watch-gateway.service проверьте User, Group, абсолютные пути
и TimeoutStopSec (не меньше shutdown_timeout + kill_timeout + запас).
Выбранному непривилегированному аккаунту заранее выдайте чтение Python/конфигов
и запись только runtime directories. Сохраните прежние state_dir и CA.

Из повышенной оболочки: sh service/install-systemd.sh; утилита откажет при
CHANGE_ME или существующем unit. Она включает boot startup, но не запускает сервер.
Затем systemctl start remote-watch-gateway.service; status и journalctl -u
remote-watch-gateway.service. Unit принудительно headless, без Windows GUI.
Для удаления сначала systemctl stop, затем sh service/remove-systemd.sh.
Сравнивается exact unit; изменённый/работающий/чужой unit не удаляется.
SQLite/CA/конфиги остаются. Реальная Linux-проверка не следует из Windows-тестов.

## Как проверить аварийное завершение

Это отдельная проверка, не Stop в трее: Stop выполняет штатную остановку.
Рабочий сервер и SpamBot для неё завершать не нужно. Ниже команды для отдельной
службы **RemoteWatchGatewayTest**, не RemoteWatchGateway. Если тестовый deployment
ещё не подготовлен, оставьте проверку открытой.

1. Разверните отдельную папку, например C:\Work\RemoteWatchTest, с собственным venv,
   JSON, control/logs/SQLite. В связанных JSON задайте новые пути журналов и свободные
   loopback-порты, например 9765/9766. Не копируйте пути production SQLite/control.
   Для команд нужен отдельный тестовый бот/закрытый топик: два reader одного бота
   конкурируют независимо от портов. Не используйте production principals/клиентов.
   Caddy оставьте external; для проверки процессов HTTPS ingress не нужен.
   При проверке каналов понадобится отдельно настроенный тестовый HTTPS-маршрут.
2. В общем JSON задайте ui=headless, для короткого опыта restart.max_restarts=1,
   window=300, initial_delay=2, max_delay=2. Оповещения владельцу включите явно,
   если хотите проверить их доставку. Выполните --check-config.
3. Из административного PowerShell в тестовой папке установите и запустите службу
   с отдельным именем. Все дальнейшие service-команды также требуют этого имени:

   ```powershell
   $testPython = 'C:\Work\RemoteWatchTest\.venv\Scripts\python.exe'
   $testConfig = 'C:\Work\RemoteWatchTest\gateway_server.local.json'
   & $testPython -m remote_watch.gateway_server.service --config $testConfig --name RemoteWatchGatewayTest --install
   & $testPython -m remote_watch.gateway_server.service --config $testConfig --name RemoteWatchGatewayTest --start
   & $testPython -m remote_watch.gateway_server --config $testConfig --status
   ```

4. Для **crash компонента** найдите свежие процессы только этой службы:

   ```powershell
   $testService = Get-CimInstance Win32_Service -Filter "Name='RemoteWatchGatewayTest'"
   if ($testService.State -ne 'Running' -or $testService.ProcessId -eq 0) { throw 'Test service is not running' }
   $testChildren = @(Get-CimInstance Win32_Process | Where-Object {
       $_.ParentProcessId -eq $testService.ProcessId -and
       $_.CommandLine -match 'remote_watch\.gateway_server\.child'
   })
   $testChildren | Select-Object ProcessId, ParentProcessId, ExecutablePath
   ```

   Проверьте, что ExecutablePath относится к тестовому venv. Оба gateway используют
   одинаковый child-модуль; по этой строке нельзя отличить commands от notifications.
   Для простого опыта можно выбрать любой тестовый child. Запишите его ProcessId
   и немедленно завершите **только его**: `Stop-Process -Id <тестовый ProcessId> -Force`.
   Не используйте старый PID и команды завершения всех python.exe.
   В status должны появиться restarting/restarts=1, затем ready; PID child сменится.
   Проверьте служебное сообщение о сбое и восстановлении, если включены получатели.
5. Для **исчерпания бюджета** в пределах 300 секунд повторите поиск свежих child,
   выберите новый PID того же компонента по изменениям status и завершите его снова.
   Если по списку нельзя однозначно выбрать компонент, сначала используйте deployment
   с одним включённым компонентом. Проверьте failed/restart_limit и отсутствие
   бесконечных перезапусков. После опыта штатно остановите тестовую службу.
6. Для **crash manager/service host** сначала заново штатно запустите тестовую службу,
   дождитесь ready, заново получите `$testService` командой из шага 4. Завершите
   только её актуальный `$testService.ProcessId` через Stop-Process -Force.
   SCM должен запустить новый host примерно через 60 секунд после первого отказа;
   второй отказ имеет задержку 300 секунд, третий автоматический запуск не назначен.
   Это отдельный бюджет SCM, не restart.max_restarts компонентов. Действующие настройки
   видны в `sc.exe qfailure RemoteWatchGatewayTest`. Смерть host сама не пошлёт сообщение.
7. Проверьте status/doctor, отсутствие старых дочерних процессов, новые PID, сохранение
   тестовых JSON/SQLite и отсутствие самопроизвольного повторного callback. Если callback
   был прерван после STARTED, допустим UNKNOWN с ручным разбором, а не повтор исполнения.
   Простой опыт без команд не подтверждает сохранение UNKNOWN/поведение callback.
   Удалите только тестовую службу обычным --remove с тем же --name.

Процессы ищутся заново перед каждым аварийным завершением. Если путь/PID/назначение
неоднозначны, не выполняйте Stop-Process. Файлы journal/CA не удаляются и не правятся.
Результат запишите отдельно: какой компонент, что случилось, время восстановления,
restarts, локальная причина и счётчики alert_delivery. Это не тест ночного сбоя SpamBot.
