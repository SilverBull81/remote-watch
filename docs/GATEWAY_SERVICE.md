# Gateway как служба ОС

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-131658

## Что реализовано в 0.4.5.dev1

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
6. Проверьте обычные --status и doctor server --online. Для иконки сейчас:

   ```powershell
   .\.venv\Scripts\pythonw.exe -I -B -m remote_watch.gateway_server.service --config C:\Work\RemoteWatch\gateway_server.local.json --tray
   ```

   Затем она стартует при входе пользователя. Exit tray/выход RDP не останавливают
   службу. Tooltip отражает **SCM**, а не готовность всех компонентов.
   В обычном status поле ui у самого manager остаётся headless: controller отдельный.

Default: RemoteWatchGateway и SID текущего пользователя. Если административные
команды выполняет другой аккаунт, укажите SID обычного оператора через
--operator-sid во всех CLI. Его профиль должен быть загружен для автозапуска UI.
Управление сверяет exact binary/config/account, а не только имя службы;
--status/--stop работают и при сломанном связанном JSON.

## Проверки в поле перед закрытием 0.4

- Один manager и по одному reader; уведомление и команда каждому приложению.
- Stop из трея → SCM stopped; start → восстановление клиентов без повтора callback.
- Exit tray, повторный запуск иконки, ui=headless/sync-tray и возврат tray не
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
