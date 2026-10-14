# ADR 0019: служба gateway и отдельный пользовательский трей

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-131658

## Решение

0.4.5 добавляет явные install/remove/start/stop/status и dry-run. Deploy всегда
поставляет инструкции и launchers; сам deploy ничего не регистрирует и не запускает.
Windows-служба использует отдельную виртуальную учётную запись NT SERVICE\NAME,
выбранный venv Python и абсолютный путь прежнего deployment. Пароля нет. Caddy,
SQLite, конфиги приложений и CA сохраняют прежние пути. Второго manager/reader нет.

Host подключается к SCM через optional extra service (pywin32), используя
собственный python -I -B -m, без PythonService.exe/postinstall и глобальных DLL.
SCM START_PENDING сохраняется до завершения начального запуска manager;
RUNNING не означает доставку провайдером или доступность приложений.
STOP/SHUTDOWN идут в общий stop_event, без поиска/убийства чужих PID.
Неисправность конфигурации завершается с безопасной категорией в Event Log.
После crash SCM делает не больше двух повторов (60 и 300 секунд), затем NONE;
счётчик сбрасывается после суток без падений. Известные временные startup отказы
используют тот же бюджет только после полного rollback manager; ошибки
configuration/authorization/ownership не повторяются. Штатный stop не вызывает restart.

application.ui=tray/headless остаётся общей настройкой. Служба всегда headless.
Отдельный controller запускается в интерактивном сеансе и не создаёт manager.
Он наблюдает SCM, показывает About и Stop gateway server. Закрытие UI/выход
пользователя не останавливает службу. При tray устанавливается HKCU Run только
для явно выбранного локального оператора; это не системный интерактивный сервис.
SCM running в tooltip — состояние службы, а не готовность компонентов.

Установка требует администратора. Оператор получает query/start/stop службы,
но не изменение ImagePath, account или DACL. Control/log directories разрешены
только service/System и оператору; токены control не публикуются через Caddy.
ACL команды ограничены явными локальными путями без reparse points; виртуальному
аккаунту даются чтение venv/конфигов и запись только runtime directories.
Log directory — выделенный logs/gateway_server: общая logs с файлами Caddy
отвергается до регистрации и ACL. Повторный deploy сохраняет прежний JSON;
оператор явно меняет log_dir, не перемещая SQLite/CA/старые логи.
Существующий manager должен быть остановлен до изменения прав. Существующая
одноимённая служба не перезаписывается. Управление/удаление сверяет exact binary
command и account, а не только имя. Удаление требует остановленной службы;
рабочие данные, ACL и CA не удаляются. Частично выданные ACL при ошибке установки
не откатываются вслепую; ошибка отмечается явно, новая служба откатывается.

Для Linux deploy содержит отдельный systemd unit и явные install/remove scripts.
Unit не создаёт GUI и использует постоянные прежние пути; полномочия и учётная
запись задаются оператором. Реальные systemd/reboot проверки фиксируются отдельно.

## Основания и проверка

Windows изолирует службы в Session 0: [Interactive Services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services).
Виртуальный account не требует пароля: [Service accounts](https://learn.microsoft.com/en-us/windows/security/identity-protection/access-control/service-accounts).
Делегируются только нужные [SCM rights](https://learn.microsoft.com/en-us/windows/win32/services/service-security-and-access-rights).
После последнего recovery action SCM повторяет его: поэтому последним выбран NONE.
Host использует [pywin32 service API](https://mhammond.github.io/pywin32/win32service.html).

Автотесты проверяют безопасный план, отсутствие записи при dry-run, сохранение
файлов, отказы при чужой/работающей службе, переходы SCM, отключение GUI в host,
пользовательский controller и шаблоны. Реальная установка в production, reboot,
выход RDP, crash и приёмка на телефоне не следуют из fake-тестов.
