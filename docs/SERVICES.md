# Постоянный запуск и Windows tray

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-212332

## Windows-служба gateway

Нужны Windows, отдельный venv, extra `service` (pywin32), права администратора
для install/remove и собственные каталоги control/logs/state. Для иконки установите
`tray`. Deploy создаёт launchers и SERVICE.md, но не регистрирует службу автоматически.
Все команды ниже выполняются **Python нужного venv** из deployment-папки.

```console
python -m remote_watch.gateway_server.service --config gateway_server.local.json --install --dry-run
python -m remote_watch.gateway_server.service --config gateway_server.local.json --install
python -m remote_watch.gateway_server.service --config gateway_server.local.json --start
python -m remote_watch.gateway_server.service --config gateway_server.local.json --status
```

Для изменения ACL и SCM install/remove откройте административный PowerShell.
В deploy есть install-service.cmd, start-service.cmd, service-status.cmd,
stop-service.cmd, remove-service.cmd, sync-tray.cmd; `.cmd` работает и при запрете ps1.
Default name — RemoteWatchGateway, account — `NT SERVICE\RemoteWatchGateway`,
без пароля. Другой name задаётся `--name`; повторяйте его во всех service-командах.
При другом административном аккаунте явно задайте SID обычного оператора через
`--operator-sid` и убедитесь, что его профиль доступен.

Install создаёт **остановленную** службу с automatic start, проверяет все пути
до регистрации, даёт ограниченные права на venv/configs и отдельные writable dirs.
Он не меняет общий Python или корпоративные политики. Ошибка DLL/доступа в Session 0
не доказывается и не исправляется обычным интерактивным импортом pywin32.
`service_install_partial` означает частичную установку: часть grants могла остаться.

Конфиги могут хранить literal token с ограниченными ACL. Если выбран token_env,
сделайте переменные доступными аккаунту службы: текущий PowerShell их не передаст.
Служба всегда headless. Параметры UI относятся к отдельному пользовательскому процессу.

## Остановка, удаление и повторная установка

```console
python -m remote_watch.gateway_server.service --config gateway_server.local.json --stop
python -m remote_watch.gateway_server.service --config gateway_server.local.json --remove --dry-run
python -m remote_watch.gateway_server.service --config gateway_server.local.json --remove
```

Remove не удаляет JSON/SQLite/CA и не сбрасывает права всей установки. Повторный
install использует прежние пути. Живой manager запрещает установку; существующий
manager.lock не удаляют. Проверяется реальная блокировка ОС, а не факт наличия файла.
`service_manager_running` — lock занят; `service_control_access` — отказ доступа;
`service_control_io` — другой I/O. Последние два не доказывают живой процесс.
Управление сверяет config/binary/account/оператора, а не только совпадение имени.

## Tray

`application.ui="tray"` включает пользовательский контроллер; `headless` скрывает
иконку. Путь `application.icon_path="logo/remote_watch.ico"` создаётся deploy.
Контроллер запускается при входе оператора, а также start/sync-tray в его активном
сеансе. Служба Windows из Session 0 сама не показывает пользовательский GUI.

Меню: About и Stop gateway server. Stop вызывает SCM stop. При stopped/not_found
иконка скрывается; при следующем running/starting возвращается обычно за две секунды.
Контроллер остаётся в фоне, новый manager не запускает. Нет пункта Exit tray.
RDP disconnect/logoff не должны останавливать службу. Tooltip показывает SCM,
не готовность каждого gateway. Состояния UI и сервера проверяйте раздельно.

Для изменения ui отредактируйте JSON и выполните sync-tray.cmd. При запуске через
services.msc уже работающий контроллер увидит службу; новый GUI-процесс SCM не создаёт.
tray=launch_requested — запрос запуска, не доказательство показа иконки.
operator_session_required требует входа нужного пользователя; ошибка UI не отменяет старт службы.

## Обновление пакета и версии в About

