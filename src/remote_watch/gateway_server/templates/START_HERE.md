# Gateway server: настройка и первый запуск

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261013-184704

Этот комплект подготовлен remote-watch **@@VERSION@@**. Deploy только записывает
отсутствующие файлы. Серверы, службы, Caddy и сетевые проверки он не запускает.

## Если подключены существующие конфиги

Откройте gateway_server.local.json: notifications.config и commands.config должны
указывать на прежние файлы. Их абсолютное расположение сохранено, поэтому прежние
относительные state_dir/SQLite пути не меняются. Не заменяйте эти файлы примерами
из clients или templates. Клиенты сохраняют свои Identity, токены и журналы.

Если старый командный JSON содержит command_display_mode, перед запуском замените
его на display_mode в клиентском конфиге и элементах sources. Это изменение имени
настройки в 0.4.2.dev5; пересоздавать SQLite не нужно. Python API приложения также
должен быть обновлён до новых имён адаптеров и NotificationGatewayClientConfig.

Для текущей LV-установки с работающей службой Caddy оставьте caddy.mode=external.
Служба продолжает пользоваться своим Caddyfile, прежним хранилищем CA и учётной
записью. Caddyfile.example — только образец маршрутов; копировать его поверх
рабочего Caddyfile не нужно. Клиентам не требуются новые сертификаты.

Перед первым start остановите прежние отдельно запущенные notification gateway
и command gateway, чтобы не было двух readers одного бота и занятых портов.
Службу external Caddy останавливать не требуется. Deploy не делает этот шаг за вас.

## Если созданы новые конфиги

1. Настройте notification_gateway.local.json: Telegram bot token/chat_id и ntfy
   topic/token. Неиспользуемое назначение удалите одновременно из destinations
   и principals[0].aliases. Для публичного ntfy без token удалите token_env;
   такой топик не даёт права принимать команды.
2. Настройте command_gateway.local.json: bot token, actor_id (ID вашего Telegram
   аккаунта) и conversation_id (ID чата). Одинаковый бот у двух компонентов
   допустим: updates читает только командный компонент.
3. В обоих конфигах совпадают пять полей Identity одного приложения. Замените
   их на настоящие; те же значения задайте в приложении. Дополнительные instances
   требуют отдельных Identity, командных principal/token и явных targets/scopes.
4. token_env — имя переменной окружения, а не токен. Можно заменить весь ключ
   token_env на token и указать значение прямо в закрытом локальном JSON.
   Оба ключа одновременно запрещены. Для приложения нужны **два разных** случайных
   секрета: уведомлений и команд, минимум 32 символа. Создайте их своим менеджером
   секретов и внесите одинаковые значения на соответствующих сторонах соединения.
   В шаблонах нет общедоступного рабочего секрета: пока переменные не заданы,
   запуск завершится отказом. Deploy не генерирует и не печатает секреты.
5. clients/command_client.example.json — готовая форма командного клиента.
   clients/notification_client.example.json — памятка для NotificationGatewayClientConfig,
   а не отдельный поддерживаемый загрузчик JSON. Перенесите значения в конфиг приложения;
   identity относится к WatcherConfig, ca_file — к проверяющему SSLContext.
   Endpoint — внешний HTTPS-адрес, не внутренние порты 8765/8766.
6. Ограничьте доступ к папке и локальным JSON учётной записью сервера. Windows-файлы
   наследуют ACL папки; deploy не меняет права существующей установки. Конфиги
   и содержимое clients не следует публиковать или добавлять в общий репозиторий.

## Caddy и TLS

