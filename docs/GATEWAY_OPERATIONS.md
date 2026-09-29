# Запуск, остановка и наблюдение gateway

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260929-202056

## Локальная диагностика, dev7

При штатной остановке, отмене и ошибке запуска CLI печатает одну строку JSON
`kind=gateway_summary` со stats(): совокупные попытки, отказы и текущую занятость.
После нормальной очистки active и destinations_busy равны нулю. Часть счётчиков
отсутствует, пока соответствующих событий не было. Имена приложений, alias, адреса,
токены и тексты уведомлений в эту сводку не входят. Сводка не подтверждает показ
на телефоне и не сохраняется при принудительном убийстве процесса/отключении VM.
Периодический вывод и публичный admin endpoint не добавлены.

Ошибки CLI содержат только code и фиксированное field:

| code | field | Что проверить |
| --- | --- | --- |
| cli_arguments | arguments | Синтаксис команды, см. --help |
| config_read | config | Наличие файла и права чтения |
| config_size | config | Размер JSON, не более 1 MiB |
| config_json | config | UTF-8, синтаксис, повторы ключей, NaN/Infinity |
| config_fields | config | Обязательные и допустимые ключи корня |
| config_schema | schema_version | Целочисленная версия 1 |
| config_value | destinations / principals / gateway | Значения выбранного раздела и связи между ними |
| dependency_missing | configuration / startup | Установленные extras или модуль доверенной фабрики |
| operation_failed | tls | Пара --cert/--key, права, формат и соответствие ключа |
| startup_failed | credentials | Наличие корректных и различных токенов приложений |
| startup_failed | providers | Токены провайдеров, их extras, открытие адаптеров |
| startup_failed | listener | Адрес, занятость порта, права привязки |
| operation_failed | configuration / startup | Фабрика, адрес/порт, TLS для внешнего адреса, stop-file или выполнение |

Неизвестные имена ключей и значения из JSON не печатаются. field — безопасный
раздел схемы, а не обязательно точное отдельное поле. Ошибки взаимных ссылок
и повторов имён относятся к итоговой проверке gateway. Исходные исключения скрыты.

## Windows: сначала ручной запуск

Используйте отдельный каталог, например C:\RemoteWatch, и ту же учётную запись,
под которой затем будет работать задача. Python, venv, конфигурация и сертификаты
должны задаваться абсолютными путями. Имена переменных токенов берутся из config;
значения задаются отдельно по [GATEWAY_SERVER.md](GATEWAY_SERVER.md).

```powershell
C:\RemoteWatch\.venv\Scripts\python.exe -m remote_watch.gateway --config C:\RemoteWatch\gateway_config.json --check-config
C:\RemoteWatch\.venv\Scripts\python.exe -u -m remote_watch.gateway --config C:\RemoteWatch\gateway_config.json --host 127.0.0.1 --port 8765 --stop-file C:\RemoteWatch\control\gateway.stop
```

Это HTTP только для прокси на той же VM. Для подключения с другой машины нужны
--host с адресом интерфейса и --cert/--key; см. [GATEWAY_TLS.md](GATEWAY_TLS.md).
Папку control создайте заранее и дайте право записи только владельцу процесса
и администраторам: создание stop-file равносильно локальному запросу остановки.
На старте этот файл должен отсутствовать. Gateway его не удаляет и не читает содержимое.

В другом PowerShell под той же учётной записью:

```powershell
New-Item -ItemType File -Path C:\RemoteWatch\control\gateway.stop
```

Сервер замечает файл примерно за секунду, прекращает приём и выполняет ограниченную
очистку по shutdown_timeout. Дождитесь завершения процесса и gateway_summary.
Перед следующим запуском удалите только этот файл:

```powershell
Remove-Item -LiteralPath C:\RemoteWatch\control\gateway.stop
```

В терминале работает и Ctrl+C; на Unix — SIGTERM. Stop-Process и кнопка «Завершить»
планировщика не являются штатной остановкой: они могут оборвать активную отправку.

## Windows: автозапуск без доступа к физическому серверу

После успешной ручной проверки настройте задачу в «Планировщике заданий» внутри VM.
Установка службы и фактическое изменение планировщика этой итерацией не выполняются.

