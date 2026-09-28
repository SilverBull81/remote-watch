# Проверки и критерии приёмки

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260928-100331

## Статус

Подготовлена проектная документация. Пакет, тестовый набор и gateway отсутствуют.
Ни один live-provider тест с серверов пользователя не выполнен. Ниже — требования
к будущей проверке, а не список успешно пройденных тестов.

## Проверка документационной основы

Проверяются наличие относительных Markdown-ссылок, UTF-8 BOM/CRLF, заполнение
метаданных, согласованность границ 0.1/0.2/0.3 и отсутствие исходных Python-файлов.
Перед коммитом проверяется staged diff и git diff --check. Официальные provider
документы использованы для выбора канала; источники перечислены в CHANNELS.md.

## Матрица 0.1

| Область | Сценарии | Ожидаемый результат |
| --- | --- | --- |
| Logging | Logger/LoggerAdapter, hierarchy, filters, propagation, чужие handlers | Нет глобальной перенастройки и изменения исходной записи |
| Eligibility | notify true/false/absent, ERROR default, topic/tags/identity | Точные правила, без двойного destination ID |
| Snapshot | Изменяемые args, exception, неверные metadata, oversize | Неизменяемые ограниченные данные, без traceback references |
| Identity | Подмена instance/service через extra | Runtime identity сохраняется |
| Queue | Полный ingress и одно переполненное назначение | Drop без ожидания, другие назначения продолжаются |
| Scheduling | Retry storm, bridge wakeups, несколько producers | Общий outstanding budget не обходится callbacks/tasks |
| Delivery | Accepted, transient/permanent failure, malformed reply, unknown | Правильная классификация и безопасная диагностика |
| Retry | Attempts, jitter, retry-after, TTL | Конечные попытки, нет повторов после expiry |
| Isolation | Slow/failing channel рядом со здоровым | Здоровый обслуживается независимо |
| Recursion | Worker, adapter, HTTP dependency и diagnostic sink logs | Не возникают новые remote notifications |
| Secrets | Config repr, ошибки HTTP, redactor, tokens в URL | Собственные credentials не появляются в снимках/диагностике |
| Lifecycle | До start, duplicate start, stop до start, startup failure | Явные состояния, корректный rollback ресурсов |
| Shutdown | Full queue, retry wait, active send, concurrent producers | Закрытие приёма, общий deadline, discard/unknown accounting |
| Async | Логирование из приложения с asyncio, async shutdown | Не захватывается и не блокируется main loop доставкой |
| Packaging | Core без extras, adapters с extras, clean wheel install | Нет скрытой provider dependency |

Clock и random source подменяются в unit-тестах; retry-тесты не ждут реальные
минуты. Настоящие concurrency-тесты используют barriers/events и конечные deadlines,
а не хрупкие ожидания по короткому sleep. Проверки воспроизводимы и не требуют сети.

Contract suite для custom adapters проверяет lifecycle в одном loop, одну попытку
на send, cancellation, классификацию ошибок и отсутствие скрытого retry.
Синхронно блокирующий/игнорирующий отмену адаптер контракту не соответствует.
Тестируемый fake transport не должен зависеть от Telegram token или ntfy endpoint.

## Полевой сценарий Android

Отдельно от offline tests выполняется [проверка канала](CHANNELS.md).
Нужны все целевые server regions, Wi-Fi и мобильная сеть, заблокированный телефон,
простой, перезапуск приложения и восстановление сети. Фиксируются задержки,
пропуски, duplicates и настройки background delivery. Не считать HTTP success
подтверждением показа уведомления на телефоне.

Live tests запускаются только явно, отправляют синтетический текст в выделенное
назначение, получают credentials извне и не запускаются в обычном CI/pytest.
Порог приемлемой мобильной задержки уточняется по полевому сценарию до приёмки;
гарантия времени remote display не является свойством runtime библиотеки.

## Relay 0.2

Проверить direct и relay одновременно, aliases/ACL, подмену identity,
несовместимую wire schema, expiry, size limits, overload и закрытие сервиса.
Проверить timeout после успешной provider attempt: клиент учитывает unknown,
повторы ограничены и могут дать duplicate. На gateway нет второго retry loop.
Relay-only режим не должен открывать command endpoints.

## Commands 0.3

Проверить actor/conversation authorization, exact target, capabilities, heartbeat,
две конкурирующие регистрации, session replacement, expiry, replay, lease/ack,
рестарт hub/application, потерю ответа и корреляцию с исходным command ID.
При недоступном audit/replay storage приём закрывается. Display name не даёт права.
Read-only status не запускает shell. Unknown outcome не превращается в success.

Broadcast и изменяющие команды не принимаются в scope минимальной версии;
их partial failure/idempotency проверяются при отдельном расширении.
