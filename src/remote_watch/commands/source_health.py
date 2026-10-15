# Безопасные причины отказов источников команд для локального состояния gateway.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261015-111157
#
# Функции:
# -> source_failure(): Снимок разрешённых признаков одного отказа.
# -> validate_source_health(): Проверка ограниченного списка источников на границе IPC.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

from remote_watch.commands.transport import CommandError

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

SOURCE_STAGES = frozenset({"storage", "hub", "provider_poll", "provider_reply", "provider_ack"})
FAULT_FIELDS = frozenset({"stage", "code", "http_status", "error_kind", "verify_code",
                          "tls_reason", "busy_reason"})

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Снимок разрешённых признаков одного отказа
#------------------------------------------------------------------------------------------------------------------
def source_failure(
    error: CommandError,
    stage: str,
    ) -> dict:

    """
    Copy only allowlisted classification fields, never exception text or provider payloads.

    :param error: Command failure raised by a local operation.
    :type error: CommandError

    :param stage: Fixed operation stage assigned by the source runner.
    :type stage: str

    :return: Independent bounded failure description.
    :rtype: dict
    """

    # error — повторная проверка полей защищает и от стороннего provider adapter.
    # stage — выбран кодом runner, а не получен из сообщения провайдера.

    if type(stage) is not str or stage not in SOURCE_STAGES:
        raise ValueError("invalid source stage")

    safe = CommandError(error.code, http_status=error.http_status, error_kind=error.error_kind,
                        verify_code=error.verify_code, busy_reason=error.busy_reason)
    return {"stage": stage, "code": safe.code, "http_status": safe.http_status,
            "error_kind": safe.error_kind, "verify_code": safe.verify_code,
            "tls_reason": safe.tls_reason, "busy_reason": safe.busy_reason}
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка ограниченного списка источников на границе IPC
#------------------------------------------------------------------------------------------------------------------
def validate_source_health(
    value: object,
    ) -> list[dict]:

    """
    Validate source indices, provider kinds and fixed failure fields before retaining child status.

    :param value: Untrusted optional child diagnostic list.
    :type value: object

    :return: Independent safe source snapshots in configuration order.
    :rtype: list[dict]
    """

    # value — максимум восемь источников, без source_id, чатов, URL и токенов.
    # Номер соответствует порядку sources в конфиге и различает двух Telegram-ботов.

    if type(value) is not list or len(value) > 8:
        raise ValueError("invalid source health")

    result = []
    fields = {"index", "provider", "ready", "receive", "reply", "last_failure", "failures", "recoveries"}
    for index, item in enumerate(value, 1):
        if (type(item) is not dict or set(item) != fields or type(item["index"]) is not int
                or item["index"] != index or type(item["provider"]) is not str
                or item["provider"] not in {"telegram", "ntfy"} or type(item["ready"]) is not bool):
            raise ValueError("invalid source health")

        if any(type(item[key]) is not int or not 0 <= item[key] <= 2**63 - 1
               for key in ("failures", "recoveries")):
            raise ValueError("invalid source counters")

        copied = dict(item)
        for key in ("receive", "reply", "last_failure"):
            fault = item[key]
            if fault is None:
                continue
            if type(fault) is not dict or set(fault) != FAULT_FIELDS or type(fault["code"]) is not str:
                raise ValueError("invalid source failure")
            safe = source_failure(CommandError(fault["code"], http_status=fault["http_status"],
                error_kind=fault["error_kind"], verify_code=fault["verify_code"],
                busy_reason=fault["busy_reason"]), fault["stage"])
            if any(type(fault[name]) is not type(safe[name]) or fault[name] != safe[name] for name in safe):
                raise ValueError("invalid source failure")
            copied[key] = safe
        result.append(copied)

    return result
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль source_health не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