1. Выберите отдельную учётную запись с чтением конфигурации/сертификата/секретов
   и записью в control/logs. «Выполнять вне зависимости от регистрации пользователя»
   требует соответствующих прав; при их отсутствии нужен администратор VM.
2. Триггер — при запуске компьютера с задержкой 30 секунд. Дополнительный триггер
   при входе пользователя нужен только для сознательно выбранного интерактивного режима.
3. Действие — PowerShell с `-NoProfile -NonInteractive -WindowStyle Hidden -File`
   и абсолютным путём локального launcher.ps1. Рабочий каталог — C:\RemoteWatch.
4. В параметрах запретите параллельные экземпляры («Не запускать новый экземпляр»),
   отключите ограничение длительности задачи, разрешите конечное число перезапусков
   при ошибке, например три с интервалом в минуту. После успеха задача не перезапускается.
5. Для планового обслуживания создайте stop-file, дождитесь окончания задачи,
   внесите изменения, удалите stop-file и запустите задачу вручную.

Пример launcher.ps1 для случая прокси на той же VM:

```powershell
$ErrorActionPreference = 'Stop'
$names = @()
$gatewayExit = 1
try {
    $secretValues = Get-Content -LiteralPath 'C:\RemoteWatch\gateway_secrets.local.json' -Raw | ConvertFrom-Json
    foreach ($property in $secretValues.PSObject.Properties) {
        if ($property.Name -notmatch '^(RW|REMOTE_WATCH)_[A-Z0-9_]+$') { throw 'Invalid secret variable name' }
        [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, 'Process')
        $names += $property.Name
    }
    $logPath = 'C:\RemoteWatch\logs\gateway-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.log'
    & 'C:\RemoteWatch\.venv\Scripts\python.exe' -u -m remote_watch.gateway --config 'C:\RemoteWatch\gateway_config.json' --stop-file 'C:\RemoteWatch\control\gateway.stop' *>> $logPath
    $gatewayExit = $LASTEXITCODE
} catch {
    Write-Output 'Gateway launcher failed; check local configuration and permissions.'
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
}
exit $gatewayExit
```

Создайте logs заранее. В gateway_secrets.local.json хранится объект соответствий
«имя переменной из config → токен»; реальные значения не помещайте в аргументы задачи,
репозиторий или отчёт. Ограничьте ACL файла и каталогов; launcher допускает лишь
префиксы RW_/REMOTE_WATCH_, поэтому согласуйте их с token_env. Не выводите объект
secretValues и не включайте PowerShell transcript для этого launcher.
Это пример для адаптации администратором, не автоматически установленная служба.
Проверьте ручной запуск задачи, остановку файлом, повторный запуск и запуск после
перезагрузки VM. Удаление старых логов настройте отдельно по своей политике хранения.

## Пример ограничений внешнего входа

Минимальный Caddyfile в TLS-инструкции показывает маршрут, но не полную защиту
публичного входа. Для корпоративного Linux-прокси подготовлен
[gateway-nginx.conf](examples/gateway-nginx.conf). Он задаёт конечное число соединений,
ограничения частоты/одновременных запросов, размеры тела/заголовков, таймауты,
единственный POST-маршрут и запрещает автоматический retry прокси.

Пример рассчитан на nginx и gateway на одной машине. Если прокси вынесен отдельно,
backend тоже должен быть HTTPS: замените proxy_pass на `https://gateway.internal:8765`,
включите proxy_ssl_verify, proxy_ssl_server_name и задайте доверенный CA через
proxy_ssl_trusted_certificate. Имя backend должно соответствовать сертификату.
Нельзя передавать bearer по HTTP между VM. Не копируйте Linux-пример в Windows
без адаптации сетевым администратором. Значения ограничений — стартовые для небольшой
установки; проверьте их под своей нагрузкой. Конфигурация прокси подготовлена,
но настоящий nginx и внешняя сеть в этой итерации не запускались.

Перед размещением проверьте `nginx -t`, единственный разрешённый маршрут, 413 на
тело больше 64 KiB, 429 при превышении квоты, отсутствие повторного POST и отсутствие
секретов в журналах. Эти проверки дополняют gateway-тесты, а не заменяются ими.

Источники: [nginx limit_req](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html),
[nginx limit_conn](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html),
[nginx core HTTP](https://nginx.org/en/docs/http/ngx_http_core_module.html),
[nginx proxy](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).
