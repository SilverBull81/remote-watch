# Повторное истечение сертификата Caddy: диагностика и постоянный запуск

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261006-170841

## Подтверждённая причина на LV, 06.10.2026

06.10.2026 владелец сообщил о повторном отказе SpamBot через Caddy 2.11.4
на Windows, `tls internal`: `verify_code=10`, `certificate_expired`.
Caddy был запущен вручную в PowerShell из `C:\Work\RemoteWatch`.
В ходе работы выяснилось, что проблемная LV доступна в текущем окружении;
владелец подтвердил принадлежность найденного экземпляра. Выполнены прямые
проверки без чтения credentials, регистрации сессии или отключения TLS:

- На 09:29 UTC peer отдавал leaf, истёкший в **01:14:40 UTC 06.10.2026**.
  OpenSSL с `-verify_return_error`, проверкой IP и прежним root CA показал
  `depth=0`, код 10. SHA-256 peer совпал с leaf в действующем storage.
- Intermediate был действителен до 07.10.2026 10:20:10 UTC, root — до 2036 года.
  Просрочен именно leaf, не корневой CA. Это уже проверенная цепочка,
  а не вывод только из кода 10.
- PID 8376 работал с 05.10.2026 13:35:20 UTC. Службы Caddy не было.
  Storage в Caddyfile был задан явно и совпал с активным конфигом;
  аккаунт процесса имел унаследованное Modify, включая файл root key.
  Чтение ACL не затрагивало содержимое закрытого ключа.
- Локальный admin API зависал, хотя TLS listener продолжал отвечать.
  WinAPI `GetConsoleSelectionInfo` вернул flags=7: активное непустое
  выделение текста в консоли Caddy.
- Владелец нажал Esc. **Без перезапуска, с тем же PID** admin API заработал,
  а в 13:24:17 UTC Caddy выпустил новые leaf и intermediate. Leaf действует
  до 07.10.2026 01:24:17 UTC, intermediate — до 13.10.2026 13:24:17 UTC.
  Новый TLS handshake прошёл с прежним root: `Verify return code: 0`.

Наблюдение до/после Esc подтверждает блокировку консольного вывода режимом
выделения как причину **этого** отказа продления. Предыдущий случай без
журнала отдельно не доказан. Речь не об истечении клиента или необходимости
копировать новый leaf на RU. Файловые журналы прежнего процесса отсутствовали;
полную ретроспективу renew attempts восстановить нельзя.

