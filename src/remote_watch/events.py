# Неизменяемые модели идентичности и уведомления с проверяемой сериализацией.
# Классы:
# -> Identity: Явная идентичность приложения.
#    -> __post_init__(): Проверка полей.
# -> SnapshotLimits: Размеры удерживаемого снимка.
#    -> __post_init__(): Проверка бюджетов.
# -> Notification: Снимок события без ссылок на LogRecord.
#    -> __post_init__(): Валидация и фиксация полей.
#    -> to_dict(): Независимое JSON-совместимое представление.
#    -> from_dict(): Проверка версии и восстановление снимка.
#
# Version 1.0.0
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
# Дата и время последнего изменения: 260928-110519

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
# КЛАСС : Явная идентичность приложения
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Identity:
    """Identify an application independently of provider display names.

    Fields service/environment identify the application and deployment tier;
    region/host identify its placement; instance_id identifies a logical instance.
    Every field is an explicitly supplied nonblank string of at most 256 UTF-8 bytes.
    """

    service: str
    environment: str
    region: str
    host: str
    instance_id: str

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка идентичности
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Validate all explicit identity fields."""

        for item in fields(self):
            require_text(getattr(self, item.name), item.name)
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Бюджеты размера снимка
#------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotLimits:
    """Bound serialized event, text, exception and metadata sizes in UTF-8 bytes.

    event_max_bytes bounds the complete compact JSON representation;
    message_max_bytes and exception_max_bytes bound individual strings;
    metadata_max_bytes bounds all other serialized event fields together.
    """

    event_max_bytes: int = 16384
    message_max_bytes: int = 8192
    exception_max_bytes: int = 4096
    metadata_max_bytes: int = 2048

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка бюджетов
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Reject nonpositive budgets and fields exceeding the event budget."""

        for item in fields(self):
            require_int(getattr(self, item.name), item.name)
            if getattr(self, item.name) > self.event_max_bytes:
                raise ValueError(f"{item.name} exceeds event_max_bytes")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Сериализуемый неизменяемый снимок уведомления
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

    SCHEMA_VERSION: ClassVar[int] = 1
    event_id: str
    session_id: str
    identity: Identity
    created_at: datetime
    expires_at: datetime
    level_no: int
    level_name: str
    logger_name: str
    message: str = field(repr=False)
    exception: str | None = field(default=None, repr=False)
    topic: str | None = None
    tags: tuple[str, ...] = ()
    correlation_id: str | None = None
    trace_id: str | None = None
    notify: bool | None = None
    truncated_fields: tuple[str, ...] = ()
    limits: SnapshotLimits = field(default_factory=SnapshotLimits, repr=False, compare=False)

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка снимка и ограничение удерживаемых данных
    #--------------------------------------------------------------------------------------------------------------
    def __post_init__(self) -> None:

        """Normalize immutable fields and validate types, time bounds and sizes."""

        if not isinstance(self.identity, Identity):
            raise TypeError("identity must be Identity")
        if not isinstance(self.limits, SnapshotLimits):
            raise TypeError("limits must be SnapshotLimits")
        for name in ("event_id", "session_id", "level_name", "logger_name"):
            require_text(getattr(self, name), name)
        require_int(self.level_no, "level_no", 0)
        for name in ("topic", "correlation_id", "trace_id"):
            value = getattr(self, name)
            if value is not None:
                require_text(value, name)
        if self.notify is not None and type(self.notify) is not bool:
            raise TypeError("notify must be bool or None")

        # Время нормализуется без обращения к системным часам.
        for name in ("created_at", "expires_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")
            object.__setattr__(self, name, value.astimezone(timezone.utc))
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")

        object.__setattr__(self, "tags", text_tuple(self.tags, "tags"))
        object.__setattr__(self, "truncated_fields", text_tuple(self.truncated_fields, "truncated_fields"))
        if not set(self.truncated_fields) <= {"message", "exception"}:
            raise ValueError("truncated_fields contains an unsupported field")
        require_text(self.message, "message", self.limits.message_max_bytes, True)
        if self.exception is not None:
            require_text(self.exception, "exception", self.limits.exception_max_bytes, True)

        # Отдельно проверяем полный JSON: escaping тоже занимает место в очереди/на wire.
        payload = self.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self.limits.event_max_bytes:
            raise ValueError("notification exceeds event_max_bytes")
        metadata = {key: value for key, value in payload.items() if key not in ("message", "exception")}
        if len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > (
            self.limits.metadata_max_bytes
        ):
            raise ValueError("notification exceeds metadata_max_bytes")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ПУБЛИЧНЫЙ МЕТОД : Независимое представление снимка
    #--------------------------------------------------------------------------------------------------------------
    def to_dict(self) -> dict[str, object]:

        """Return a fresh JSON-compatible payload with explicit schema version.

        :return: Detached primitive data; mutations cannot affect this notification.
        :rtype: dict[str, object]
        """

        payload = {item.name: getattr(self, item.name) for item in fields(self) if item.name != "limits"}
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
    # ПУБЛИЧНЫЙ МЕТОД : Восстановление снимка с проверкой схемы
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

        # payload - сериализованный снимок.
        # limits - доверенная локальная политика размера.

        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        version = payload.get("schema_version")
        if type(version) is not int or version != cls.SCHEMA_VERSION:
            raise ValueError("unsupported notification schema_version")
        allowed = {item.name for item in fields(cls)} - {"limits"}
        if set(payload) - allowed - {"schema_version"}:
            raise ValueError("notification contains unknown fields")
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
            return cls(**data, limits=limits if limits is not None else SnapshotLimits())
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid notification payload") from None
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------
