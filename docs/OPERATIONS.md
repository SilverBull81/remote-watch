# Диагностика и эксплуатация

Version 1.0.1

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261015-111157

## Что означает готовность

Проверяйте четыре разных уровня: процесс запущен, компонент готов, HTTPS доступен
с нужной клиентской машины, приложение восстановило свою сессию. SCM running
или зелёный server doctor не подтверждают весь путь до телефона.

`watcher.runtime.health()` пассивно возвращает runtime_state, общий delivery_state
и destinations. Для каждого назначения: delivery_state, current_error, last_error,
время последней попытки/успеха/отказа, consecutive_failures и recoveries.
Состояния доставки: unobserved/healthy/degraded. Успех одного назначения не снимает
отказ другого. Следующее принятие провайдером очищает текущую ошибку данного
назначения, но сохраняет историю. Close также не стирает наблюдения.

`runtime.stats()` — история: старое failed_attempts не должно держать мониторинг
вечно degraded. Локальные drops/overflow — отдельные потери, а не доказательство
недоступности сети. Health не делает probes и не доказывает показ на телефоне.
Времена health монотонные и локальны процессу, не календарные timestamps.

В `watcher.command_stats.health` проверяйте ready, session_valid, connection_state,
has_pending_results и отдельные poll/heartbeat/storage/result. `last_error` хранит
историю; `current_error` этапа — текущий отказ. Успешный callback не может скрыть
поздний отказ heartbeat или потерю квитанции результата.
`operator_action_required` и recovery_hint видны и после закрытия клиента.

## Doctor

Offline — default; `--online` разрешает ограниченные сетевые проверки.
Doctor никогда не выполняет callback, не регистрирует сессию, не продлевает lease
и не использует send/poll/heartbeat как ping.

```console
python -m remote_watch.diagnostics.gateway_doctor --mode server --config gateway_server.local.json --online --report doctor-server.local.json
python -m remote_watch.diagnostics.gateway_doctor --mode client --config gateway_doctor.client.local.json --online --report doctor-client.local.json
```

Server проверяет настройки/модули, читает частный status manager и проверяет TCP
внутренних listeners. При external Caddy он не проверяет внешний HTTPS и CA.
Client на соответствующей машине проверяет DNS → TCP → TLS → маршрут → собственные
права → готовность; для команд также trusted time и наличие сессии своей Identity.
Используется отдельный authenticated probe. Identity/aliases/scopes должны совпадать
с principal, actor/callback не выбирается. [Клиентский пример](examples/gateway_doctor.client.example.json).

При session_present routing зарегистрирован, но это не доказательство выполнения
конкретного callback. При отсутствии сессии возможен warning, если приложение ещё
не стартовало. Проверьте его локальный health и сделайте разрешённую status-команду.
Отчёт содержит безопасные фиксированные коды без provider payloads и секретов.

## Причины отказов источников команд

Начиная с 1.0.2, статус gateway server, его журнал переходов и серверный doctor
содержат `components.commands.source_health`. В отчёте doctor это отдельные строки
`commands.source_1`, `commands.source_2` и далее. Номер `index` соответствует порядку
элементов `sources` в командном JSON; `provider` различает Telegram и ntfy.
Имена чатов, source_id, токены, URL и тексты исключений не выводятся.

`receive` описывает текущий отказ цикла приёма, `reply` — доставки сохранённых
результатов. Успех одного цикла не скрывает отказ другого. `last_failure` хранит
последний исторический отказ после восстановления; `failures` и `recoveries` —
счётчики неудачных операций и восстановлений циклов в текущем процессе.
Текущую готовность показывает `ready`, а не наличие исторической ошибки.

| Поле отказа | Смысл |
| --- | --- |
| stage | provider_poll — чтение команд; provider_reply — ответ; provider_ack — подтверждение события; storage — локальный журнал; hub — обмен состоянием команд |
| code | Прежний общий код, например unavailable, busy или denied |
| error_kind | DNS, timeout, connection, network либо безопасная TLS-категория; null, если причина не уточнена |
| http_status | Полученный HTTP-статус; 429 также сохраняется во время паузы, которую запросил провайдер |
| busy_reason | Категория локальной занятости, если известна; не равнозначна HTTP 429 |
| verify_code / tls_reason | Числовой код и фиксированная причина проверки TLS без текста сертификата |

Журнал записывает смену причины и восстановление, а не каждую повторную попытку.
Полные счётчики доступны через status/doctor. Короткий сбой между двумя heartbeat
может остаться только как `last_failure`; по нему нельзя восстановить точное время
или доказать доставку команды. Внешняя диагностика не сбрасывает журналы и курсоры.

Служебные сообщения владельцу написаны на английском: что перестало работать,
какого источника касается отказ и продолжаются ли автоматические попытки.
При восстановлении указывается наблюдённая длительность по монотонным часам manager.
Существующий `application.alerts.cooldown` ограничивает повторы и объединяет быстрые
переходы; длительность фиксируется при восстановлении, а не при отложенной отправке.
Код остаётся отдельной строкой `Diagnostic`. Режим full/compact/text назначения
по-прежнему управляет технической шапкой. Менять JSON для этой доработки не требуется.

## Сводки SQLite