Исходные локальные материалы лежат в игнорируемом `build/`:
`caddy-before.json`, `caddy-after.json`, `caddy-peer-before.txt`,
`caddy-peer-after.txt`, `caddy-console-state.json`. Они содержат реальные пути
и публичные сертификаты, поэтому в репозиторий не включаются.
Механизм консольного выделения описан в
[документации Microsoft](https://learn.microsoft.com/en-us/windows/console/getconsoleselectioninfo)
и обсуждении [Microsoft Terminal #2678](https://github.com/microsoft/terminal/issues/2678).

В общем случае код 10 относится к проверке цепочки по часам клиента. Он не определяет,
просрочен ли leaf, intermediate или root. Поэтому при новом инциденте повторите
проверку цепочки; не переносите сегодняшнюю причину на любой TLS-отказ.
На обеих VM надо записать UTC: известное расхождение часов также влияет на TLS.
TrustedClock команд и допустимое расхождение часов relay проверку TLS не меняют.

Дальнейшая защита — файловый журнал и служба без интерактивного окна.
Успех после Esc не заменяет приёмку следующего планового продления и reboot.
TLS остаётся обязательным на каждом шаге.

### Применённая настройка и текущая проверка

На LV установлена служба **RemoteWatchCaddy**, `StartMode=Auto`, аккаунт
`NT AUTHORITY\LocalService`. Включено восстановление после сбоя с задержками
5/15/60 секунд. Ручной Caddy штатно остановлен через локальный admin API;
служба запущена с тем же абсолютным Caddyfile и storage. Сохранена резервная
копия прежнего Caddyfile рядом с ним; SHA-256 root CA до/после совпал.
Python-процессы gateway не перезапускались. TLS после переключения проверен
с прежним CA и проверкой IP, результат — успешный.

Runtime log: `C:\Work\RemoteWatch\logs\caddy-runtime.jsonl`, ротация 10 MiB,
не более 10 архивов, срок хранения 14 дней. Текущий файл отдельный от архивов.
Службе даны права Modify на storage/logs и чтение бинарного файла/конфига.
Проверка запуска выполнена; автоматический запуск после reboot пока не доказан.

В 14:02 UTC запущено конечное **25-часовое наблюдение** с новой TLS-сессией
каждую минуту и неизменным исходным trust context. Оно не отправляет HTTP,
credentials или команды. Отчёт: `C:\Work\remote-watch\build\caddy-tls-observation.jsonl`;
в нём UTC, SHA-256 root/leaf, serial/notAfter, результат проверки и признак жизни
исходного PID службы. Первые проверки успешны; последующие результаты ещё
нужно проанализировать. Это локальное LV-наблюдение, не замена RU monitoring-check.
Вспомогательный процесс завершится сам; reboot остановит его раньше, автозапуска
у этого одноразового наблюдения нет. Служба Caddy от него не зависит.

Владелец выбрал **самостоятельную перезагрузку VM позже** и передачу результата.
Из этого решения нельзя выводить подтверждение reboot или очередного продления.

## 1. Сохранить состояние до перезапуска

Скопируйте [collect_caddy_state.ps1](examples/collect_caddy_state.ps1) на LV,
например в `C:\Work\RemoteWatch`. Скрипт только читает сведения о процессах,
службах, ACL каталога и публичные `.crt`; ключи `.key`, конфиги, переменные
среды и содержимое журналов он не читает. Отчёт содержит локальные пути
и имя учётной записи; это диагностический файл для владельца, не публичный лог.
Если корпоративная политика запрещает запуск скрипта, не обходите её.

В отдельном PowerShell на LV выполните:

```powershell
Set-Location C:\Work\RemoteWatch
.\collect_caddy_state.ps1 -StoragePath C:\Work\RemoteWatch\caddy-data |
    Set-Content -Encoding UTF8 C:\Work\RemoteWatch\caddy-before.json
```

`StoragePath` должен совпадать с **фактическим** storage Caddy. Посмотрите
локально существующий `Caddyfile`: `storage file_system ...`, а также способ
первоначального запуска. При отсутствии явного storage проверьте
`$env:APPDATA\Caddy` для той же учётной записи. `caddy environ` из нового окна
показывает среду нового процесса, а не работающего Caddy; весь его вывод не присылайте.
Не создавайте пустой `caddy-data`, чтобы просто убрать `storage_not_found`.

В отчёте нужны `processes`, `services`, `issues` и `certificates`. Для каждого
PEM-блока будут SHA-256, serial, сроки UTC и признак CA. `pem_index=0` в файле
серверной цепочки обычно соответствует leaf; последующие блоки — intermediates.
Отдельно проверьте `pki\authorities\local\root.crt` и `intermediate.crt`.
Сравните сроки с UTC обеих VM, а не только с `expired_at_server_time`.

Один PID и время запуска не доказывают отсутствие остановки/приостановки VM.
Также проверьте события сна, возобновления, перезагрузки и изменения времени
в Windows Event Viewer за период между последним успехом и первым отказом.
Два Caddy, разные storage или разные владельцы процесса требуют выяснения,
какой PID обслуживает 8443: `Get-NetTCPConnection -State Listen -LocalPort 8443`.

При ручном запуске проверьте, не оставлено ли выделение текста в консоли.
Сначала сохраните диагностику, затем нажмите Esc, не Ctrl+C, и сравните
API/сертификат при том же PID. Даже без выделения процесс 24/7 не должен
зависеть от открытого окна RDP или поведения его консоли.

### Фактически отдаваемая цепочка

Файлы на диске могут уже отличаться от сертификата в памяти процесса. Для
точного определения просроченного элемента нужна проверка соединения с тем же
адресом и CA, что использует SpamBot. Если OpenSSL уже установлен на RU
(например, вместе с Git), выполните для IP-адреса:

```powershell
$openssl = 'C:\Program Files\Git\usr\bin\openssl.exe'
$gatewayIp = 'IP_ВАШЕЙ_LV_VM'
$rootCa = 'C:\Work\RemoteWatch\secrets\caddy-root.crt'
'' | & $openssl s_client -connect "${gatewayIp}:8443" -verify_ip $gatewayIp `
    -CAfile $rootCa -verify_return_error -showcerts 2>&1 |
    Set-Content -Encoding UTF8 C:\Work\RemoteWatch\caddy-peer-before.txt
```

Подставьте существующий CA **из конфига клиента**, не новый CA с сервера.
Если клиент использует DNS-имя, замените `-verify_ip $gatewayIp` на
`-servername $gatewayName -verify_hostname $gatewayName`, и используйте это
имя в `-connect`. Не посылайте SNI с выдуманным именем для IP-подключения.
`-verify_return_error` обязателен: без него `s_client` способен продолжить
соединение после ошибки. HTTP-запросы и токены эта команда не отправляет.
При зависании остановите Ctrl+C; соединение не должно ждать бесконечно.

В выводе сохраните `depth=...`, `verify error:num=10:...` и цепочку. Depth 0 —
leaf, 1 — его issuer; больший depth относится к следующему элементу цепочки.
Не путайте depth с кодом ошибки. Если OpenSSL отсутствует, сначала достаточно
`caddy-before.json` и результата имеющейся monitoring-check; **какой сертификат
реально отдаёт peer, до дополнительной проверки остаётся не подтверждено**.

Диагностические PEM-блоки — публичные сертификаты; не копируйте приватные ключи.
Подробности команды: [OpenSSL s_client](https://docs.openssl.org/3.0/man1/openssl-s_client/).

## 2. Один постоянный storage, корневой CA и журнал обновлений

Сначала сохраните предыдущие отчёты. Затем в существующий глобальный блок
`Caddyfile` добавьте или уточните настройки ниже; второй глобальный блок
создавать не надо. Пути должны указывать на уже используемое хранилище.
Маршруты notifications/commands и адрес сайта оставьте действующими.

```caddyfile
{
    storage file_system C:/Work/RemoteWatch/caddy-data
    log default {
        output file C:/Work/RemoteWatch/logs/caddy-runtime.jsonl {
            roll_size 10MiB
            roll_keep 10
            roll_keep_for 336h
        }
        format json
        level INFO
        exclude http.log.access
    }
}
```

Это **runtime log** для TLS/PKI и запуска; `log` внутри блока сайта задаёт
другой, access log. Не включайте DEBUG и вывод среды без необходимости.
Рабочие логи могут содержать адреса и пути: храните их рядом с gateway с
ограниченными правами и присылайте только относящийся к ошибке фрагмент.
Настройки описаны в [глобальных опциях Caddy](https://caddyserver.com/docs/caddyfile/options).

Если действующее хранилище находится в другом месте, сначала используйте
его **абсолютный** путь. Перенос можно сделать отдельно при остановленном
Caddy, сохранив весь storage и права; запуск с пустым каталогом создаст другой CA.
Не удаляйте `pki`, не очищайте сертификаты и не меняйте CA как способ ремонта.

На RU и LV сравните SHA-256 соответствующих файлов `root.crt`:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath C:\Work\RemoteWatch\secrets\caddy-root.crt
# На LV — реальный root CA из storage, а не leaf-файл сайта:
Get-FileHash -Algorithm SHA256 -LiteralPath C:\Work\RemoteWatch\caddy-data\pki\authorities\local\root.crt
```

Это сравнение файлов предполагает точную копию PEM. При ином форматировании
PEM сравните SHA-256 DER-сертификата; разные файлы сами по себе ещё не доказывают
разные CA. Клиентский `ca_file` должен доверять root, а не копии intermediate
или leaf. `root.key` и `intermediate.key` остаются только на LV.
Стабильный root позволяет обновлять leaf/intermediate без замены файла на RU.
Root тоже имеет конечный срок: для его истечения/компрометации нужен отдельный
план смены доверия, а не обещание бессрочного автоматического обновления.

Создайте каталог журналов, проверьте конфигурацию и примените её:

```powershell
New-Item -ItemType Directory -Force C:\Work\RemoteWatch\logs | Out-Null
C:\Work\RemoteWatch\caddy.exe validate --config C:\Work\RemoteWatch\Caddyfile --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw 'Caddy validation failed' }
C:\Work\RemoteWatch\caddy.exe reload --config C:\Work\RemoteWatch\Caddyfile --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw 'Caddy reload failed' }
```

Validate/reload могут повлиять на состояние сертификатов; время этой границы
зафиксируйте. Reload требует уже работающего Caddy и доступа к его локальному
admin endpoint. Если он отключён или перенастроен, используйте его действующую
настройку; не открывайте admin port в сеть.

В журнале ищите `tls.renew`, `tls.obtain`, `pki.ca`, `renewed intermediate`,
`permission denied`, `Access is denied`, `locking`, `clamping lifetime`,
`PANIC` и `ERROR`. Возможные выводы:

| Наблюдение | Что проверять дальше |
| --- | --- |
| Leaf истёк, intermediate/root действуют | Фоновое продление leaf, ошибки записи/lock, приостановки процесса/VM |
| Intermediate истёк | PKI maintenance, доступ к root key, явная ручная настройка intermediate |
| На диске сертификат новый, peer отдаёт старый | PID/listener, другой storage/процесс, сертификат в памяти, TLS proxy перед Caddy |
| CA клиента не совпадает с действующим root | Смена пользователя/storage или доверие intermediate вместо root |
| Время VM выходит за срок сертификата | Часы ОС и их скачки; допуски команд/relay здесь не помогают |

Отсутствие записи об ошибке само по себе не подтверждает успешное продление.
Нужны новый сертификат на peer и успешная проверка с прежним root CA.

## 3. Постоянный запуск Windows-службой

Официально Caddy поддерживает Windows service через `sc.exe` либо WinSW.
Ниже вариант встроенной службы без дополнительного wrapper. Нужны права
администратора **внутри VM**; доступ к физическому серверу не нужен.
Если эти права запрещены, установка службы потребует IT. Ручное окно PowerShell
или задача «только при входе пользователя» не обеспечивают запуск до входа после reboot.
[Официальная инструкция Caddy](https://caddyserver.com/docs/running#windows-service).

До переключения убедитесь, что отчёт сохранён, storage задан абсолютным путём,
root совпадает и нет уже установленной службы Caddy. Создайте **новую** службу
в остановленном состоянии из PowerShell администратора:

```powershell
$caddyService = 'RemoteWatchCaddy'
if (Get-Service -Name $caddyService -ErrorAction SilentlyContinue) {
    throw 'Service already exists: inspect its settings instead of recreating it'
}
$caddyCommand = '"C:\Work\RemoteWatch\caddy.exe" run --config "C:\Work\RemoteWatch\Caddyfile" --adapter caddyfile'
New-Service -Name $caddyService -DisplayName 'Remote Watch Caddy' `
    -BinaryPathName $caddyCommand -StartupType Manual | Out-Null
sc.exe config $caddyService obj= 'NT AUTHORITY\LocalService'
if ($LASTEXITCODE -ne 0) { throw 'Service account configuration failed' }
```

Для этой схемы выбрана встроенная LocalService; ей нужен read/execute к
`caddy.exe`, чтение `Caddyfile` и **modify с наследованием** к storage и logs.
Выдавайте права только этой учётной записи, не `Everyone`, и не давайте ей
изменять бинарный файл/конфигурацию. Пример для существующих каталогов:

```powershell
icacls.exe C:\Work\RemoteWatch\caddy-data /grant '*S-1-5-19:(OI)(CI)M'
if ($LASTEXITCODE -ne 0) { throw 'Storage ACL update failed' }
icacls.exe C:\Work\RemoteWatch\logs /grant '*S-1-5-19:(OI)(CI)M'
if ($LASTEXITCODE -ne 0) { throw 'Log ACL update failed' }
icacls.exe C:\Work\RemoteWatch\caddy.exe /grant '*S-1-5-19:RX'
if ($LASTEXITCODE -ne 0) { throw 'Executable ACL update failed' }
icacls.exe C:\Work\RemoteWatch\Caddyfile /grant '*S-1-5-19:R'
if ($LASTEXITCODE -ne 0) { throw 'Config ACL update failed' }
sc.exe failure $caddyService reset= 86400 actions= restart/5000/restart/15000/restart/60000
if ($LASTEXITCODE -ne 0) { throw 'Service recovery configuration failed' }
Set-Service -Name $caddyService -StartupType Automatic
```

Проверьте наследование у уже существующих файлов `pki`, `certificates`, `locks`:
запреты и отключённое наследование способны перекрыть права родителя. Нужна
возможность создавать, переименовывать и удалять файлы/locks, не только читать CA.
Проверка записи от имени интерактивного пользователя не доказывает права службы.
Не применяйте без разбора `/reset` или снятие всех существующих ACL.

Теперь остановите **именно прежний ручной Caddy** через Ctrl+C в его окне,
дождитесь освобождения 8443 и запустите службу:

```powershell
Start-Service RemoteWatchCaddy
Get-Service RemoteWatchCaddy
Get-CimInstance Win32_Service -Filter "Name='RemoteWatchCaddy'" |
    Select-Object Name, State, StartMode, StartName, ProcessId
Get-NetTCPConnection -State Listen -LocalPort 8443 |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

Должен остаться один владелец TLS listener. Проверьте runtime log и RU monitoring-check
с прежним `ca_file`. Не меняйте одновременно Caddy version, root и клиентский
конфиг: иначе результат не объяснит исходный сбой. Если служба не стартует,
проверьте Event Viewer → System → Service Control Manager и журнал Caddy.

Это автозапуск **Caddy**, а не двух Python-gateway. Полная готовность commands
и notifications после reboot требует их независимого автозапуска или будущего
supervisor этапа 0.4.2. Успешный TLS при остановленном Python-backend не доказывает
работу remote-watch; возможен HTTP 502. См. [план серверного приложения](GATEWAY_OPERATIONS_PLAN.md).

## 4. Приёмка: реальное продление и reboot

После восстановления сохраняйте с RU результат существующей monitoring-check
SpamBot каждые 1–5 минут, каждый раз с **новым TLS-соединением**, без credentials
и регистрации сессии. Пул старых соединений может скрыть просроченный сертификат.
Записывайте UTC, TLS success/error, verify_code, serial/SHA-256 и notAfter
проверенного leaf. При ошибке снова снимайте peer chain через OpenSSL.

Критерии продления:

1. В исходном отчёте известны срок leaf и SHA-256 root.
2. Caddy работает с неизменным PID, без ручного reload/restart в интервале проверки.
3. До истечения старого leaf новый handshake показывает новый serial/SHA-256
   и более поздний notAfter; root клиента не менялся, отказов проверки не было.
4. Проверки продолжаются за старый notAfter и через ещё одно продление.
   Фиксированного «12 часов прошло» недостаточно без факта смены сертификата.
5. Для проверки intermediate наблюдение продолжается через его обновление.
   Два обновления leaf не доказывают исправность недельного цикла intermediate.

Затем в согласованное владельцем время перезагрузите **VM**, не физический сервер.
Планируйте остановку остальных приложений отдельно. До нового RDP-входа проверьте
с RU HTTPS, затем notification smoke и безопасную команду status. После входа
сохраните время boot, PID/аккаунт службы и root fingerprint. Штатное окно простоя
при reboot ожидаемо; критерий — автоматическое восстановление без ручного запуска
Caddy, замены CA и обхода TLS. Позже проверьте ещё одно продление после reboot.

При повторном истечении сохраните отчёты **до** перезапуска. Service recovery
перезапускает упавший процесс, но не замечает работающий процесс с просроченным
сертификатом. Периодический restart и увеличение lifetime не считаются исправлением.

Для `tls internal` default leaf lifetime — 12h, intermediate — 7d, root — 3600d.
Это ориентиры, фактические сроки берутся из цепочки. Не увеличиваем lifetime
и не применяем `sign_with_root`: это не устраняет причину сбоя.
[Параметры internal issuer](https://caddyserver.com/docs/caddyfile/directives/tls#internal).

## Диагностика remote-watch 0.4.1.dev10

Командный HTTP сохраняет `CommandError.code="unavailable"` для совместимости,
но дополняет локальную ошибку полями `error_kind`, `verify_code`, `tls_reason`.
При просроченной цепочке строка ошибки выглядит так:

```text
unavailable; error_kind=tls_certificate; verify_code=10; tls_reason=certificate_expired
```

Эти поля есть в верхней ошибке `watcher.start()`, в причине запуска и в текущем
StageHealth соответствующего этапа команды. Успешное восстановление этого этапа
сбрасывает текущие TLS-поля, история/счётчики сохраняются. Они не называют
просроченный элемент цепочки; для этого нужны peer chain/depth.

Notification HTTP/relay сохраняет такие же локальные поля в `DeliveryResult`.
Прежний `reason_code=tls_certificate` сохранён. Новый `error_kind=tls_handshake`
отделяет прочие SSL-ошибки от проверки цепочки. Неизвестные числовые verify-коды
не интерпретируются по тексту исключения. URL, имена хостов, verify_message,
response body и credentials не входят в безопасную классификацию.
Wire-схемы не меняются: ошибки TLS соединения gateway → provider по-прежнему
передаются через существующий relay reason_code без новых локальных полей.
TLS-поля не обещаны в старых текстовых отчётах smoke/агрегатах уведомлений.

Локальные тесты библиотеки не подтверждают исправность Caddy на LV. Результаты
этого набора и границы проверки записываются в [VALIDATION.md](VALIDATION.md).
