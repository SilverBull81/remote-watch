# Ограниченные сообщения состояния между управляющим и дочерними процессами.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Функции:
# -> status_message(): Проверка состояния без переноса произвольных подробностей.
#
# -> read_limited(): Чтение полного ответа в пределах установленного размера


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio

from remote_watch.commands.journal_stats import validate_status

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

STATES = frozenset({"starting", "ready", "degraded", "failed", "stopping", "stopped", "restarting"})
REASONS = frozenset({"time_unavailable", "source_stopped", "source_unavailable", "source_denied",
    "maintenance_failed", "storage_unavailable", "storage_capacity", "configuration", "authorization",
    "ownership", "storage", "crash", "hang", "restart_limit", "startup_timeout", "heartbeat_timeout",
    "shutdown_timeout", "control_unavailable", "not_started", "closed", "caddy_unavailable", "logging_failed",
    "missing_sample", "source_failed", "refresh_cancelled", "invalid_sample", "sample_expired", "uncertainty",
    "clock_rollback", "stale_session", "unavailable", "busy", "capacity", "denied", "conflict", "invalid",
    "tray_unavailable", "dependency_missing"})

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка состояния без переноса произвольных подробностей.
#------------------------------------------------------------------------------------------------------------------
def status_message(
    value: object,
    ) -> dict:

    """
    Accept only small fixed-schema status records with bounded aggregate counters.

    :param value: Decoded private IPC message, still treated as untrusted input.
    :type value: object


    :return: Safe status without tokens, paths, identities, source text or exception payloads.
    :rtype: dict
    """

    # value — heartbeat несёт только безопасные агрегаты, не полный stats провайдера.
    keys = {"state", "reason", "time_reason", "failure", "counters"}
    if (type(value) is not dict or set(value) - {"journals"} != keys or type(value["state"]) is not str
            or value["state"] not in STATES):
        raise ValueError("invalid status")
    for key in ("reason", "time_reason"):
        if value[key] is not None and (type(value[key]) is not str or value[key] not in REASONS):
            raise ValueError("invalid status reason")
    if value["failure"] not in (None, "crash", "hang", "configuration", "authorization", "ownership", "storage"):
        raise ValueError("invalid failure")
    counters = value["counters"]
    if type(counters) is not dict or len(counters) > 8:
        raise ValueError("invalid counters")
    if any(key not in {"active", "destinations_busy", "pending", "unknown", "received", "submitted", "failures"}
           or type(number) is not int or not 0 <= number <= 2**63 - 1 for key, number in counters.items()):
        raise ValueError("invalid counter")
    result = dict(value, counters=dict(counters))
    if "journals" in value:
        journals = value["journals"]
        if (type(journals) is not dict or set(journals) != {"hub", "sources"}
                or type(journals["sources"]) is not list or len(journals["sources"]) > 8):
            raise ValueError("invalid journals")
        result["journals"] = {"hub": validate_status(journals["hub"]),
                              "sources": [validate_status(item) for item in journals["sources"]]}
    return result
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Чтение полного ответа в пределах установленного размера
#------------------------------------------------------------------------------------------------------------------
async def read_limited(
    stream: object,
    limit: int,
    ) -> bytes:

    """
    Read through EOF without assuming one TCP read contains an entire response.

    :param stream: aiohttp response body stream.
    :type stream: object

    :param limit: Maximum accepted payload size.
    :type limit: int

    :return: Complete bounded response bytes.
    :rtype: bytes
    """

    # stream/limit — лимит действует и при chunked transfer, без Content-Length.
    try:
        await stream.readexactly(limit + 1)
    except asyncio.IncompleteReadError as error:
        return error.partial
    raise ValueError("response too large")
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль messages не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
