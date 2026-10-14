# Проверки и статус выпуска

Version 1.0.6

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261015-002634

## Стабильное исправление 1.0.1: обновление трея

start/sync-tray заменяют контроллер другой версии; restart-tray явно обновляет
только GUI. Приватные записи адресуют запрос конкретному запуску. Старый
контроллер закрывает GUI и освобождает прежний lock перед новым bootstrap.
CLI и новый bootstrap ждут каждый не более восьми секунд, без убийства PID.
Уже нажатый пользователем Stop сохраняет приоритет; запрос UI сам службу не останавливает.
Для контроллеров 1.0.0 и старше требуется один ручной переход, явно обозначенный
restart_required. При тайм-ауте принятый запрос может завершиться позже.

Финальные чистые Windows CPython 3.12.2 wheel-from-sdist проверки **1.0.1**:

- Core: 1215 passed, 75 skipped, 2 deselected in 71.01s (0:01:11).
- Extras: 1650 passed, 2 deselected in 135.56s (0:02:15).
- Отчёты: build/validation-101-stable-core и build/validation-101-stable-extras.
- Ruff/style: 165 файлов, без ошибок; документация: 21 документ/13 JSON.
- 94 модуля и packaged resources сверены с исходниками; 12 installed CLIs.

Python-код совпадает с проверенным и принятым владельцем commit 0549c70
(предварительная версия 1.0.1.dev1). Изменены номер/документация; в шаблоне
инструкции номер доступной возможности заменён на стабильный 1.0.1.
[GitHub Release v1.0.1](https://github.com/SilverBull81/remote-watch/releases/tag/v1.0.1)
содержит wheel, sdist и SHA256SUMS.txt. Старые файлы/тег v1.0.0 не меняются.

21 новая проверка охватывает передачу настоящей OS-блокировки, версии, явный
restart, старый протокол, тайм-аут, старый запрос, CLI/SCM границу, закрытие GUI
с ошибкой и коллизию с проверочным lock. Последняя сначала воспроизведена на
промежуточном wheel: bootstrap пропадал, GUI не запускался. Исправление проверено
тем же детерминированным тестом. GUI, финальный Popen и SCM в этих тестах подставные:
владелец затем подтвердил работу доработки в реальном GUI. Это свидетельство
из переписки, а не независимый отчёт о каждом отрицательном сценарии.

Новая CI/Linux-проверка не запускалась; для выпуска выбран стабильный тег v1.0.1. Production процессы,
конфиги, служба, ACL и журналы не менялись. Новые deploy-комплекты содержат
restart-tray.cmd/.ps1; существующим можно добавить launchers повторным deploy.
MIT/ICO-only и исключение private проверены сборками. Последующая запись результатов
меняет только документацию; runtime/шаблоны остаются идентичны проверенным wheel.

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

## Локальная проверка

Windows CPython 3.12.2, чистые wheel-from-sdist окружения:

- Core: 1194 passed, 75 skipped, 2 deselected in 67.88s (0:01:07).
- Extras: 1629 passed, 2 deselected in 137.18s (0:02:17).
- Отчёты: build/validation-100-core и build/validation-100-extras.
- Стиль: 163 Python-файла; документация: 21 документ и 13 JSON-примеров.
- Проверяются 93 Python-модуля, packaged resources, ICO и 12 installed CLIs.

Финальные release-архивы заново строятся после записи результатов. Проверяются
License-Expression=MIT и точный LICENSE, ICO без PNG, docs/BACKLOG.md и отсутствие
private/runtime/credentials файлов. Локальный PNG сохраняется, но не входит в
Git-дерево тега, автоматически создаваемые исходные архивы, wheel или sdist.
Архив private-документов присутствовал при сборках и проверен на исключение.

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