Hub/client/source владельцы кэшируют числовые сводки после обычных операций,
не чаще одного раза за пять секунд. Status/health читают кэш, не открывают вторую
SQLite и не создают probe-запись. Ошибка наблюдения не меняет исход основной операции.

| Поле | Смысл |
| --- | --- |
| records / records_limit | Занятые места и предел записей |
| pending | Незавершённые запросы/неподтверждённые результаты по роли журнала |
| unknown | Исторические неопределённые исходы |
| unresolved_unknown | Неопределённые исходы без отдельного принятого разрешения |
| execution_active | Исполнение ещё удерживает слот |
| capacity | Оценка ёмкости; warning не означает необходимость restart |
| age_seconds | Возраст снимка; растёт после потери heartbeat |
| resolutions | Отдельные административные принятия исхода, не удалённые UNKNOWN |

Отсутствующие в файле limits остаются null. UNKNOWN=1 при unresolved_unknown=0
возможно после подтверждённой сверки, но агрегат не доказывает разрешение конкретного
request. Исторический UNKNOWN не удаляется. Status/doctor manager раскрывают только
числа hub/до восьми sources локальному оператору; клиентский probe не показывает
журналы других приложений. Изменение возраста/счётчиков не засоряет журнал переходов;
отказы наблюдения и изменения capacity в нём остаются.

Для **остановленной** отдельной копии без SQLite sidecars:

```console
python -m remote_watch.diagnostics.gateway_doctor --mode journal --journal /path/to/stopped/hub.sqlite
```

Режим immutable/ro не мигрирует и не чинит файл, не создаёт owner/lock и не трогает
generation/cursors/ACK. Если присутствует WAL/SHM/journal или владелец не остановлен,
не пытайтесь убрать sidecar вручную: сначала корректно завершите SQLite-владельца.

## Если reconnect блокируется результатом

Нельзя удалять клиентскую БД, ставить ACK вручную, менять owner или повторять callback.
При старом COMPLETED и серверном UNKNOWN требуется явная административная сверка.
Она допустима только для точного совпадения исходного request/ref/claim,
остановленного исполнения, клиентского COMPLETED и hub UNKNOWN.

1. Планово остановите приложение и command gateway; подтвердите отсутствие
   выполняющегося callback. Сохраните резервные копии обоих журналов.
2. На клиентской копии выполните list, затем export нужной sequence.
3. Закрытым способом передайте evidence оператору hub, выполните preview.
4. Проверьте весь исход и fingerprint; только после осознанного решения выполните apply.

```console
python -m remote_watch.diagnostics.command_resolution list --journal client.sqlite
python -m remote_watch.diagnostics.command_resolution export --journal client.sqlite --sequence 1 --output evidence.local.json
python -m remote_watch.diagnostics.command_resolution preview --journal hub.sqlite --evidence evidence.local.json
python -m remote_watch.diagnostics.command_resolution apply --journal hub.sqlite --evidence evidence.local.json --expect FINGERPRINT_FROM_PREVIEW --confirm-owners-stopped
```

Число sequence и fingerprint берутся из **вашего** вывода, не копируются из примера.
Evidence содержит данные команды, не публикуйте его. Apply не меняет оригинальный
UNKNOWN: записывает отдельное принятие, сохраняет аудит/owner/cursors и fencing старых
hub handles. Клиент получает точную authenticated квитанцию обычным протоколом.
Нет HTTP admin endpoint, автоматической adjudication или callback replay.
Схема SQLite 1 мигрирует в 2 при обычном открытии новым runtime; старый runtime схему 2
не читает. Откат версии после миграции требует отдельного плана, не удаления данных.

## Частые отказы

| Код/симптом | Действие |
| --- | --- |
| tcp_refused | Проверить внешний порт, Caddy running/listener/firewall |
| TLS verify_code | Проверить CA/имя/часы/renew; не отключать TLS |
| denied | Сверить token, Identity, aliases/targets/scopes; не расширять ACL вслепую |
| busy / HTTP 429 | Сверить busy_reason и этап; временное пересечение должно восстанавливаться |
| time_unavailable | Проверить time_reason и доступ к выбранному trusted source |
| stale_session | Смотреть heartbeat/reconnect, дать время TTL/backoff |
| unresolved_results / outcome_conflict | Сохранить журналы и выполнить осознанный разбор |
| ownership | Найти действующего владельца по актуальному состоянию, не удалить lock |
| log_directory_shared / service_shared_logs | Выделить manager собственную папку, сохранить чужие данные/ACL |

Gateway status содержит `alerts` и `alert_delivery`: attempts=0 означает отсутствие
попыток в текущем запуске; accepted — принятие провайдером, не телефон. failed/unknown
требуют проверки прямого доступа сервера к провайдеру. Падение всей VM требует
внешнего наблюдателя. Обычный doctor сам уведомлений владельцу не отправляет.

## Дополнительные проверки

`remote_watch.diagnostics.smoke`, `field_smoke`, `command_smoke`, `ntfy_diagnostic`
и `time_probe` поставляются вместе с библиотекой. Начинайте с `--help`: smoke/field
могут **реально отправлять** сообщения, time_probe делает запрос к выбранному UTC origin.
Для поля используйте отдельные bot/topic и synthetic приложения, не production reader.
Искусственный provider 429 не доказывает задержку показа на телефоне.
