# Конечная проверка DNS, TCP, TLS и read-only маршрута gateway.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Функции:
# -> network_checks(): Наблюдение ограниченной проверки без зависания на системном DNS
#
# -> _probe(): Сетевая работа с проверяемым TLS и без повторных POST
#
# -> _http(): Единственный запрос doctor и проверка схемы ответа


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import http.client
import json
import queue
import socket
import ssl
import threading
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from remote_watch._tls import tls_diagnostics
from remote_watch.gateway.doctor_protocol import _pairs, probe_response

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Наблюдение ограниченной проверки без зависания на системном DNS
#------------------------------------------------------------------------------------------------------------------
def network_checks(
    endpoint: str,
    context: ssl.SSLContext,
    component: str,
    token: str,
    body: bytes,
    timeout: float,
    ) -> list[dict[str, Any]]:

    """
    Run one bounded diagnostic exchange; never retry a POST or disable TLS verification.

    :param endpoint: Validated HTTPS origin without credentials or operation path.
    :type endpoint: str

    :param context: Verifying SSL context owned by this diagnostic invocation.
    :type context: ssl.SSLContext

    :param component: Fixed notifications or commands channel.
    :type component: str

    :param token: Resolved channel credential, never included in observations.
    :type token: str

    :param body: Bounded read-only identity and permission request.
    :type body: bytes

    :param timeout: Maximum observed time for each DNS, TCP, TLS or HTTP phase.
    :type timeout: float

    :return: Fixed diagnostic records without endpoint, identity, headers or body values.
    :rtype: list[dict[str, Any]]
    """

    # endpoint/context/component/token/body/timeout — только один явно заданный адрес, не сканирование.
    # Системный DNS нельзя надёжно прервать. Один daemon-поток позволяет завершить CLI
    # по сроку; после запоздалого DNS он увидит cancel и не начнёт TCP/HTTP.
    events: queue.Queue = queue.Queue()
    cancel = threading.Event()
    worker = threading.Thread(target=_probe, args=(endpoint, context, component, token, body, timeout,
                                                   events, cancel), daemon=True, name="gateway-doctor")
    worker.start()
    rows = []
    active_socket = None
    stage = "dns"
    deadline = monotonic() + timeout
    while True:
        try:
            kind, value = events.get(timeout=max(0, deadline - monotonic()))
        except queue.Empty:
            cancel.set()
            # Закрытие активного соединения прерывает и медленный ответ по одному байту.
            # Одного socket timeout недостаточно: он обновляется при каждом чтении.
            if active_socket is not None:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                active_socket.close()
            rows.append({"check": component + "." + stage, "status": "failed", "code": stage + "_timeout"})
            return rows
        if kind == "done":
            return rows
        if kind == "socket":
            active_socket = value
        elif kind == "stage":
            stage = value
            deadline = monotonic() + timeout
        else:
            rows.append(dict(value, check=component + "." + value["check"]))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Сетевая работа с проверяемым TLS и без повторных POST
