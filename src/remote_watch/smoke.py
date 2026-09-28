# Явный запуск одной пробной отправки с локальными настройками доступа.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-180519
#
# Классы:
# -> _Parser: Разбор аргументов без вывода ошибочных значений.
#    Интерфейс:
#    -> error(): Безопасное сообщение об ошибке аргументов.
#
# Функции:
# -> _load_settings(): Чтение только настроек выбранного сервиса.
# -> _notification(): Создание свежего синтетического уведомления.
# -> _send(): Одна попытка с обязательной очисткой клиента и временного токена.
# -> main(): Запуск выбранного smoke и безопасный код завершения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .channels import NotificationChannel
from .config import RetryPolicy
from .delivery import Delivery, DeliveryResult, DeliveryStatus
from .events import Identity, Notification

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Разбор аргументов без вывода ошибочных значений
#------------------------------------------------------------------------------------------------------------------
class _Parser(argparse.ArgumentParser):
    """Keep invalid argument values out of command diagnostics."""

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Безопасное сообщение об ошибке аргументов
    #--------------------------------------------------------------------------------------------------------------
    def error(
        self,
        message: str,
        ) -> None:

        """Reject invalid arguments without printing the original parser message.

        :param message: Original parser error, intentionally not displayed.
        :type message: str
        """

        # message может содержать введённое пользователем значение; не переносим его в вывод.

        raise ValueError("invalid smoke arguments")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение только настроек выбранного сервиса
