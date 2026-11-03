# Проверки и статус выпуска

Version 1.1.3

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261103-111759

## Совместимое расширение 1.3.0: конфиги комплекта в config/

Новый deploy создаёт рабочие конфиги в config/, серверные/клиентские примеры и
Caddyfile.example — в config/examples/. Скрипты остаются в корне, выбирают новый
путь общего JSON из любой cwd. Обновлены Windows service/tray, POSIX launchers,
systemd unit, installed CLI probes и публичные инструкции.
Пути шаблонов рассчитаны на рабочую копию в config: control/logs/logo/state_dir
через ../ продолжают указывать на прежние каталоги корня. SQLite/CA не переносятся.
Явные --*-config ссылки сохраняют исходные файлы и относительную базу.
Прежний корневой gateway_server.local.json вызывает layout_migration_required
до записи обычного deploy; отдельные update-templates proposals разрешены.
Ни deploy, ни обновление библиотеки не меняют service binding или рабочую установку.
Wire/hash/SQLite/ACL и клиентский API не меняются; прежние явные CLI config-пути работают.

Четыре добавленных contract cases проверяют old-layout отказ с/без dry-run,
безопасные предложения миграции, вложенный symlink/junction и новый managed Caddyfile.
Расширены существующие проверки всех components, сохранения абсолютных data/control/log
путей, doctor, installer, повторного deploy и реального запуска shell из другой cwd.

Финальные чистые Windows CPython 3.12.2 wheel-from-sdist проверки **1.3.0**:

- Core: 1286 passed, 90 skipped, 2 deselected in 74.65s (0:01:14).
- Extras: 1736 passed, 2 deselected in 175.11s (0:02:55).
- Отчёты: .development/validation-130-final-core и .development/validation-130-final-extras.
- Ruff/style: 171 файл; документация: 21 документ/13 JSON, без ошибок.
- Проверяются 96 Python-модулей, packaged resources, ICO и 12 installed CLIs.

Новая CI/Linux-матрица не запускалась; ниже сохранена последняя полная матрица 1.0.0.
Провайдеры подставные, HTTP использует loopback; эта проверка не выполняла реальную
миграцию или установку службы. Production при подготовке выпуска не менялся.
Последующие разрешённые оператором локальные операции фиксируются только в docs/private
и не подменяют результаты release-проверок или отдельную полевую приёмку.
Runtime/шаблоны финального wheel сверяются с чистыми проверенными сборками.
MIT/ICO-only и исключение private сохраняются; прежние теги и файлы не заменяются.

## Проверка обслуживания рабочего каталога

Рабочие артефакты перенесены в `.development/`; пути tools/CI, исключения Git/sdist
и build_base setuptools согласованы с ней. Это не новый релиз: версия библиотеки
остаётся 1.3.0, опубликованные файлы и теги не меняются.

Чистые Windows CPython 3.12.2 проверки обслуженного дерева:

- Core: 1286 passed, 90 skipped, 2 deselected in 75.81s.
- Extras: 1736 passed, 2 deselected in 168.18s.
- Отчёты: .development/maintenance-core и .development/maintenance-extras.
- Ruff/style/docs без ошибок; installed CLI и wheel-from-sdist прошли.
- Состав runtime/resources нового wheel побайтно совпал с опубликованным 1.3.0:
  116 файлов внутри remote_watch/. Рабочая папка и private не попали в sdist.
- Старый build/ не создан; --output внутри него отвергается до записи.

Перенесённый Python 3.10.21 проверен запуском --version; это не полный прогон тестов
на 3.10. Новая CI/Linux-матрица, provider field tests и публикация не выполнялись.
Производственные установки при этом обслуживании не менялись.

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
