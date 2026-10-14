# Gateway как служба ОС

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-161558

## Что реализовано в 0.4.5.dev3

Deploy всегда включает SERVICE.md, install/remove/start/stop-service.cmd и .ps1,
service-status, sync-tray и tray.ps1. На Windows используется optional extra
`service` с pywin32; `tray` нужен интерактивному controller. Core, обычные клиенты,
Linux и --help не импортируют Windows DLL или GUI. Генерация deploy не регистрирует
и не запускает службу. Контракт: [ADR 0019](adr/0019-gateway-os-service.md).

Полная инструкция, поставляемая в комплекте:
[SERVICE.md](../src/remote_watch/gateway_server/templates/SERVICE.md).

## Переключение существующего LV gateway

1. Выберите окно обслуживания и остановите прежний foreground gateway_server.
   Оставьте Caddy работающим. Второго manager/reader запускать нельзя.
2. Сохраните прежние JSON, state_dir, control_dir и CA. При остановленных
   владельцах сделайте резервную копию журналов. Пустые шаблоны не подставляйте.
   **Измените только log_dir на logs/gateway_server**: прежняя общая logs
   используется внешним Caddy, менять её права как личного каталога нельзя.
   При service_shared_logs выберите пустую выделенную папку; старые логи остаются.
3. Обновите тот же venv проверенным commit с extras
   `gateway,commands,telegram,ntfy,service,tray`. Повторите deploy с прежними
   ссылками на конфиги. Новые service launchers добавятся; старые файлы сохранятся.
4. В общем JSON достаточно прежнего `application.ui`: `tray` либо `headless`.
   Новый обязательный service-раздел или секрет не нужен. icon_path остаётся
   `logo/remote_watch.ico`.
5. В повышенном PowerShell своего обычного пользователя:

   ```powershell
   Set-Location C:\Work\RemoteWatch
   cmd /c install-service.cmd --dry-run
   cmd /c install-service.cmd
   cmd /c start-service.cmd
   cmd /c service-status.cmd
   ```

   Установка оставляет службу остановленной. Если ошибка service_install_partial,
   прочитайте stage/winerror и SERVICE.md. ExecutionPolicy менять не требуется.
6. Проверьте обычные --status и doctor server --online. При ui=tray команды
   start-service и sync-tray запрашивают контроллер в текущем сеансе оператора.
   Отдельно его можно запустить той же безопасной командой:

   ```powershell
   .\.venv\Scripts\pythonw.exe -I -B -m remote_watch.gateway_server.service --config C:\Work\RemoteWatch\gateway_server.local.json --tray
   ```

   После входа контроллер также запускается через пользовательский Run.
   При stopped/удалённой службе иконка скрыта, при следующем start появляется.
   В меню только About и Stop gateway server. Контроллер остаётся в фоне,
   не запускает manager и не останавливает службу при выходе пользователя.
   Tooltip отражает **SCM**, а не готовность всех компонентов.
   В обычном status поле ui у самого manager остаётся headless: controller отдельный.

Default: RemoteWatchGateway и SID текущего пользователя. Если административные
команды выполняет другой аккаунт, укажите SID обычного оператора через
--operator-sid во всех CLI. Его профиль должен быть загружен для автозапуска UI.
Управление сверяет exact binary/config/account, а не только имя службы;
--status/--stop работают и при сломанном связанном JSON.

## Повторная установка после работы службы

В 0.4.5.dev1/dev2 чтение control было разрешено оператору, а установщик запрашивал
запись в manager.lock. Такой отказ ошибочно превращался в service_manager_running.
На LV подтверждено: служба уже удалена (1060), файл открывается rb, но не r+b;
DACL оставляет полный доступ service/System и чтение оператору. Данные не менялись.

В dev3 существующий lock открывается для чтения и удерживается той же блокировкой
ОС до конца установки. Живой manager по-прежнему исключает установку. Уже выданные
полные права прежнего service SID проверяются и не переписываются без необходимости,
включая дочерние файлы, созданные службой. Нельзя удалять lock, сбрасывать права всей
папки или удалять SQLite/CA для обхода ошибки.