#------------------------------------------------------------------------------------------------------------------
def _load_settings(
    path: Path,
    provider: str,
    ) -> dict[str, object]:

    """Read a bounded local JSON document and select one provider section.

    :param path: Explicit local credentials file.
    :type path: Path

    :param provider: Selected provider name.
    :type provider: str

    :return: Selected settings, including the private token.
    :rtype: dict[str, object]
    """

    # path - файл из текущего каталога либо явно переданный путь.
    # provider - единственная секция, которую требуется проверить для этого запуска.

    # Читаем с пределом размера. Ни файл, ни ошибки JSON не печатаются в терминал.
    with path.open("rb") as stream:
        raw = stream.read(65537)

    if len(raw) > 65536:
        raise ValueError("credentials file is too large")

    payload = json.loads(raw.decode("utf-8-sig"))
    settings = payload.get(provider) if isinstance(payload, dict) else None

    if not isinstance(settings, dict):
        raise ValueError("provider settings are missing")

    token = settings.get("token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("provider token is missing")

    # Пустая секция второго сервиса не мешает проверить уже настроенный канал.
    return settings
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание свежего синтетического уведомления
#------------------------------------------------------------------------------------------------------------------
def _notification() -> Notification:

    """Create a current synthetic message without identifying the real host.

    :return: Fresh notification with unique event and session identifiers.
    :rtype: Notification
    """

    now = datetime.now(timezone.utc)

    # Для ручной проверки используем явные тестовые сведения, а не рабочие логи приложения.
    return Notification(
        identity=Identity(
            service="remote-watch-smoke", environment="smoke", region="manual",
            host="manual", instance_id="smoke",
        ),
        event_id=uuid4().hex,
        session_id=uuid4().hex,
        created_at=now,
        expires_at=now + timedelta(minutes=1),
        level_no=20,
        level_name="INFO",
        logger_name="remote_watch.smoke",
        message="Проверка Remote Watch. Если вы видите это сообщение, канал доставки работает.",
    )
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Одна попытка с обязательной очисткой клиента и временного токена
#------------------------------------------------------------------------------------------------------------------
async def _send(
    provider: str,
    settings: dict[str, object],
    ) -> DeliveryResult:

    """Send once through the selected adapter and remove temporary authentication.

    :param provider: Provider selected explicitly by the caller.
    :type provider: str

    :param settings: Private settings loaded from the local file.
    :type settings: dict[str, object]

    :return: Sanitized result of exactly one provider attempt.
    :rtype: DeliveryResult
    """

    # provider - telegram или ntfy; одновременная рассылка двум сервисам здесь не выполняется.
    # settings - локальные настройки; их содержимое не входит в Notification и журнал.

    from .adapters.ntfy import NtfyChannel, NtfyConfig
    from .adapters.telegram import TelegramChannel, TelegramConfig

    token = settings.get("token")
    if not isinstance(token, str):
        raise ValueError("provider token is missing")

    policy = RetryPolicy(max_attempts=1)
    channel: NotificationChannel | None = None
    token_env = "REMOTE_WATCH_SMOKE_" + uuid4().hex.upper()

    # Адаптеры уже умеют безопасно читать ссылку на переменную. Используем отдельное имя
    # только в этом процессе и удаляем его даже при ошибке настройки, отправки или отмене.
    os.environ[token_env] = token
    try:
        if provider == "telegram":
            channel = TelegramChannel(TelegramConfig(
                token_env=token_env,
                chat_id=settings.get("chat_id", settings.get("chat")),
                endpoint=settings.get("endpoint", "https://api.telegram.org"),
                message_thread_id=settings.get("message_thread_id"),
                allow_http=settings.get("allow_http", False),
            ), retry=policy)
        elif provider == "ntfy":
            channel = NtfyChannel(NtfyConfig(
                token_env=token_env,
                topic=settings.get("topic", settings.get("chat")),
                endpoint=settings.get("endpoint", "https://ntfy.sh"),
                allow_http=settings.get("allow_http", False),
            ), retry=policy)
        else:
            raise ValueError("unknown provider")

        await asyncio.wait_for(channel.open(), 5)
        delivery = Delivery(
            notification=_notification(), destination_id="smoke-" + provider, delivery_id=uuid4().hex,
        )
        return await asyncio.wait_for(channel.send(delivery), policy.attempt_timeout)
    finally:
        os.environ.pop(token_env, None)
        if channel is not None:
            await asyncio.wait_for(channel.close(), 5)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запуск выбранного smoke и безопасный код завершения
#------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:

    """Run one explicitly requested smoke test without displaying private settings.

    :param argv: Command arguments; None reads the current process arguments.
    :type argv: Sequence[str] | None

    :return: Zero on provider acceptance, one on delivery failure, two on setup failure.
    :rtype: int
    """

    # argv - имя сервиса и необязательный путь к файлу; токены через командную строку не принимаются.

    parser = _Parser(description="Отправить одно синтетическое уведомление Remote Watch.")
    parser.add_argument("provider", choices=("telegram", "ntfy"))
    parser.add_argument("--credentials", type=Path, default=Path("credentials.local.json"))

    try:
        args = parser.parse_args(argv)
        settings = _load_settings(args.credentials, args.provider)
        result = asyncio.run(_send(args.provider, settings))
    except asyncio.TimeoutError:
        print("Время проверки истекло. Исход доставки неизвестен; повторная отправка не выполнялась.")
        return 1
    except (ValueError, TypeError, OSError, ImportError, RecursionError):
        print("Ошибка настройки smoke. Проверьте локальный JSON и установку нужного extra; см. docs/SMOKE.md.")
        return 2
    except KeyboardInterrupt:
        print("Проверка прервана. Сообщение могло быть принято сервисом.")
        return 130
    except Exception:
        # Исходный traceback может содержать URL Telegram. Наружу выдаём только общий исход.
        print("Проверка не завершилась штатно. Исход доставки неизвестен; подробности скрыты.")
        return 1

    if result.status is DeliveryStatus.PROVIDER_ACCEPTED:
        print(f"{args.provider}: сообщение принято сервисом. Проверьте получение на телефоне.")
        return 0

    print(f"{args.provider}: {result.status.value}; повторная отправка не выполнялась.")
    return 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явный запуск smoke через python -m remote_watch.smoke
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