#------------------------------------------------------------------------------------------------------------------
def _probe(
    endpoint: str,
    context: ssl.SSLContext,
    component: str,
    token: str,
    body: bytes,
    timeout: float,
    events: queue.Queue,
    cancel: threading.Event,
    ) -> None:

    """
    Resolve once, connect to at most four returned addresses, and keep the verified TLS socket.

    :param endpoint: Validated HTTPS origin.
    :type endpoint: str

    :param context: Mandatory verifying TLS context.
    :type context: ssl.SSLContext

    :param component: Fixed channel used to select the probe route.
    :type component: str

    :param token: Private bearer credential.
    :type token: str

    :param body: Serialized read-only request.
    :type body: bytes

    :param timeout: Finite per-phase time allowance.
    :type timeout: float

    :param events: Bounded-count observations consumed by the caller.
    :type events: queue.Queue

    :param cancel: Prevent further network operations after the caller times out.
    :type cancel: threading.Event
    """

    # endpoint/context/component/token/body — не появляются в событиях или исключениях CLI.
    # timeout/events/cancel — этапы наблюдаются отдельно; поздний поток не запускает следующий этап.
    connection = None
    stream = None
    stage = "dns"
    try:
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("verifying_context_required")
        origin = urlsplit(endpoint)
        addresses = socket.getaddrinfo(origin.hostname, origin.port or 443, type=socket.SOCK_STREAM)
        events.put(("row", {"check": "dns", "status": "ok", "code": "dns_ok"}))
        if cancel.is_set():
            return
        stage = "tcp"
        events.put(("stage", stage))
        deadline = monotonic() + timeout
        failure = OSError()
        for family, kind, protocol, _, address in addresses[:4]:
            if cancel.is_set():
                return
            if monotonic() >= deadline:
                raise TimeoutError()
            candidate = socket.socket(family, kind, protocol)
            try:
                candidate.settimeout(max(0.01, deadline - monotonic()))
                candidate.connect(address)
                stream = candidate
                break
            except OSError as error:
                candidate.close()
                failure = error
        if stream is None:
            raise failure
        events.put(("socket", stream))
        events.put(("row", {"check": "tcp", "status": "ok", "code": "tcp_ok"}))
        if cancel.is_set():
            return
        stage = "tls"
        events.put(("stage", stage))
        stream.settimeout(timeout)
        stream = context.wrap_socket(stream, server_hostname=origin.hostname, do_handshake_on_connect=False)
        events.put(("socket", stream))
        if cancel.is_set():
            return
        stream.do_handshake()
        events.put(("row", {"check": "tls", "status": "ok", "code": "tls_ok"}))
        if cancel.is_set():
            return
        stage = "http"
        events.put(("stage", stage))
        # HTTPConnection использует уже проверенный TLS socket: повторного DNS и
        # handshake нет, редиректы не выполняются, proxy из окружения не читается.
        connection = http.client.HTTPConnection(origin.hostname, origin.port or 443, timeout=timeout)
        connection.sock = stream
        for row in _http(connection, component, token, body):
            events.put(("row", row))
    except Exception as error:
        details = tls_diagnostics(error) if stage == "tls" else {}
        code = (details.get("error_kind") or "tls_handshake") if stage == "tls" else (
            stage + "_timeout" if isinstance(error, TimeoutError) else stage + "_unavailable")
        if isinstance(error, TimeoutError):
            code = stage + "_timeout"
        if stage == "tcp" and isinstance(error, ConnectionRefusedError):
            code = "tcp_refused"
        if stage == "http" and isinstance(error, (ValueError, http.client.HTTPException)):
            code = "response_invalid"
        events.put(("row", {"check": stage, "status": "failed", "code": code, **details}))
    finally:
        if connection is not None:
            connection.close()
        if stream is not None:
            stream.close()
        events.put(("done", None))
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНАЯ ФУНКЦИЯ : Единственный запрос doctor и проверка схемы ответа
#------------------------------------------------------------------------------------------------------------------
def _http(
    connection: http.client.HTTPConnection,
    component: str,
    token: str,
    body: bytes,
    ) -> list[dict[str, Any]]:

    """
    Interpret a small fixed-schema response without returning remote strings or following redirects.

    :param connection: HTTP connection already bound to a verified TLS socket.
    :type connection: http.client.HTTPConnection

    :param component: Expected channel marker and operation route.
    :type component: str

    :param token: Private credential sent only to the configured HTTPS origin.
    :type token: str

    :param body: Bounded probe payload.
    :type body: bytes

    :return: Safe route, authorization and readiness observations.
    :rtype: list[dict[str, Any]]
    """

    # connection/component/token/body — ошибочные HTML/JSON, заголовки и Location не выводятся.
    connection.request("POST", "/v1/" + component + "/probe", body=body,
                       headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                "Accept-Encoding": "identity", "Connection": "close"})
    response = connection.getresponse()
    status = response.status
    if status in (404, 405):
        return [{"check": "route", "status": "failed", "code": "route_missing", "http_status": status}]
    if 300 <= status < 400:
        return [{"check": "route", "status": "failed", "code": "redirect_rejected", "http_status": status}]
    if (response.getheader("X-Remote-Watch-Probe") != "1"
            or response.getheader("X-Remote-Watch-Component") != component):
        code = "upstream_unavailable" if status in (502, 503, 504) else "route_unverified"
        return [{"check": "route", "status": "failed", "code": code, "http_status": status}]
    rows = [{"check": "route", "status": "ok", "code": "route_ok", "http_status": status}]
    if status != 200:
        code = {401: "credential_denied", 403: "permission_denied", 429: "probe_busy"}.get(status, "probe_failed")
        return rows + [{"check": "authorization", "status": "failed", "code": code, "http_status": status}]
    if (response.getheader("Content-Type", "").split(";")[0].strip() != "application/json"
            or response.getheader("Content-Encoding") is not None):
        raise ValueError("probe_response")
    data = response.read(4097)
    if len(data) > 4096:
        raise ValueError("probe_response_size")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    if (type(value) is not dict or type(value.get("schema_version")) is not int
            or type(value.get("ready")) is not bool or value.get("authorized") is not True
            or value != probe_response(component, value)):
        raise ValueError("probe_response_schema")
    rows.append({"check": "authorization", "status": "ok", "code": "permissions_ok"})
    rows.append({"check": "readiness", "status": "ok" if value["ready"] else "failed",
                 "code": "ready" if value["ready"] else value["reason"]})
    if component == "commands":
        rows.append({"check": "trusted_time", "status": "ok" if value["time_ready"] else "failed",
                     "code": "time_ready" if value["time_ready"] else "time_unavailable"})
        rows.append({"check": "application_session", "status": "ok" if value["session_present"] else "warning",
                     "code": "session_present" if value["session_present"] else "application_not_registered"})
    return rows
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль _doctor_network не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
