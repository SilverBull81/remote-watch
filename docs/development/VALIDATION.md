# Проверки и статус выпуска

Version 1.1.4

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261125-110146

## Проверенное расширение 1.4.0: резервирование доверенного UTC

Командный gateway по умолчанию использует UTCTime и TimeAPI; явный trusted_time.sources
заменяет defaults списком из 1–4 источников. Проверяются параллельные конечные запросы,
резерв при timeout/ошибке основного источника, конфликт пригодных показаний, предел
погрешности, сохранение исходного TTL/дрейфа и сокращение оставшегося срока команды.
Ошибки TLS и неизвестные/некорректные ответы при полном отказе обновления снимают доверие.
Отмена не оставляет фоновых запросов, истёкший anchor не используется. Часы ОС не служат
резервом. Паузы повторов 15/30/60 возвращаются к обычному периоду после успеха.

Добавлены offline-проверки UTC JSON, настроек, allowlisted диагностики, IPC и doctor:
отказ резерва при пригодном anchor даёт warning, потеря пригодного времени — failed.
Прежние wire/hash/SQLite/ACL и клиентские контракты не меняются.

Финальные чистые Windows CPython 3.12.2 wheel-from-sdist проверки **1.4.0**:

- Core: 1331 passed, 90 skipped, 2 deselected in 77.62s.
- Extras: 1782 passed, 2 deselected in 171.81s.
- Отчёты: .development/validation-140-final-core и .development/validation-140-final-extras.
- Ruff/style: 174 файла; документация: 21 документ/13 JSON, без ошибок.
- Проверены чистая установка, 98 runtime-модулей и 12 installed CLIs.
- Все 118 файлов runtime/resources побайтно совпали между core/extras wheel.
- В sdist есть ICO, нет PNG, private, credentials и .development.

Отдельная короткая живая проверка defaults из исходников: 3/3 показаний приняты от
UTCTime при timeout TimeAPI. Это не длительный полевой прогон, не проверка всех origin
и не доказательство доступности внешних API в других сетях.

GitHub Release/tag 1.4.0 и новая CI/Linux-матрица здесь не заявляются. Последняя полная
матрица 1.0.0 приведена ниже. Production служба, конфиги, SQLite и CA не изменялись.
Сводка обновлена после проверок; содержимое проверенных архивов и отчёты сохранены.
Предыдущие опубликованные файлы/теги и процитированные исторические отчёты сохранены.

## Первый стабильный выпуск 1.0.0

Владелец утвердил MIT, выпуск **1.0.0** и аннотированный тег **v1.0.0**.
[GitHub Release](https://github.com/SilverBull81/remote-watch/releases/tag/v1.0.0)
содержит wheel, sdist и SHA256SUMS.txt. Публикация на PyPI и единый EXE не выполняются.

## Полная CI-матрица

[GitHub Actions 38609427482](https://github.com/SilverBull81/remote-watch/actions/runs/38609427482),
проверенный code commit `5ebf78c9378eaa8e8a6589b2e63fc31910357001`, пакет **1.0.0**.
Все восемь jobs успешны. Каждый выполняет Ruff/style/docs checks, сборку sdist/wheel,
чистую установку wheel из sdist, installed CLI и полные offline tests.

| Платформа | CPython | Режим | Результат |
| --- | --- | --- | --- |
| Linux | 3.10.22 | core | 1180 passed, 89 skipped, 2 deselected in 28.68s |
| Linux | 3.10.22 | extras | 1613 passed, 16 skipped, 2 deselected in 74.97s (0:01:14) |
| Linux | 3.12.15 | core | 1180 passed, 89 skipped, 2 deselected in 25.15s |
| Linux | 3.12.15 | extras | 1613 passed, 16 skipped, 2 deselected in 75.72s (0:01:15) |
| Windows | 3.10.11 | core | 1194 passed, 75 skipped, 2 deselected in 122.56s (0:02:02) |
| Windows | 3.10.11 | extras | 1629 passed, 2 deselected in 165.84s (0:02:45) |
| Windows | 3.12.10 | core | 1194 passed, 75 skipped, 2 deselected in 100.40s (0:01:40) |
| Windows | 3.12.10 | extras | 1629 passed, 2 deselected in 178.54s (0:02:58) |

Два opt-in live-provider теста исключены; платформенные/optional skips видны в таблице.
Настоящие службы Windows и уведомления на телефон CI не запускает.
Release tag включает последующие правки только итоговых документов; Python-код,
метаданные пакета и поставляемые шаблоны совпадают с проверенным code commit.

## Состав поставки

При подготовке пакетов проверяются License-Expression=MIT и точный LICENSE,
ICO без PNG, docs/BACKLOG.md и отсутствие private/runtime/credentials файлов.
Локальная `.development/` исключена из Git и sdist явно; каталог docs/private
также pruned отдельно. Наличие локальных артефактов не должно менять состав wheel.
Опубликованные архивы и теги не заменяются при обслуживании рабочего дерева.

## Полевая приёмка и границы

Владелец подтвердил работу уведомлений/команд, reconnect, deploy/doctor,
Windows-службы/tray/install/remove/reinstall, reboot и crash-тестов, доставку
служебных оповещений. Это свидетельство из переписки, а не независимый отчёт по
каждому сценарию. Повтор client doctor отдельно прочитан: все 16 checks ok.
Подробности конкретной инфраструктуры находятся только в локальной private-документации.

Полевая приёмка private ntfy-команд, systemd deployment, конкретной admin-resolution
и notification-health отказ → восстановление остаётся отдельной от реализации.
Эти проверки не блокируют выпуск по решению владельца. Очереди уведомлений
in-memory; приём провайдером не означает показ на телефоне; UNKNOWN не разрешается
автоматически. Оставшиеся планы сведены в [BACKLOG.md](../BACKLOG.md).
