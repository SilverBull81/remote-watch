# Gateway doctor: проверка сервера и клиентского подключения

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261013-230511

Начиная с **0.4.4.dev1** доступна конечная диагностика
`python -m remote_watch.diagnostics.gateway_doctor`. Она не запускает и не останавливает
сервер, не регистрирует клиентскую сессию, не выполняет команды и не отправляет
уведомления. Рабочие SQLite-журналы не открываются: recovery, migration, очистка,
смена generation и cursor не выполняются.

По умолчанию проверяются только локальные настройки. Для сети необходимо явно
добавить `--online`. Проверка использует выбранный venv, а не Python другого приложения.

## На сервере LV

Для существующей установки в `C:\Work\RemoteWatch`:

```powershell
C:\Work\RemoteWatch\.venv\Scripts\python.exe -m remote_watch.diagnostics.gateway_doctor --mode server --config C:\Work\RemoteWatch\gateway_server.local.json --online
```

Без `--online` проверяются основной и связанные JSON, предусмотренные схемой пути,
порты и наличие модулей зависимостей. Секреты из `token_env` не разрешаются,
процессы и сеть не запускаются. Это не проверка валидности токена у провайдера.

С `--online` doctor читает существующий локальный status manager и проверяет TCP
его notification/command listeners. Готовность времени берётся из состояния
command-компонента; doctor не выполняет новый запрос к TimeAPI. Если manager
остановлен, выводится `control_unavailable`: doctor не пытается занять его место.

В deploy-комплект входят `doctor.ps1` и `doctor.sh`. Из папки установки:

```powershell
.\doctor.ps1 --online --report .\doctor-server-01.json
```

Если политика PowerShell запрещает скрипты, используйте прямую команду Python выше;
изменять общесистемную политику для doctor не требуется. Для Linux: `sh doctor.sh --online`.

Серверная проверка локальных listeners не проверяет внешний маршрут Caddy, TLS или
доступность из RU. Для этого запускайте клиентский режим на соответствующей машине.
Внешний Caddy не управляется doctor независимо от результата проверки.

## На клиентской машине

Deploy создаёт `clients/gateway_doctor.client.example.json`; публичный образец также
находится в [examples/gateway_doctor.client.example.json](examples/gateway_doctor.client.example.json).
Скопируйте его в отдельный закрытый `gateway_doctor.client.local.json` и задайте:

- `endpoint`: внешний HTTPS origin Caddy, например `https://адрес-VM:8443`;
  без пути `/v1/...`, логина или query. Порты 8765/8766 — внутренние listeners.
- `ca_file`: корневой CA, которому доверяет приложение. Относительный путь считается
  от папки JSON. Для `tls internal` это прежний `root.crt`, не leaf и не приватный key.
  Отсутствие поля или `null` означает стандартное хранилище доверия Python.
- `identity`: все пять значений именно проверяемого приложения, как в principal gateway.
  Doctor не берёт их из имени чата, DNS или имени текущей машины.
- `notifications`: отдельный сервисный `token` либо `token_env` и список разрешённых
  `aliases`. Это токен приложения для notification gateway, не Telegram bot token.
- `commands`: другой сервисный `token` либо `token_env` и список `scopes` из principal,
  например `command:status`. Scope не обязательно совпадает с названием команды.

Можно удалить любой из двух каналов, но должен остаться хотя бы один. Для каждого
канала задаётся ровно один из `token`/`token_env`; секреты двух каналов должны различаться.
Шаблон согласован с новым deploy для `app1/one`; существующая установка может иметь
совсем другие Identity, aliases и scopes — перенесите свои значения.

```powershell
<Python приложения> -m remote_watch.diagnostics.gateway_doctor --mode client --config gateway_doctor.client.local.json --online --report doctor-client-01.json
```

Замените `<Python приложения>` на полный путь к его `python.exe`. Клиентский doctor
использует стандартную библиотеку Python и не требует extras. Он не импортирует
SpamBot, не открывает его journal и не пытается получить credentials из произвольного
конфига приложения. Поэтому проверку можно выполнять рядом с работающим клиентом.

Последовательность для каждого канала: DNS → TCP → проверяемый TLS → маршрут →
аутентификация токена, точная Identity и запрошенные права → текущая готовность.
Командный ответ также сообщает наличие действующей сессии этой Identity.
Отсутствие сессии — `warning`, а не ошибка подключения: приложение может быть ещё
не запущено. Если приложение работает, сверяйте это предупреждение с его текущими
`command_stats.health`, прежде всего `connection_state`, `reconnect_reason` и `ready`.
Doctor не исправляет reconnect и не продлевает существующую сессию.

