# Понятные владельцу сообщения о сбоях gateway без приватных подробностей.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261015-111157
#
# Функции:
# -> operator_notice(): Описание сбоя или восстановления для мессенджера.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

from remote_watch.commands.source_health import validate_source_health
from remote_watch.gateway_server.messages import REASONS, STATES

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************

COMPONENTS = {"commands": "Command channel", "notifications": "Notification channel",
              "caddy": "HTTPS server", "manager": "Gateway supervisor"}
REASON_TEXT = {
    "source_unavailable": "A messenger command source is temporarily unavailable.",
    "time_unavailable": "The trusted time service is unavailable; command admission is paused.",
    "source_stopped": "A messenger command source has stopped.",
    "source_denied": "The messenger refused access; check the bot or topic permissions.",
    "authorization": "Access was refused; check credentials and permissions.",
    "configuration": "The component could not use its configuration.",
    "storage": "The component could not use its local command journal.",
    "storage_unavailable": "The local command journal is unavailable.",
    "storage_capacity": "The command journal has reached its capacity limit.",
    "maintenance_failed": "Command journal maintenance could not finish.",
    "logging_failed": "The gateway could not write its operational log.",
    "tray_unavailable": "The operator tray is unavailable; this does not stop the service.",
    "ownership": "The component could not acquire exclusive ownership of its resources.",
    "crash": "The component process exited unexpectedly.",
    "hang": "The component stopped responding to the supervisor.",
    "heartbeat_timeout": "The supervisor is no longer receiving component heartbeats.",
    "restart_limit": "The automatic restart limit has been reached; operator action is required.",
    "startup_timeout": "The component did not become ready within the startup deadline.",
    "shutdown_timeout": "The component did not stop within the shutdown deadline.",
    "caddy_unavailable": "The HTTPS server is not responding.",
    "control_unavailable": "The local gateway control interface is unavailable.",
}
ERROR_TEXT = {"dns": "DNS lookup failed", "timeout": "the request timed out",
              "connection": "the connection failed or was interrupted", "network": "the network request failed",
              "tls_certificate": "TLS certificate verification failed",
              "tls_handshake": "the TLS handshake failed"}
STAGE_TEXT = {"provider_poll": "receiving commands", "provider_reply": "sending a command reply",
              "provider_ack": "acknowledging a messenger event", "storage": "accessing the local command journal",
              "hub": "exchanging command state with the hub"}

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Описание сбоя или восстановления для мессенджера
#------------------------------------------------------------------------------------------------------------------
def operator_notice(
    name: str,
    state: str,
    reason: str | None,
    *,
    sources: list,
    duration: int | None = None,
    ) -> str:

    """
    Explain a bounded gateway transition in English without raw provider or exception text.

    :param name: Fixed supervisor component name.
    :type name: str

    :param state: Current sanitized component state.
    :type state: str

    :param reason: Fixed supervisor reason, or None after recovery.
    :type reason: str | None

    :param sources: Validated source diagnostics; historical incident details on recovery.
    :type sources: list

    :param duration: Observed interruption duration on a monotonic clock, in seconds.
    :type duration: int | None

    :return: Human-readable alert with a separate machine diagnostic code.
    :rtype: str
    """

    # name/state/reason — только известные коды; сообщения провайдера не переводим и не цитируем.
    # sources — номера из порядка конфигурации, а не chat_id, имена приложений или токены.
    # duration — наблюдённая длительность, не разность часов двух виртуальных машин.

    if name not in COMPONENTS or state not in STATES or (reason is not None and reason not in REASONS):
        raise ValueError("invalid operator transition")
    sources = validate_source_health(sources)

    title = COMPONENTS[name]
    if state == "ready":
        lines = ["Remote Watch gateway", f"{title} is ready again."]
        if type(duration) is int and 0 <= duration <= 2**63 - 1:
            minutes, seconds = divmod(duration, 60)
            lines.append(f"Observed interruption: {minutes} min {seconds} sec.")
    else:
        label = {"degraded": "is temporarily unavailable", "failed": "has stopped",
                 "restarting": "is restarting"}.get(state, "needs attention")
        lines = ["Remote Watch gateway", f"{title} {label}."]
        lines.append(REASON_TEXT.get(reason, "The component needs attention; see the local gateway log."))

    for source in sources:
        faults = [source[key] for key in ("receive", "reply") if source[key] is not None]
        for fault in faults:
            explanation = ERROR_TEXT.get(fault["error_kind"])
            if explanation is None:
                explanation = ("the messenger asked to slow down" if fault["http_status"] == 429 else
                    "the service returned an error" if fault["http_status"] else
                    "the operation is temporarily busy" if fault["code"] == "busy" else
                    "the operation could not finish")
            line = (f"{source['provider'].title()} source #{source['index']}, "
                    f"{STAGE_TEXT[fault['stage']]}: {explanation}")
            if fault["http_status"] is not None:
                line += f" (HTTP {fault['http_status']})"
            if fault["verify_code"] is not None:
                line += f" (TLS verification code {fault['verify_code']})"
            lines.append(line + ".")

    if state in {"degraded", "restarting"} and reason not in {
            "authorization", "configuration", "ownership", "storage", "restart_limit", "source_stopped"}:
        lines.append("Automatic recovery attempts continue.")
    if state == "restarting" and name == "commands":
        lines.append("Command clients will attempt to reconnect; executed callbacks are not replayed.")
    if reason is not None:
        lines.append(f"Diagnostic: {reason}.")
    return "\n".join(lines)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль presentation не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
