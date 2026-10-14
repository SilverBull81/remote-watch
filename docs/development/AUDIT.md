# Аудит подготовки первого релиза

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-184253

Проверяются границы конфигурации, владения ресурсами, доставки/повторов, авторизации,
устойчивости команд и состава поставки. Автотесты и статический просмотр дополняют
друг друга; аудит не обещает отсутствие всех возможных ошибок.

## Исправления

| Находка | Изменение и проверка |
| --- | --- |
| Git ignore не защищает sdist от docs/private | Явный prune в MANIFEST и проверка содержимого архивов |
| Foreground manager менял ACL log_dir без проверки чужих файлов | Отказ log_directory_shared до lock/ACL/process startup; тест неизменности данных/прав |
| Возраст/счётчики journal cache считались переходом состояния | Стабильная log projection: health/capacity остаются, возраст/числа доступны в status/doctor; regression на отсутствие flood |
| Command client example использовал внутренний HTTPS-порт | Внешний generic endpoint 8443, совпадающий с примерами reverse proxy |
| Публичные инструкции содержали реальную deployment-историю | Новые user guides, компактные development docs, ignored private runbook/archive |
| README смешивал API, историю этапов и неподтверждённые заявления | Короткий клиентский старт, optional gateway, явные ограничения и статус лицензии |

## Просмотренные контракты

- Logging не меняет чужие handlers/levels, constructors не запускают сеть.
- Notification capacity/TTL/retry/shutdown и health отделены от истории.
- Tokens приватны; TLS/имя проверяются; HTTP ошибки не раскрывают provider payloads.
- Actor/chat/target/scopes и сессия ограничивают команды; callback не повторяется.
- UNKNOWN/receipts/reconnect и отдельная offline resolution сохраняют durable evidence.
- StoreWorker сериализует операции и не освобождает активную операцию по timeout.
- Manager использует собственные handles, ограниченные рестарты и startup rollback.
- Deploy не перезаписывает конфиги и не трогает SQLite/CA; service/tray разделены.

Ограничения и условия находятся в [архитектурном контракте](ARCHITECTURE.md).
Шаблоны и CLI проверяются дополнительно реальными сборками/тестами; совпадение
AST само по себе не доказывает правильность сети или установленной службы.

## Что не объявляется завершённым

Не выполнены юридическая проверка всех dependency/asset notices, публичный релиз,
выбор stable version/tag, полевая private ntfy-команда и Linux systemd acceptance.
Не проводятся pentest, переписывание Git-истории, HA/нагрузочная сертификация,
автоматическое разрешение UNKNOWN или отключение TLS ради доступности.
Все дальнейшие задачи сведены в [BACKLOG.md](../../BACKLOG.md).