Трей — отдельный длительно работающий процесс. Переустановка пакета и даже
remove/install службы сами по себе не меняют загруженный код и меню About.
Обновляйте пакет в окне обслуживания при остановленной службе, затем выполните
`sync-tray.cmd` и `start-service.cmd`. Удалять и заново устанавливать службу
для обычного обновления Python-пакета не требуется.

Начиная с 1.0.1.dev1, start/sync-tray сравнивают установленную версию с версией
действующего контроллера. Совпавшую версию оставляют; отличающуюся заменяют
кооперативно. Для явного перезапуска той же версии есть `restart-tray.cmd`
и `restart-tray.ps1`. Прямой вызов из venv:

```console
python -I -B -m remote_watch.gateway_server.service --config gateway_server.local.json --restart-tray
```

Команда restart-tray только проверяет привязку службы и обращается к пользовательскому
контроллеру. Она не вызывает SCM stop/start, не меняет автозапуск и не трогает gateway,
SQLite или подключённые приложения. Запускайте её в сеансе назначенного оператора;
при application.ui=headless скрытая иконка не включается вопреки конфигу.

`tray=already_running` — контроллер нужной версии уже есть; `restart_requested` —
старый контроллер освободил lock для замены; `launch_requested` — отправлен запрос
первого запуска. Эти результаты не подтверждают готовность всей сети или показ GUI.
`restart_timeout` означает, что CLI перестал ждать через восемь секунд. Процесс
не убивается; если контроллер уже принял запрос, он может завершить замену позже.
Повторите sync-tray и проверьте About. Служба от этой операции не останавливается.

При переходе с 1.0.0 и прежних dev-версий нужен **однократный ручной перезапуск
только контроллера трея**: старый процесс не умеет принимать запрос замены.
`tray=restart_required` явно сообщает об этом; restart-tray возвращает ненулевой код.
Проверьте пользовательский pythonw-процесс с модулем
`remote_watch.gateway_server.service`, аргументом `--tray` и путём именно вашего
gateway-конфига, завершите только его и повторите sync-tray. Не завершайте
service host/manager, Caddy или все python-процессы. Альтернатива — новый вход
пользователя Windows; простой RDP disconnect сохраняет старый процесс.

Если в существующем deployment ещё нет restart-tray.cmd, используйте прямой вызов
выше или повторный deploy: он добавит новые launchers, сохранив JSON/SQLite/CA.

## Восстановление и проверка

SCM crash recovery host: первая задержка 60 секунд, следующая 300 секунд,
затем без автоматического restart; период сброса счётчика 86400 секунд.
Повторы собственных компонентов отдельно ограничивает restart из server JSON.
Ошибки прав/config/storage не лечатся бесконечным restart. Caddy external имеет
свою службу и свой бюджет, за ним manager не следит.

Проверка новой установки: doctor server/client, уведомление, status-команда,
stop/start, reconnect без повтора callback, remove/install с сохранением данных,
reboot до входа пользователя, вход/выход оператора и отсутствие второго manager.

Crash-тесты проводите на отдельном deployment с отдельными bot reader, портами,
SQLite и именем службы. Получите актуальные PID через SCM и сверку executable/родителя,
не старый status и не имя python.exe. Принудительное завершение child должно дать
ограниченный restart; повтор в пределах window — restart_limit; падение host —
SCM recovery. Проверяйте оповещения владельцу и отсутствие бесконечного цикла.
Если callback мог начаться, UNKNOWN сохраняется, callback не повторяется.

## Linux/systemd

Deploy создаёт unit `remote-watch-gateway.service`, install-systemd.sh и
remove-systemd.sh. До install задайте User/Group вместо CHANGE_ME, проверьте
абсолютные venv/config/workdir пути и права. Install выполняется root, отказывается
перезаписывать существующий unit, проверяет systemd-analyze и включает autostart,
но сам сервис не стартует. Затем `systemctl start remote-watch-gateway.service`.

Type=exec и SCM running означают запуск процесса, а не готовность HTTPS/приложения.
Проверяйте doctor и оба используемых канала. Полевую Windows-приёмку нельзя
переносить на systemd: тест на целевом Linux-хосте остаётся отдельной процедурой.
