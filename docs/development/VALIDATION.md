# Проверки и статус выпуска

Version 1.0.2

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-202944

## Этап 0.4 завершён

Функциональная приёмка завершена: владелец подтвердил работу уведомлений/команд,
reconnect, deploy/doctor, Windows-службы/tray/install/remove/reinstall, последующего
reboot и crash-тестов, а также доставку служебных оповещений. Это свидетельство
из переписки, не независимый отчёт по каждому сценарию. Повтор client doctor отдельно
прочитан: все 16 checks ok, включая TLS, authorization, trusted time и session presence.

Подробности конкретной инфраструктуры хранятся только в локальной private-документации.
Приёмка не распространяется на private ntfy-команды, реальный systemd deployment,
применение конкретной admin-resolution и отложенный полевой отказ → восстановление
notification health. Эти проверки не блокируют 0.4 по решению владельца.

## Полная CI-матрица

[GitHub Actions 38602660574](https://github.com/SilverBull81/remote-watch/actions/runs/38602660574), code commit `1c05538638156f04a0760b4bc714fe445de2358a`, пакет **0.4.5.dev6**.
Все восемь jobs успешны. Каждый выполняет Ruff/style/docs checks, сборку sdist/wheel,
чистую установку wheel из sdist, installed CLI и полные offline tests.

| Платформа | CPython | Режим | Результат |
| --- | --- | --- | --- |
| Linux | 3.10.22 | core | 1180 passed, 89 skipped, 2 deselected in 22.90s |
| Linux | 3.10.22 | extras | 1613 passed, 16 skipped, 2 deselected in 76.77s (0:01:16) |
| Linux | 3.12.15 | core | 1180 passed, 89 skipped, 2 deselected in 26.00s |
| Linux | 3.12.15 | extras | 1613 passed, 16 skipped, 2 deselected in 75.30s (0:01:15) |
| Windows | 3.10.11 | core | 1194 passed, 75 skipped, 2 deselected in 191.35s (0:03:11) |
| Windows | 3.10.11 | extras | 1629 passed, 2 deselected in 263.08s (0:04:23) |
| Windows | 3.12.10 | core | 1194 passed, 75 skipped, 2 deselected in 114.10s (0:01:54) |
| Windows | 3.12.10 | extras | 1629 passed, 2 deselected in 213.32s (0:03:33) |

Два integration-теста live providers исключены, а не выполнены в CI.
Платформенные/optional skips указаны в каждой строке. Уведомления на телефон и
настоящие Windows-службы CI не запускает. Последующий коммит записи этих результатов
меняет только документацию; code SHA тестирования указан выше явно.

Промежуточные матрицы выявили тестовые зависимости от canonical paths, uptime и
100-мс создания SQLite, предположение о свободном соседнем порте и скорость fsync,
а также погрешность Retry-After в gateway. Причины исправлены,
не скрыты retry CI или ослаблением production-проверок. Для Retry-After два
детерминированных HTTP-теста сначала воспроизвели 11 вместо 10, затем проверили
исправление и сохранение запрета раннего допуска. Actions закреплены по SHA с Node 24.

## Локальный контроль финального runtime

Windows CPython 3.12.2, wheel-from-sdist 0.4.5.dev6:

- Core: 1194 passed, 75 skipped, 2 deselected in 67.75s (0:01:07).
- Extras: 1629 passed, 2 deselected in 136.99s (0:02:16).
- Отчёты: build/validation-0456-core и build/validation-0456-extras.
- 93 Python-модуля и packaged resources совпадают с исходниками; 12 installed CLIs.
- Стиль: 163 файла. Документация: 21 документ и 13 JSON-примеров.
- Проверен настоящий Windows short-path temp alias в чистом core venv.

Архив прежних private-документов реально присутствовал при локальной сборке;
sdist/wheel проверены на его отсутствие. Runtime credentials/SQLite/control,
не относящиеся к проекту локальные файлы и private-instructions не включены.

## Границы prod-релиза

После этой CI-матрицы владелец выбрал MIT. Лицензия и перенос backlog
проверены отдельно сборкой; CI выше относится к указанному code commit.
Для первого stable-релиза рекомендован 1.0.0 и тег v1.0.0; публикация ещё не выполнена.
Список оставшихся работ — только docs/BACKLOG.md. Завершение 0.4 и зелёный CI не
заменяют юридическую проверку зависимостей/assets и настройку целевого deployment.

## MIT и перенос backlog после CI

Сборка sdist и повторная сборка wheel из него прошли. Оба архива содержат
точный текст LICENSE и metadata License-Expression=MIT, License-File=LICENSE.
В sdist есть docs/BACKLOG.md, прежнего root BACKLOG.md нет; private-документы
исключены. Все 93 Python-модуля wheel совпадают с исходниками.
Отчёт: build/validation-mit/report.json. Изменений runtime-кода не было.

Проверки: documentation — 21 документ/13 JSON, style — 163 файла, Ruff — без
ошибок; 10 тестов документации прошли. Полная CI-матрица повторно не запускалась.
