# Ограниченная проверка прав и готовности без регистрации и исполнения команд.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261013-220456
#
# Классы:
# -> ProbeEndpoint: Отдельный read-only обработчик с ограничением нагрузки.
#    Конструктор:
#    -> __init__(): Привязка проверки credentials и наблюдения состояния.
#    Интерфейс:
#    -> handle(): Проверка запроса и возврат только безопасных признаков.
#
# Функции:
# -> decode_probe(): Строгая ограниченная схема диагностического запроса.
# -> _pairs(): Запрет повторяющихся ключей JSON.
# -> probe_response(): Отбор разрешённых полей состояния без исходных значений.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from remote_watch.events import Identity


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Строгая ограниченная схема диагностического запроса
#------------------------------------------------------------------------------------------------------------------
def decode_probe(data: bytes) -> tuple[Identity, tuple[str, ...]]:

    """Decode a bounded identity and requested permissions without accepting execution data.

    :param data: UTF-8 JSON request, at most sixteen KiB.
    :type data: bytes

    :return: Exact application identity and explicit notification aliases or command scopes.
    :rtype: tuple[Identity, tuple[str, ...]]
    """

    # data — отдельная схема, без session_id, command_id, callback и provider actor.
    if len(data) > 16384:
        raise ValueError("probe_size")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    if (type(value) is not dict or set(value) != {"schema_version", "identity", "permissions"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("probe_schema")
    identity = value["identity"]
    if type(identity) is not dict or set(identity) != {"service", "environment", "region", "host", "instance_id"}:
        raise ValueError("probe_identity")
    permissions = value["permissions"]
    if (type(permissions) is not list or not 1 <= len(permissions) <= 64
            or any(type(item) is not str or not 1 <= len(item) <= 128 or not item.strip()
                   or any(ord(char) < 32 for char in item) for item in permissions)
            or len(set(permissions)) != len(permissions)):
        raise ValueError("probe_permissions")
    return Identity(**identity), tuple(permissions)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрет повторяющихся ключей JSON
#------------------------------------------------------------------------------------------------------------------
def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:

    """Reject ambiguous duplicate fields without including their names in an error.

    :param items: Decoded JSON object members.
    :type items: list[tuple[str, Any]]

    :return: Object with unique keys.
    :rtype: dict[str, Any]
    """

    # items — одинаковые ключи запрещены на любом уровне объекта.
    value = dict(items)
    if len(value) != len(items):
        raise ValueError("probe_duplicate")
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Отбор разрешённых полей состояния без исходных значений
#------------------------------------------------------------------------------------------------------------------
def probe_response(
    component: str,
    state: dict[str, Any],
) -> dict[str, Any]:

    """Build a fixed response that cannot disclose configuration or journal records.

    :param component: Fixed commands or notifications channel label.
    :type component: str

    :param state: Locally observed health, filtered before transmission.
    :type state: dict[str, Any]

    :return: Versioned readiness and scoped session-presence flags.
    :rtype: dict[str, Any]
    """

    # component/state — выводим только фиксированные признаки; значения запроса не отражаем.
    if component not in ("commands", "notifications"):
        raise ValueError("probe_component")
    reasons = {"closed", "not_started", "time_unavailable", "maintenance_failed", "busy",
               "storage_busy", "storage_unavailable", "unavailable"}
    reason = state.get("reason")
    ready = state.get("ready") is True
    return {"schema_version": 1, "component": component, "authorized": True, "ready": ready,
            "reason": None if ready else reason if type(reason) is str and reason in reasons else "unavailable",
            "time_ready": state.get("time_ready") if type(state.get("time_ready")) is bool else None,
            "session_present": state.get("session_present")
            if type(state.get("session_present")) is bool else None}
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Отдельный read-only обработчик с ограничением нагрузки
#------------------------------------------------------------------------------------------------------------------
class ProbeEndpoint:

    """Authorize before body reading and never call registration, storage or delivery operations."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        component: str,
        authenticate: Callable[[Any], Any],
        observe: Callable[[Identity], dict[str, Any]],
    ) -> None:

        """Retain read-only callbacks and an independent two-request admission bound.

        :param component: Commands or notifications channel label.
        :type component: str

        :param authenticate: Existing credential check returning a principal or None.
        :type authenticate: Callable[[Any], Any]

        :param observe: Cached local health for one authorized identity.
        :type observe: Callable[[Identity], dict[str, Any]]
        """

        # component/authenticate/observe — зависимости не открывают серверы и не читают окружение.
        self._component = component
        self._authenticate = authenticate
        self._observe = observe
        self._active = 0
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Проверка запроса и возврат только безопасных признаков
    #--------------------------------------------------------------------------------------------------------------
    async def handle(
        self,
        request: Any,
    ) -> Any:

        """Answer one bounded authenticated probe without consuming operational queue capacity.

        :param request: Incoming aiohttp request; optional dependency is imported at call time.
        :type request: Any

        :return: Small fixed-schema JSON response; failures never echo inputs.
        :rtype: Any
        """

        # request — doctor имеет отдельную квоту; потоки доставки и команд не занимают её.
        from aiohttp import web

        counted = False
        status, payload = 400, {"code": "invalid"}
        try:
            principal = self._authenticate(request)
            if principal is None:
                status, payload = 401, {"code": "denied"}
            elif self._active >= 2:
                status, payload = 429, {"code": "busy"}
            else:
                self._active += 1
                counted = True
                if (request.content_type != "application/json" or request.headers.get("Content-Encoding")
                        or request.content_length is not None and request.content_length > 16384):
                    raise ValueError("probe_body")
                # Ограничение проверяется и при chunked: request.read() здесь не используется.
                data = bytearray()
                deadline = asyncio.get_running_loop().time() + 3
                while True:
                    chunk = await asyncio.wait_for(request.content.read(16385 - len(data)),
                                                   max(0, deadline - asyncio.get_running_loop().time()))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 16384:
                        raise ValueError("probe_size")
                identity, permissions = decode_probe(bytes(data))
                identities = principal.identities if self._component == "commands" else (principal.identity,)
                allowed = principal.scopes if self._component == "commands" else principal.aliases
                if identity not in identities or not set(permissions) <= set(allowed):
                    status, payload = 403, {"code": "denied"}
                else:
                    payload = probe_response(self._component, self._observe(identity))
                    status = 200
        except Exception:
            status, payload = 400, {"code": "invalid"}
        finally:
            if counted:
                self._active -= 1
        response = web.json_response(payload, status=status,
                                     headers={"Cache-Control": "no-store", "X-Remote-Watch-Probe": "1",
                                              "X-Remote-Watch-Component": self._component})
        response.force_close()
        return response
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль doctor_protocol не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
