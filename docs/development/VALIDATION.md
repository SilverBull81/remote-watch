# Проверки и статус выпуска

Version 1.0.0

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-184253

## Текущий статус

Подготовка первого публичного prod-релиза выполняется на ветке разработки.
Лицензия, номер/tag первого релиза и публикация ещё не выбраны.
Завершение функционального этапа 0.4 не означает публикацию stable-пакета.

Владелец подтвердил полевую работу уведомлений и команд, reconnect, deploy/doctor,
Windows-службы, tray, install/remove/reinstall, reboot и crash-тестов, а также
доставку служебных оповещений владельцу. Это приёмка из переписки, не имитация в CI.
После устранения конфликта прав внешнего HTTPS-прокси дополнительный reboot прошёл
штатно. До него отдельно прочитан повтор client doctor: все 16 checks ok, включая
TLS, authorization, trusted time и presence командной сессии.

Точные инфраструктурные пути, topology и исходные field отчёты находятся только
в локальной private-документации. Они не нужны пользователю для настройки библиотеки.
Полевое восстановление notification health после воспроизводимого отказа отложено;
private ntfy-команды и реальный systemd deployment не приняты полевым запуском.

## Последняя чистая локальная база

0.4.5.dev4, Windows CPython 3.12.2, wheel-from-sdist:

- Core: 1191 passed, 75 skipped, 2 live deselections.
- Extras: 1624 passed, 2 live deselections.
- Ruff/style: 161 Python-файл; 17 style regressions.
- Все 93 production Python-модуля wheel совпали с исходниками; 12 installed CLIs.

Это результаты до последующего аудита/реорганизации, не повтор нового CI.
Текущий итоговый контроль будет записан сюда после завершения матрицы.

Кандидат 0.4.5.dev5: целевые проверки документации/журналов/health/style —
85 passed. Чистый Windows CPython 3.12.2 core wheel-from-sdist:
1194 passed, 75 skipped, 2 deselected; без предупреждений pytest.
Отчёт build/validation-0455-core. Стиль: 163 файла; документация: 21 документ
и 13 JSON-примеров. В локальной private-папке при сборке действительно находился
архив прежних документов; sdist проверен на его отсутствие.
Extras и полный CI этого кандидата пока не завершены.

## Обязательный контроль изменения

Ruff, AST-style, ссылки/примеры документации, targeted regression tests,
полные core/extras wheels из sdist и installed CLI. Архивы не содержат private,
credentials, SQLite и runtime control. Обычные тесты используют fake providers,
loopback TLS и synthetic state, никогда production tokens.

CI manual-only: Windows/Linux × CPython 3.10/3.12 × core/extras, восемь jobs.
Логи/report.json/tests.xml сохраняются GitHub Actions как artifacts. Успех только
Windows или только source tree не объявляется успехом всей матрицы.
Полевая Windows-служба не доказывает systemd и не покрывает все возможные callback
side effects; UNKNOWN и запрет callback replay остаются обязательными ограничениями.