После обновления venv повторите install-service.cmd --dry-run, install-service.cmd,
start-service.cmd и doctor. Обновлённая библиотека не требует замены JSON/SQLite/CA.
Уже запущенный старый контроллер сам не обновится: у dev1/dev2 можно один раз
выбрать старый пункт Exit tray перед обновлением. Другой вариант — завершить его процесс
pythonw из этого venv и с аргументом --tray либо выйдите и войдите в сеанс Windows.
Не завершайте процессы --run, manager или child. Последующие start/sync-tray
запускают новый контроллер автоматически; повторный запуск не создаёт второй сервер.

Безопасные причины проверки владельца имеют stage=ownership:

- service_manager_running — блокировка ОС занята действующим владельцем.
- service_control_access — не удалось открыть или создать файл из-за прав.
- service_control_io — другая ошибка ввода-вывода; это не доказательство живого manager.

winerror сохраняется только если он действительно предоставлен Windows, иначе null.
Ошибки выдачи прав после регистрации сохраняют service_install_partial/stage=acl.
Временный файл блокировки и права по имени процесса автоматически не удаляются.

## Автоматическая иконка

Контроллер работает в интерактивном сеансе отдельно от службы Windows Session 0.
Это позволяет службе запускаться до входа и переживать выход пользователя.
При stopped/not_found и при ui=headless иконка скрывается, но контроллер сохраняет
свой цикл наблюдения. При следующем running/starting и ui=tray она возвращается
обычно в пределах двух секунд. В меню нет Exit tray: для отключения используйте
application.ui=headless и sync-tray.cmd. Обратное переключение также не требует
перезапуска службы. Ошибка/неизвестное состояние SCM показывается в tooltip;
отсутствие иконки не является проверкой готовности каналов.

start-service и sync-tray могут запустить GUI только в текущем ненулевом сеансе
того же оператора. При другом аккаунте/Session 0 ответ tray=operator_session_required:
используйте Run при входе либо запустите --tray в сеансе оператора. Запуск через
services.msc сам GUI не создаёт: его увидит уже работающий контроллер.
tray=launch_requested означает запрос запуска, не подтверждение готовности иконки.
tray=unavailable не отменяет уже принятый SCM запуск; ошибка UI не останавливает сервер.

Схема соответствует рекомендации Microsoft разделять службу и пользовательский GUI:
[Interactive Services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services).
Блокировка существующего файла разрешена с доступом GENERIC_READ:
[LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex).

## Проверки в поле перед закрытием 0.4

- Один manager и по одному reader; уведомление и команда каждому приложению.
- Stop из трея → SCM stopped; start → восстановление клиентов без повтора callback.
- Скрытие/возврат иконки после stop/start, повторный запуск контроллера,
  ui=headless/sync-tray и возврат tray не
  создают второй manager. Разрыв RDP и полный выход пользователя не останавливают службу.
- При согласованном reboot служба стартует **до входа** пользователя; Caddy
  сохраняет CA, после входа появляется иконка. Проверить doctor и оба канала.
- Crash manager/компонента и конечные бюджеты восстановления проверять на
  тестовом deployment. Production процесс без отдельного решения не убивать.
  UNKNOWN сохраняется, callback автоматически не повторяется.
- При остановке проверить remove и сохранение JSON/SQLite/CA; повторный install
  использует прежние пути. Fake/native-console тесты не доказывают права и DLL в Session 0.
- Новые journal summaries проверить через status/doctor; применить notification
  health в приложении и проверить отказ → успешную доставку.

Linux deploy содержит unit и install/remove-systemd.sh. User/Group и права задаются
явно до установки. Type=exec — процесс запущен, не доказательство готовности manager.
Реальные systemd/reboot проверки и финальная CI-матрица фиксируются отдельно.
