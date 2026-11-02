# Проверки и статус выпуска

Version 1.1.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261102-190841

## Совместимое расширение 1.2.0: журналы и служебные оповещения

Gateway Server лениво выбирает ежедневный JSONL в папках года/месяца;
ротация по размеру сохраняется внутри дня. Прежние плоские журналы остаются.
Manager и installer проверяют календарное дерево до изменения прав, отвергая
чужие имена, неверные даты, symlink/junction и чрезмерно большое дерево.
Необязательная application.alerts.grace_period откладывает только сообщения
о временной недоступности источника команд или доверенного времени.
Health, журнал переходов, авторизация и проверка trusted time не меняются.
Command wire/hash/SQLite, callback/reconnect и клиентский API не меняются.

12 новых contract tests проверяют переход месяца, пропуск пустого дня,
дописывание после перезапуска, ротацию внутри дня, сохранение старого файла,
чужие имена/даты/архивы, короткие и длительные отказы, смену временной причины,
восстановление и серьёзную ошибку во время выдержки, пределы настройки.
Существующий процессный тест проверяет запись нового календарного журнала.

Финальные чистые Windows CPython 3.12.2 wheel-from-sdist проверки **1.2.0**:

- Core: 1277 passed, 90 skipped, 2 deselected in 73.16s (0:01:13).
- Extras: 1727 passed, 2 deselected in 173.90s (0:02:53).
- Отчёты: build/validation-120-final-core и build/validation-120-final-extras.
- Ruff/style: 170 файлов; документация: 21 документ/13 JSON, без ошибок.
- Проверяются 96 Python-модулей, packaged resources, ICO и 12 installed CLIs.

Новая CI/Linux-матрица не запускалась; ниже сохранена последняя полная матрица 1.0.0.
Провайдеры подставные, HTTP использует loopback; полевой приёмки новой версии нет.
Production процессы, конфиги, credentials, службы, CA и журналы агентом не менялись.
Старые конфиги сохраняют немедленные оповещения; grace_period требует gateway 1.2.0+.
Runtime/шаблоны финального wheel сверяются с чистыми проверенными сборками.
MIT/ICO-only и исключение private сохраняются; прежние теги и файлы не заменяются.

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