## Обновление gateway и Caddy

Для online client-проверки нужны gateway **0.4.4.dev1+** и два read-only маршрута:

```caddyfile
handle /v1/notifications/probe {
    reverse_proxy 127.0.0.1:8765
}
```

Добавьте этот блок **внутрь существующего блока сайта**, рядом с прежним точным
`handle /v1/notifications`. Сам прежний маршрут доставки сохраните. Для commands
существующий `handle /v1/commands/*` уже включает `/v1/commands/probe`; если маршруты
перечислены по одному, добавьте его с upstream `127.0.0.1:8766`.
Используйте фактические порты своего deployment.

Новый deploy включает маршрут сразу. Повторный deploy сохраняет рабочие файлы;
`--update-templates` только подготавливает образцы для сравнения. Проверяйте и
перезагружайте конфигурацию уже работающего Caddy обычным способом из
[GATEWAY_TLS.md](GATEWAY_TLS.md), сохраняя его учётную запись, storage и корневой CA.
Doctor никогда не меняет Caddyfile, службу, сертификаты или доверие клиентов.
Старый gateway/маршрут даст `route_missing`; fallback через register/send отсутствует.

## Как читать результат

| Код | Что проверять дальше |
| --- | --- |
| `config_invalid` | `config_code`, `field`, при наличии `component_code/component_field`; значения скрыты |
| `ca_read` / `ca_invalid` в `config_code` | Путь, доступ и PEM-формат корневого CA |
| `dns_unavailable` / `dns_timeout` | Имя endpoint и DNS на проверяемой машине |
| `tcp_refused` / `tcp_timeout` | Адрес, порт, listener, VPN и Firewall |
| `tls_certificate` | `verify_code`, `tls_reason`, доверенный CA, имя и часы |
| `tls_handshake` | HTTPS ли работает на этом порту |
| `route_missing` / `route_unverified` | Версию gateway и маршрут нужного канала в Caddy |
| `upstream_unavailable` | Caddy доступен, но вернул 502/503/504 без признака ответа doctor |
| `credential_denied` | Сервисный токен principal нужного канала |
| `permission_denied` | Точную Identity и разрешённые aliases/scopes |
| `time_unavailable` | Готовность времени на gateway; отдельная проверка описана в [COMMAND_TIME.md](COMMAND_TIME.md) |
| `application_not_registered` | Локальное состояние командного клиента и восстановление после restart |
| `probe_busy` | Заняты отдельные диагностические слоты; повторить позже |

`verify_code=10` означает истечение сертификата в проверяемой цепочке, **не определяет
её просроченный элемент**. Doctor не делает вывода, что это обязательно leaf или CA.
TLS-проверка имени, цепочки и срока обязательна. Redirect отклоняется без отправки
токена по новому адресу; proxy из environment не используется.

`--timeout` задаёт предел одного этапа, по умолчанию 5 секунд, допустимо 0.1–10.
На канал приходится максимум четыре этапа; DNS вызывается один раз, TCP пробует
не более четырёх полученных адресов в одном общем сроке. POST один, без повторов.
Зависший системный DNS не удерживает CLI: поздний результат не запускает соединение.
Уже начавшийся read-only обмен может завершиться после срока наблюдения; новых
операций после отмены doctor не начинает.

`--json` заменяет текст на JSON. `--report` создаёт только новый файл, не перезаписывает
существующий и не создаёт отсутствующую родительскую папку. Отчёт содержит фиксированные
коды, версию, статусы и безопасные TLS-поля; endpoint, пути, Identity, tokens, chat ID,
тела ответов и произвольные исключения в него не попадают. Сам закрытый конфиг не
прикладывайте к отчёту. Коды выхода: **0** — нет failed, **1** — проверка не пройдена,
**2** — неверные аргументы или невозможно записать новый отчёт. Skipped и warning
остаются видимыми даже при коде 0: offline success не доказывает работоспособность сети.

## Границы первой версии

Doctor не читает SQLite и не перечисляет UNKNOWN: `journal_unknown_outcomes=skipped`
не означает, что UNKNOWN нет. Их расследование остаётся отдельной операцией по
[COMMAND_STORAGE.md](COMMAND_STORAGE.md). Внешний command probe показывает состояние
hub и сессии, но не доказывает работу reader бота, actor/chat ACL или callback;
состояние источников дополнительно наблюдается через server manager.
Ни один результат не подтверждает получение сообщения телефоном. Для настоящей
доставки и выполнения используйте явно запускаемые [SMOKE.md](SMOKE.md) и
[COMMAND_SOURCES.md](COMMAND_SOURCES.md). Автоматического repair и запуска служб нет.