External: Caddy уже обслуживается отдельно. Убедитесь, что /v1/commands/* идёт
на command_port, а /v1/notifications — на notification_port общего конфига.
Путь /v1/commands/ сохраняется целиком, поэтому в Caddy нужен handle, не handle_path.

Managed: используйте явно выбранные caddy_executable и caddy_data. Для прежней
установки укажите также прежний caddy_config. В Caddyfile должен быть абсолютный
storage file_system, совпадающий с data_dir. Никогда не выбирайте новую пустую
папку для уже действующего CA. Два процесса Caddy на одном порту запускать нельзя.

Для новой установки создаётся Caddyfile с tls internal. Запуск Caddy впервые создаст
CA в выбранном storage. Передайте клиентам только публичный root.crt из
pki/authorities/local этого storage и настройте ca_file/проверяющий SSLContext.
Закрытые .key, серверные leaf.crt и intermediate.crt клиентам не нужны.
Проверка TLS остаётся включённой; будущие продления не требуют смены root.crt.
Шаблон отключает HTTP redirects: порт 80 для этой схемы не требуется.

Проверка Caddyfile командой caddy validate выполняется оператором отдельно;
check-config.ps1 проверяет JSON, но не конфигурацию Caddy или сеть.

## Команды управления

В Windows PowerShell из папки комплекта:

```powershell
.\check-config.ps1
.\start.ps1
```

Start работает до Ctrl+C или команды stop из другого окна:

```powershell
.\status.ps1
.\stop.ps1
```

При запрете локальных .ps1 используйте Python из выбранного venv напрямую.
Например, для установки в C:\Work\RemoteWatch:

```powershell
C:\Work\RemoteWatch\.venv\Scripts\python.exe -m remote_watch.gateway_server --config C:\Work\RemoteWatch\gateway_server.local.json --check-config
```

Для управления замените --check-config на --start/--status/--stop.
Изменение политики PowerShell для этого не требуется.

Ошибка component_config означает отказ связанного JSON. Поля component_code и
component_field указывают причину и место внутри файла компонента. Например,
field=commands и component_field=sources[1] относятся ко второму источнику команд.
При config_unknown сверьте названия полей; старый command_display_mode в sources
нужно заменить на display_mode, оставив значение. Проверка не исправляет файл сама.
В старой версии без подробностей запустите проверку компонента отдельно:

```powershell
C:\Work\RemoteWatch\.venv\Scripts\python.exe -m remote_watch.gateway.commands --config C:\Work\RemoteWatch\command_gateway.local.json --check-config
```

Используйте фактический путь из commands.config. Аналогичная проверка уведомлений:
модуль remote_watch.gateway с путём из notifications.config и --check-config.
Содержимое JSON и токены для диагностики пересылать не нужно.
В Linux соответствуют `sh check-config.sh`, `sh start.sh`, `sh status.sh`, `sh stop.sh`.
Скрипты используют выбранный при deploy Python и работают из любой текущей папки.

Логи manager находятся в log_dir, готовность компонентов — в status. Для трея
задайте application.ui=tray на Windows и установите extra tray. Логотип берётся
из установленного пакета; свой путь можно задать через application.icon_path.
Оповещения владельцу настраиваются отдельно в application.alerts.destinations;
используйте прямые Telegram/ntfy назначения с явными получателями.

Версию окружения фиксируйте на **@@VERSION@@**, с extras gateway,commands,telegram,ntfy
и при необходимости tray. Если версия получена из Git, сохраняйте точный commit
или wheel; не выполняйте pip install из изменяющейся ветки при каждом старте.
Скрипты не устанавливают и не обновляют зависимости. Иной --python не проверяется
запуском: оператор должен установить туда соответствующую версию и extras.

## Обновление и полевая проверка

Повторный deploy показывает create/identical/preserved. Preserved означает, что
существующий файл отличается, но не перезаписан. Даже собственный manifest deploy
не служит разрешением заменить файл. --update-templates складывает предложения в
templates/версия; current_preserved показывает отличия по файлам без раскрытия
содержимого. Эти образцы не запускайте прямо из вложенной папки: относительные
пути рассчитаны на основной комплект. Переносите только нужные настройки вручную.
При ошибке записи может остаться часть новых файлов; ничего не удаляется автоматически.
Проверьте комплект перед повтором. Неполный существующий файл тоже сохраняется.

Проверьте уведомление и status-команду каждого приложения, затем остановку и новый
запуск manager с прежними журналами. Командные клиенты поддерживают reconnect;
неразрешённый UNKNOWN требует разбирательства и не повторяет callback.
Чужие SQLite/CA нельзя заменять шаблонами или откатывать для повторения теста.

Автозапуск после reboot/выхода из RDP ещё не устанавливается: это следующий этап
службы ОС. Gateway doctor также пока не реализован; используйте check-config,
status и существующие явные smoke. Готовность manager не доказывает доставку на телефон.

Настройки Caddy сверены с официальными описаниями
[storage](https://caddyserver.com/docs/caddyfile/options#storage) и
[handle](https://caddyserver.com/docs/caddyfile/directives/handle).
