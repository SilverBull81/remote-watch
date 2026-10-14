# Логотип, восстановление связи и термины Remote Watch

Version 1.0.6

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-113001

Статус на 13.10.2026: в **0.4.2.dev3** переименованы API/модули без старых алиасов
по прямому решению владельца. Логотип уточнён по большому образцу; добавлена ICO
в папку `logo`. В 0.4.2.dev4 реализован reconnect, удалена копия ICO из src.
Правила и эксплуатационная проверка: [COMMAND_RECONNECT.md](COMMAND_RECONNECT.md).
Основание — пожелания владельца после запуска недельного prod-пилота SpamBot.
Владелец сообщил, что недельный прогон идёт без вопросов к remote-watch;
итоги недели ещё не получены. Production deployment в этом проходе не менялся.

## Логотип и конфигурация

Иконка для копирования: [logo/remote_watch.ico](../logo/remote_watch.ico).
В src дубликата нет. Wheel устанавливает тот же ICO в `share/remote-watch/logo`
Python-окружения; установленный сервер не зависит от исходного checkout.
Исходное изображение: [remote_watch.png](../logo/remote_watch.png).
ICO содержит 16, 24, 32, 48, 64, 128 и 256 px, как SpamBot; PNG — 1254×1254 RGBA.
Файлы других приложений остаются пользовательскими образцами и не входят в пакет.
`application.icon_path` выбирает свою иконку; отсутствие/null — встроенную.
Настройка и правила пути: [GATEWAY_DEPLOYMENT.md](GATEWAY_DEPLOYMENT.md).

Изображение обновлено встроенным инструментом imagegen: `robot_quik.png` задаёт
образец лица и геометрии, прежний `remote_watch.png` — одобренный символ глаза
с двумя узлами. Фиолетовый фон эмблемы отличается от синего, голубого, зелёного,
красного и жёлтого других приложений. После генерации выполнено только преобразование
формата/размера в многоразмерный ICO с сохранением прозрачности. Запрос:

> Use robot_quik.png as the master robot geometry and sharply drawn face reference,
> and the previous Remote Watch icon only for the approved white eye connected to
> two circular network nodes. Preserve the grey wide rectangular face, clean round
> eyes, polygonal jaw outline, red buttons, torso and arms. Replace the cyan Q panel
> with saturated violet/purple #853BC2 and the approved white symbol. Crisp flat
> vector-like edges and uniform sharp outlines; no blur, grain, speckles or glow.
> Preserve the master robot proportions. No text, Q or scenery. Square composition
> filling the frame, transparent alpha background, high-resolution source for small
> Windows tray icons. Preserve the eye/network symbol geometry.

## Единый словарь

| Термин | Значение |
| --- | --- |
| Notifications, уведомления | События приложения, обычно от стандартного logger, отправляемые человеку |
| Commands, команды и ответы | Запрос человека приложению, проверка прав, выполнение callback и ответ на этот запрос |
| Direct, напрямую | Приложение само вызывает API мессенджера для уведомления |
| Gateway, через шлюз | Между приложением и мессенджером работает наш сервер |
| Notification gateway | Серверный компонент, пересылающий уведомления к провайдеру |
| Command gateway / hub | Компонент, принимающий команды, адресующий их приложениям и возвращающий ответы |
| Gateway server | Управляющее приложение: запускает компоненты, следит за ними, предоставляет status/stop и трей |
| Relay (в старых документах) | Прежнее название пересылки уведомлений через notification gateway; не третий контур |

Сейчас direct/gateway выбирается **для назначения уведомлений**. Путь команд
из Telegram/ntfy использует command gateway. Прямой приём команд в приложении
как готовый альтернативный режим не реализован. Возможность поднять gateway
на той же машине не превращает его в прямой адаптер команд.

```text
Notifications: приложение → адаптер → мессенджер
           или приложение → notification gateway → адаптер → мессенджер

Commands:      мессенджер → command gateway → приложение
Ответ:         приложение → command gateway → мессенджер
Help/отказ:                 command gateway → мессенджер
```

Ответы callback, справка и отказы отправляются командным контуром. Они не проходят
через logger/очередь уведомлений. Если сам callback пишет в logger, такая запись
может дополнительно стать обычным уведомлением согласно маршрутам приложения.
Служебные сообщения manager владельцу — отдельные прямые уведомления, не ответы
команд и не пересылка через неисправный дочерний gateway.

Два контура нужны из-за разных правил. Для уведомлений допустимы фильтрация,
переполнение очереди, TTL и возможные дубликаты доставки. Для команд нужны
ACL actor/chat/target/scopes, привязка к сессии, журнал до исполнения, сохранённый
результат и запрет повторного callback. Ответ должен вернуться в исходный чат,
даже если logger фильтрует INFO или notifications отключены. Компоненты могут
использовать один бот/чат; единственный читатель входящих сообщений остаётся
command source. Это логическое разделение, а не требование двух ботов/серверов.

## Выполненные переименования и обновление SpamBot

В пользовательских инструкциях используются «напрямую» и «через gateway».
Владелец отменил первоначальное предложение совместимых алиасов: библиотека
используется только в SpamBot, поэтому оставлять старый API не требуется.

