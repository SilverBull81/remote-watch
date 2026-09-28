# Сведения о приложении и данные уведомления: проверка полей и преобразование в словарь.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-140516
#
# Классы:
# -> Identity: Сведения о приложении и его размещении.
#    Специальные методы:
#    -> __post_init__(): Проверка сведений о приложении.
#
# -> SnapshotLimits: Ограничения размера уведомления.
#    Специальные методы:
#    -> __post_init__(): Проверка ограничений.
#
# -> Notification: Данные уведомления без ссылок на исходный LogRecord.
#    Интерфейс:
#    -> to_dict(): Копия данных уведомления для передачи.
#    -> from_dict(): Чтение уведомления из словаря с проверкой формата.
#    Специальные методы:
#    -> __post_init__(): Проверка полей и размера уведомления.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import ClassVar

from ._validation import require_int, require_text, text_tuple

#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Сведения о приложении и его размещении
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Identity:
    """Identify an application independently of provider display names.

    Fields service/environment identify the application and deployment tier;
    region/host identify its placement; instance_id identifies a logical instance.
    Every field is an explicitly supplied nonblank string of at most 256 UTF-8 bytes.
    """

    service: str        # Название приложения.
    environment: str    # Окружение: рабочее, тестовое и т. п.
    region: str         # Регион размещения.
    host: str           # Имя сервера.
    instance_id: str    # Идентификатор экземпляра приложения.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка сведений о приложении
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate all explicit identity fields."""

        # Все сведения задаёт приложение: имя сервера и экземпляра не угадываем по окружению.
        for item in fields(self):
            require_text(getattr(self, item.name), item.name)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ограничения размера уведомления
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotLimits:
    """Bound serialized event, text, exception and metadata sizes in UTF-8 bytes.

    event_max_bytes bounds the complete compact JSON representation;
    message_max_bytes and exception_max_bytes bound individual strings;
    metadata_max_bytes bounds all other serialized event fields together.
    """

    event_max_bytes: int = 16384        # Предел размера всего уведомления, байт.
    message_max_bytes: int = 8192       # Предел размера сообщения, байт.
    exception_max_bytes: int = 4096     # Предел размера описания ошибки, байт.
    metadata_max_bytes: int = 2048      # Предел размера служебных полей, байт.

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка ограничений
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject nonpositive budgets and fields exceeding the event budget."""

        # Отдельное поле не может занимать больше, чем разрешено всему уведомлению.
        for item in fields(self):
            require_int(getattr(self, item.name), item.name)

            if getattr(self, item.name) > self.event_max_bytes:
                raise ValueError(f"{item.name} exceeds event_max_bytes")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Подготовленное уведомление для отправки
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Notification:
    """Store rendered notification data, never a LogRecord or traceback object.

    event_id/session_id identify the event and runtime session. Identity is explicit.
    created_at/expires_at are aware datetimes normalized to UTC. level_no/level_name
    and logger_name describe the source. message/exception are rendered strings.
    topic, tags, correlation_id and trace_id are optional bounded metadata.
    notify is a tri-state eligibility hint. truncated_fields names shortened texts.
    limits is a local validation policy and is not serialized. SCHEMA_VERSION is 1.
    Construction validates but never truncates, performs I/O or generates identity.
    """

    SCHEMA_VERSION: ClassVar[int] = 1   # Версия формата передачи данных.
    event_id: str                       # Идентификатор события.
    session_id: str                     # Идентификатор текущего запуска приложения.
    identity: Identity                  # Сведения об отправившем приложении.
    created_at: datetime                # Время создания с часовым поясом.
    expires_at: datetime                # Время, после которого отправка не нужна.
    level_no: int                       # Числовой уровень важности из logging.
    level_name: str                     # Название уровня важности.
    logger_name: str                    # Имя исходного логгера.
    message: str = field(repr=False)    # Готовый текст сообщения.
    exception: str | None = field(default=None, repr=False)     # Готовое описание ошибки, если есть.
    topic: str | None = None            # Тема для выбора получателей.
    tags: tuple[str, ...] = ()          # Метки для выбора получателей.
    correlation_id: str | None = None   # Идентификатор связанной операции.
    trace_id: str | None = None         # Идентификатор цепочки вызовов.
    notify: bool | None = None          # Указание на отправку; None — по правилам.
    truncated_fields: tuple[str, ...] = ()      # Имена ранее сокращённых текстовых полей.
    limits: SnapshotLimits = field(     # Ограничения размера при проверке.
        default_factory=SnapshotLimits,
        repr=False,
        compare=False,
        )

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Копия данных уведомления для передачи
    #--------------------------------------------------------------------------------------------------------------
    def to_dict(self) -> dict[str, object]:

        """Return a fresh JSON-compatible payload with explicit schema version.

        :return: Detached primitive data; mutations cannot affect this notification.
        :rtype: dict[str, object]
        """

        # Локальные ограничения размера не передаём: принимающая сторона использует свои настройки.
        payload = {item.name: getattr(self, item.name) for item in fields(self) if item.name != "limits"}

        # Вложенные объекты превращаем в независимые словари и списки, даты — в строки ISO 8601.
        # Получатель словаря может менять его, не затрагивая исходное уведомление.
        payload.update(
            schema_version=self.SCHEMA_VERSION,
            identity=asdict(self.identity),
            created_at=self.created_at.isoformat(),
            expires_at=self.expires_at.isoformat(),
            tags=list(self.tags),
            truncated_fields=list(self.truncated_fields),
        )
        return payload
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение уведомления из словаря с проверкой формата
    #--------------------------------------------------------------------------------------------------------------
    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, object],
        *,
        limits: SnapshotLimits | None = None,
        ) -> Notification:

        """Validate a schema-1 payload using local size limits.

        :param payload: JSON-compatible notification data.
        :type payload: Mapping[str, object]

        :param limits: Local limits, never accepted from the remote payload.
        :type limits: SnapshotLimits | None

        :return: Validated immutable notification.
        :rtype: Notification
        """

        # payload - данные уведомления, полученные из JSON.
        # limits - ограничения размера из настроек принимающего приложения.

        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")

        # Не пытаемся угадать формат неизвестной версии или молча отбросить лишние поля.
        version = payload.get("schema_version")

        if type(version) is not int or version != cls.SCHEMA_VERSION:
            raise ValueError("unsupported notification schema_version")

        allowed = {item.name for item in fields(cls)} - {"limits"}

        if set(payload) - allowed - {"schema_version"}:
            raise ValueError("notification contains unknown fields")

        # Рабочая копия позволяет восстановить типы полей, не меняя словарь вызывающего кода.
        data = dict(payload)
        del data["schema_version"]

        try:
            identity = data["identity"]

            if not isinstance(identity, Mapping):
                raise TypeError("identity must be a mapping")

            data["identity"] = Identity(**identity)

            for name in ("created_at", "expires_at"):
                if not isinstance(data[name], str):
                    raise TypeError("timestamp must be a string")

                data[name] = datetime.fromisoformat(data[name].replace("Z", "+00:00"))

            # Конструктор повторно проверит все поля, размеры и соотношение дат.
            return cls(**data, limits=limits if limits is not None else SnapshotLimits())
        except (KeyError, TypeError, ValueError):
            # Внешние данные могут содержать секреты; не включаем их и исходную ошибку в сообщение.
            raise ValueError("invalid notification payload") from None
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СПЕЦИАЛЬНЫЙ МЕТОД : Проверка полей и размера уведомления
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Normalize immutable fields and validate types, time bounds and sizes."""

        # Сначала проверяем вложенные настройки и сведения об отправителе.
        # Они нужны для последующих проверок текста и служебных полей.
        if not isinstance(self.identity, Identity):
            raise TypeError("identity must be Identity")

        if not isinstance(self.limits, SnapshotLimits):
            raise TypeError("limits must be SnapshotLimits")

        # Обязательные идентификаторы должны быть непустыми; дополнительные поля могут отсутствовать.
        for name in ("event_id", "session_id", "level_name", "logger_name"):
            require_text(getattr(self, name), name)

        require_int(self.level_no, "level_no", 0)

        for name in ("topic", "correlation_id", "trace_id"):
            value = getattr(self, name)

            if value is not None:
                require_text(value, name)

        if self.notify is not None and type(self.notify) is not bool:
            raise TypeError("notify must be bool or None")

        # Приводим обе даты к UTC, чтобы сравнение не зависело от часового пояса отправителя.
        # Текущее время здесь не читаем: проверка срока отправки будет обязанностью очереди.
        for name in ("created_at", "expires_at"):
            value = getattr(self, name)

            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")

            object.__setattr__(self, name, value.astimezone(timezone.utc))

        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")

        # Копируем коллекции: изменение исходного списка не должно менять готовое уведомление.
        # object.__setattr__ нужен только при создании объекта с frozen=True.
        object.__setattr__(self, "tags", text_tuple(self.tags, "tags"))
        object.__setattr__(self, "truncated_fields", text_tuple(self.truncated_fields, "truncated_fields"))

        if not set(self.truncated_fields) <= {"message", "exception"}:
            raise ValueError("truncated_fields contains an unsupported field")

        # Здесь только проверяем размер. Подготовка и сокращение текста выполняются до создания объекта.
        require_text(self.message, "message", self.limits.message_max_bytes, True)

        if self.exception is not None:
            require_text(self.exception, "exception", self.limits.exception_max_bytes, True)

        # Проверяем размер готового JSON: экранирование символов увеличивает объём передаваемых данных.
        payload = self.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        if len(encoded) > self.limits.event_max_bytes:
            raise ValueError("notification exceeds event_max_bytes")

        # Ограничиваем служебные поля отдельно, чтобы длинные метки не заняли весь доступный объём.
        metadata = {key: value for key, value in payload.items() if key not in ("message", "exception")}

        if len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > (
            self.limits.metadata_max_bytes
        ):
            raise ValueError("notification exceeds metadata_max_bytes")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch.events не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
