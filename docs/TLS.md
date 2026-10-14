# HTTPS и Caddy

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-184253

## Общая схема

Клиент обращается к внешнему `https://gateway.example.test:8443`. Caddy завершает TLS
и передаёт HTTP на loopback 8765 (notifications) и 8766 (commands).
Эти внутренние порты не нужно открывать удалённым клиентам. Настройте входящий
firewall только для выбранного HTTPS-порта и доверенных сетей.

Доступ по RDP не доказывает сетевой доступ к произвольному HTTPS-порту.
Проверяйте с **клиентской машины**: `Test-NetConnection gateway.example.test -Port 8443`.
Проверка localhost на сервере подтверждает другой участок маршрута.

## Выбор сертификата

При доступном домене и подходящих условиях ACME можно использовать публично
доверенный сертификат. Настройте DNS/доступность challenge согласно инструкции Caddy.
При закрытой сети, VPN или отсутствии домена используйте `tls internal` и явно
доверенный корневой CA. Само по себе владение VM не даёт права менять сеть провайдера;
если VM не доступны друг другу по выбранному порту, потребуется настройка сети/VPN.

[Пример Caddyfile](examples/Caddyfile.example) и generated Caddyfile.example из
deploy показывают маршруты и ограничения. Замените host, порт и абсолютные пути.
Для Windows путь storage можно писать с `/`, например `C:/Apps/RemoteWatch/caddy-data`.
Путь журнала выделите отдельно: `C:/Apps/RemoteWatch/logs/caddy/caddy-runtime.jsonl`.

## Internal CA и клиенты

Задайте явный `storage file_system` и сохраните его между обновлениями. При смене
аккаунта службы обеспечьте доступ к прежнему storage, не создавайте пустой каталог.
Leaf/intermediate сертификаты обновляются; доверять клиент должен корневому CA.
Справка: [Caddy Local HTTPS](https://caddyserver.com/docs/automatic-https#local-https).

Копируйте на клиента только `pki/authorities/local/root.crt` из выбранного storage.
Файлы `.key` и серверные leaf `.crt` клиентам не нужны. Перенос root.crt в папку
secrets на клиенте допустим; это публичный сертификат, но его подлинность проверьте
доверенным каналом/сверкой fingerprint.

```python
import ssl

tls = ssl.create_default_context(cafile="secrets/root-ca.crt")
assert tls.check_hostname
assert tls.verify_mode == ssl.CERT_REQUIRED
```

Передайте context в NotificationGatewayClientConfig. Command client/doctor
используют `ca_file`. Имя/IP endpoint должно соответствовать сертификату;
не подставляйте другое имя только ради маршрутизации. Часы ОС также важны для TLS.
Настройки clock skew уведомлений не отменяют проверку сертификата.

## Запуск и обновление

```console
caddy validate --config Caddyfile --adapter caddyfile
caddy run --config Caddyfile --adapter caddyfile
```

validate не запускает listener и не доказывает правильность сетевого доступа.
Он может provision ресурсов; используйте на выбранной установке осознанно.
`caddy reload` обращается к **уже запущенному** admin API. Если Caddy не работает,
reload закончится отказом соединения с 2019; сначала нужен run/старт службы.

Для 24/7 запускайте Caddy под службой/systemd. Текстовое выделение в интерактивной
Windows-консоли может остановить работу процесса; служба устраняет зависимость от окна.
Официальные варианты: [Keep Caddy Running](https://caddyserver.com/docs/running).
Не запускайте второй foreground Caddy поверх работающей службы.

У аккаунта Caddy должны быть RX на бинарный файл, чтение Caddyfile, Modify на
своё storage и **свою папку журналов**. Доступ к существующему файлу лога не означает
право создать новый при reboot/rotation. Не используйте logs/gateway_server:
manager назначает ей приватный ACL. Отдельный каталог logs/caddy предотвращает конфликт.

При изменении Caddyfile используйте reload запущенного экземпляра; при миграции
аккаунта/службы сохраните CA и заранее обеспечьте права. Проверка HTTPS должна
оставаться включённой, без `ssl=False` и `CERT_NONE`.

## Проверка и типовые ошибки

| Симптом | Проверка |
| --- | --- |
| tcp_refused | Caddy running, правильный внешний порт, listener и firewall |
| tls_certificate / code 10 | Сроки элементов цепочки, часы ОС и журнал renew; код не показывает failing depth |
| unable to get local issuer | Правильный root CA в ca_file, не leaf/intermediate |
| hostname mismatch | Имя/IP endpoint и SAN сертификата |
| Caddy 1067, нет новых runtime logs | Права аккаунта на config/storage и создание/rotation log |
| Server doctor зелёный, client красный | Внутренние listeners готовы, внешний HTTPS ещё может быть недоступен |

Root CA при обычном renew не заменяют. Устойчивость проверяют следующим renew и
reboot службы, а не только успешным ручным стартом. Doctor не делает отправку:
после его успеха отдельно проверьте уведомление и разрешённую команду status.