| До 0.4.2.dev3 | Теперь, в 0.4.2.dev5 |
| --- | --- |
| `RelayConfig`, `RelayChannel` | `NotificationGatewayClientConfig`, `NotificationGatewayChannel` |
| `adapters/relay.py` | `adapters/notification_gateway.py` |
| `relay_protocol.py`, `RelayRequest` | `notifications/gateway_protocol.py`, `NotificationGatewayRequest` |
| `DeliveryMode.RELAY`, `ResultSource.RELAY` | `DeliveryMode.GATEWAY`, `ResultSource.GATEWAY` |
| Значение режима/provider/source `relay` | `gateway` |
| extra `relay` | extra `gateway` для клиента и сервера уведомлений |
| `smoke relay` | `smoke gateway` |
| Ключ `relay` в локальном файле smoke | `gateway` |
| Причины `relay_*`, `invalid_relay_response` | `gateway_*`, `invalid_gateway_response` |

Длительный `field_smoke mixed` сохраняет имя режима: это gateway + прямой ntfy.
В его файле настроек меняется только секция `relay` на `gateway`.

Обновите imports, сборку настроек назначения и сравнения режима/source/reason_code
в приложении. `token_env` по-прежнему принимает произвольное имя: переименовывать
рабочие переменные среды и менять сами секреты не требуется. В новых примерах
используется `REMOTE_WATCH_GATEWAY_TOKEN`. Старый extra отсутствует; pip может
выдать лишь предупреждение о неизвестном extra, поэтому явно выбирайте `[gateway]`.

На плановой остановке обновите библиотеку на notification gateway и клиенте,
затем запускайте приложение с новыми imports/настройками и проверьте smoke.
Работающий недельный прогон автоматически не обновляется. В этом проходе
SpamBot и конфиги LV не менялись. Командная часть и её сессии не переименованы;
перезапуск command hub требует новой клиентской сессии; с dev4 она восстанавливается
автоматически при безопасно завершённой прежней работе.

Адрес POST `/v1/notifications`, поля JSON и схемы 1/2/3 сохраняют назначение;
это не совместимые переходники старых Python-имён. Числа схем не меняются ради
переименования: diagnostic reason_code и ранее был ограниченной строкой.
Успешные envelopes остаются прежними; новые ошибки возвращают новые коды.
Нет повторного POST/fallback к старому маршруту, адаптеров-обёрток или re-export.
Журналы SQLite, cursor, source_id, токены, Identity и Caddy CA сохраняются.
Решение и границы: [ADR 0013](adr/0013-notification-gateway-names.md).

## Автоматическое восстановление командной связи

Реализовано в 0.4.2.dev4. Разделены поколение живого владельца журнала и ID
последующих сессий. Один координатор выполняет сверку результатов, backoff с jitter
и новый register. Dispatcher переживает потерю сессии и остановку удалённого hub.
Старый callback не повторяется; неизвестный результат сохраняется и может блокировать
восстановление. Есть публичные состояние, причина и счётчики reconnect.

Проверки охватывают idle restart/expiry, две Identity, настоящий TLS, потерю
register/ACK/grant, активное исполнение, ошибки хранения/прав/TLS, разницу lease
из-за RTT и shutdown. Инструкции и ограничения:
[COMMAND_RECONNECT.md](COMMAND_RECONNECT.md), [ADR 0014](adr/0014-command-reconnect.md).
Результаты конкретной локальной комплектации — в [VALIDATION.md](VALIDATION.md).
Полевой reconnect принят владельцем 14.10.2026; два восстановления видны в свежем логе SpamBot.

## Приёмка и дальнейшие направления

- Получить результаты недельного prod-пилота SpamBot; миграция первого приложения
  уже выполнена и принята владельцем, это больше не будущая задача.
- Deploy и работа нового manager приняты владельцем 14.10.2026. Будущая служба
  и её запуск после reboot ещё требуют отдельной проверки.
- Проверить существенные изменения процессов в Windows/Linux CI; последний
  локальный проход 0.4.2 на Windows не заменяет платформенную матрицу.
- Live команды ntfy оставлены в резерве по выбору владельца, без обязательной
  покупки. Успешные исходящие уведомления ntfy этим не отменяются.
- Следующая ранее выбранная интеграция — Matrix. До неё нужны решение по SDK,
  размещению и encrypted rooms; это не незавершённая часть 0.1–0.3.
- По отдельной потребности: durable notification outbox, batching/deduplication,
  quiet hours, fallback/escalation, вложения, email/webhook/Apprise, hot reload,
  группы/broadcast и HA. Не считать их обязательствами текущего этапа.
- Разбор UNKNOWN сейчас остаётся явной процедурой; удобный административный
  инструмент можно обсудить отдельно. Автоматическое повторное исполнение
  неизвестного результата не планируется.
- Для публичного распространения понадобится лицензия; сейчас использование
  частное. MAX и удалённое исполнение произвольного shell/Python вне плана.

Deploy с шаблонами и сохранением CA/SQLite/секретов реализован в 0.4.3.dev1:
[инструкция](GATEWAY_DEPLOY.md). Doctor реализован в 0.4.4.dev1.
Сводное наблюдение журналов реализовано в 0.4.4.dev5. Остаток 0.4: служба manager
с обязательным пользовательским треем и проверка работы службы
после reboot/сбоев: [REVIEW_0_4.md](REVIEW_0_4.md).
Работающая служба Caddy не заменяет службу нового gateway_server.
