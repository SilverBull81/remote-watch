# Проверки причин отказов источников и понятных служебных оповещений.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261015-111157
#
# Функции:
# -> source_health(): Синтетические состояния двух разных Telegram-источников.
#
# Тесты:
# -> test_transport_categories(): Различение сетевых причин без текста исключения.
#    Вложенные объявления:
#    -> scenario(): Один подставной HTTP-вызов с освобождением слота.
# -> test_http_status(): Сохранение HTTP-статуса и различение локальной занятости.
#    Вложенные объявления:
#    -> scenario(): Подставной ответ провайдера без сетевых обращений.
# -> test_source_privacy(): Отказ от произвольных полей на границе IPC.
# -> test_transition_history(): История сохраняется, повторы счётчиков не засоряют журнал.
# -> test_owner_notice(): Понятные сообщения и длительность с сохранением cooldown.
# -> test_doctor_source_health(): Безопасная причина доступна в серверном отчёте doctor.
# -> test_component_alert(): Общий degraded не дублирует сообщение компонента.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import copy
import json
import socket
import ssl
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from remote_watch.adapters._source_http import SourceHttp
from remote_watch.commands.source_health import source_failure, validate_source_health
from remote_watch.commands.transport import CommandError
from remote_watch.diagnostics.gateway_doctor import _server_online
from remote_watch.gateway_server.application import ApplicationConfig, OperatorAlerts
from remote_watch.gateway_server.manager import GatewayManager
from remote_watch.gateway_server.messages import status_message
from remote_watch.notifications._context import delivery_context

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Синтетические состояния двух разных Telegram-источников
#------------------------------------------------------------------------------------------------------------------
def source_health() -> list[dict]:

    """
    Build independent snapshots with only the second configured reader failing.

    :return: One healthy source and one failed Telegram poller.
    :rtype: list[dict]
    """

    healthy = {"index": 1, "provider": "telegram", "ready": True, "receive": None,
               "reply": None, "last_failure": None, "failures": 0, "recoveries": 0}
    fault = source_failure(CommandError("unavailable", error_kind="dns"), "provider_poll")
    failed = dict(healthy, index=2, ready=False, receive=fault, last_failure=fault, failures=1)
    return [healthy, failed]
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Различение сетевых причин без текста исключения
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case,kind", [("dns", "dns"), ("timeout", "timeout"), ("connection", "connection"),
    ("tls", "tls_handshake"), ("certificate", "tls_certificate"), ("wrapped_certificate", "tls_certificate"),
    ("wrapped_tls", "tls_handshake"), ("other", "network")])
def test_transport_categories(
    case: str,
    kind: str,
    ) -> None:

    """
    Preserve safe native and wrapped failure categories without private exception details.

    :param case: Synthetic native or aiohttp failure variant.
    :type case: str

    :param kind: Expected fixed transport category.
    :type kind: str
    """

    # case/kind — исключения намеренно содержат маркер секрета; сеть не используется.

    aiohttp = pytest.importorskip("aiohttp")
    native = ssl.SSLCertVerificationError("private-token-and-host")
    native.verify_code = 10
    key = SimpleNamespace(host="private-token-and-host", port=443, ssl=True)
    variants = {"dns": socket.gaierror(-2, "private-token-and-host"),
                "timeout": asyncio.TimeoutError("private-token-and-host"),
                "connection": ConnectionResetError("private-token-and-host"),
                "tls": ssl.SSLError("private-token-and-host"), "certificate": native,
                "wrapped_certificate": aiohttp.ClientConnectorCertificateError(key, native),
                "wrapped_tls": aiohttp.ClientConnectorSSLError(key, ssl.SSLError("private-token-and-host")),
                "other": RuntimeError("private-token-and-host")}

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Один подставной HTTP-вызов с освобождением слота
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Execute the failing source call and verify privacy and internal context restoration.
        """

        transport = SourceHttp("https://example.invalid")
        transport._client = SimpleNamespace(request=Mock(side_effect=variants[case]))
        with pytest.raises(CommandError) as captured:
            await transport.call("POST", "/bot-private-token/getUpdates")
        error = captured.value
        assert error.code == "unavailable" and error.error_kind == kind
        assert error.verify_code == (10 if kind == "tls_certificate" else None)
        assert error.tls_reason == ("certificate_expired" if kind == "tls_certificate" else
                                    "handshake_failed" if kind == "tls_handshake" else None)
        assert "private-token-and-host" not in str(error)
        assert transport._active == 0 and delivery_context.get() is False
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение HTTP-статуса и различение локальной занятости
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,code", [(429, "busy"), (401, "denied"), (403, "denied"),
                                        (409, "conflict"), (503, "unavailable")])
def test_http_status(
    status: int,
    code: str,
    ) -> None:

    """
    Distinguish provider throttling, server errors and the local two-request gate.

    :param status: Synthetic provider HTTP status.
    :type status: int

    :param code: Expected stable command error code.
    :type code: str
    """

    # status/code — JSON содержит лишний приватный текст; сохраняется только число статуса.

    pytest.importorskip("aiohttp")

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Подставной ответ провайдера без сетевых обращений
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Return one synthetic HTTP error and inspect only safe classification fields.
        """

        body = b'{"description":"private-token-and-host","parameters":{"retry_after":2}}'
        response = SimpleNamespace(status=status, headers={},
            content=SimpleNamespace(read=AsyncMock(return_value=body), at_eof=Mock(return_value=True)))
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        transport = SourceHttp("https://example.invalid")
        transport._client = SimpleNamespace(request=Mock(return_value=context))
        with pytest.raises(CommandError) as captured:
            await transport.call("POST", "/synthetic")
        assert captured.value.code == code and captured.value.http_status == status
        assert "private-token-and-host" not in str(captured.value)
        transport._blocked_until = monotonic() + 60
        with pytest.raises(CommandError) as captured:
            await transport.call("POST", "/synthetic")
        assert captured.value.http_status == 429
        transport._active = 2
        with pytest.raises(CommandError) as captured:
            await transport.call("POST", "/synthetic")
        assert captured.value.http_status is None and captured.value.busy_reason == "transport_requests_full"
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ от произвольных полей на границе IPC
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["extra", "provider", "index", "bool_index", "stage", "code", "kind",
                                 "status", "bool_status", "tls", "negative", "many"])
def test_source_privacy(
    case: str,
    ) -> None:

    """
    Reject malformed diagnostic messages before they reach manager state, logs or owner alerts.

    :param case: Untrusted diagnostic field mutation.
    :type case: str
    """

    # case — номера и HTTP-статусы также проверяются по типу: bool не является допустимым int.

    sources = source_health()
    row = sources[1]
    fault = row["receive"]
    if case == "extra":
        row["secret"] = "private-token-and-host"
    elif case in {"provider", "index"}:
        row[case] = "private-token-and-host"
    elif case == "bool_index":
        sources[0]["index"] = True
    elif case in {"stage", "code"}:
        fault[case] = "private-token-and-host"
    elif case == "kind":
        fault["error_kind"] = "private-token-and-host"
    elif case in {"status", "bool_status"}:
        fault["http_status"] = 999 if case == "status" else True
    elif case == "tls":
        fault["tls_reason"] = "private-token-and-host"
    elif case == "negative":
        row["failures"] = -1
    else:
        sources *= 5

    message = {"state": "degraded", "reason": "source_unavailable", "time_reason": None,
               "failure": None, "counters": {}, "source_health": sources}
    with pytest.raises(ValueError):
        status_message(message)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : История сохраняется, повторы счётчиков не засоряют журнал
#------------------------------------------------------------------------------------------------------------------
def test_transition_history() -> None:

    """
    Preserve incident evidence after recovery without logging each failed retry or exposing mutable aliases.
    """

    sources = source_health()
    safe = validate_source_health(sources)
    sources[1]["receive"]["code"] = "private-token-and-host"
    assert safe[1]["receive"]["code"] == "unavailable"
    manager = object.__new__(GatewayManager)
    component = {"state": "degraded", "source_health": safe}
    first = manager._log_component_status(component)
    safe[1]["failures"] += 5
    assert first == manager._log_component_status(component)
    safe[1].update(ready=True, receive=None, recoveries=1)
    recovered = manager._log_component_status(dict(component, state="ready"))
    assert recovered != first
    assert recovered["source_health"][1]["last_failure"]["error_kind"] == "dns"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Понятные сообщения и длительность с сохранением cooldown
#------------------------------------------------------------------------------------------------------------------
def test_owner_notice(
    caplog: pytest.LogCaptureFixture,
    ) -> None:

    """
    Explain the second reader failure, coalesce retries and report monotonic recovery duration.

    :param caplog: Local handler capturing synthetic operator notices only.
    :type caplog: pytest.LogCaptureFixture
    """

    # caplog — только локальные сообщения без RemoteWatcher.start и без реальной отправки.

    alerts = OperatorAlerts(ApplicationConfig(cooldown=60))
    alerts.logger.addHandler(caplog.handler)
    sources = source_health()
    alerts.observe("commands", "degraded", "source_unavailable", 0, sources=sources)
    assert len(caplog.records) == 1
    text = caplog.records[-1].getMessage()
    assert "Command channel is temporarily unavailable" in text and "Telegram source #2" in text
    assert "DNS lookup failed" in text and "Automatic recovery attempts continue" in text
    sources[1]["failures"] += 5
    alerts.observe("commands", "degraded", "source_unavailable", 30, sources=sources)
    assert len(caplog.records) == 1
    sources[1].update(ready=True, receive=None, recoveries=1)
    alerts.observe("commands", "ready", None, 102, sources=sources)
    assert len(caplog.records) == 2
    assert "ready again" in caplog.records[-1].getMessage()
    assert "1 min 42 sec" in caplog.records[-1].getMessage()
    bad = copy.deepcopy(sources)
    bad[1]["secret"] = "private-token-and-host"
    alerts.observe("commands", "degraded", "source_unavailable", 200, sources=bad)
    assert len(caplog.records) == 2 and "private-token-and-host" not in json.dumps([r.msg for r in caplog.records])
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасная причина доступна в серверном отчёте doctor
#------------------------------------------------------------------------------------------------------------------
def test_doctor_source_health(
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """
    Include validated source details in online doctor without contacting providers or opening journals.

    :param monkeypatch: Substitute only the private manager status operation.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # monkeypatch — источники подставные, а пути TCP-компонентов отключены.

    snapshot = {"state": "degraded", "components": {"commands": {
        "state": "degraded", "reason": "source_unavailable", "source_health": source_health()}}}
    monkeypatch.setattr("remote_watch.gateway_server.manager.control_request", AsyncMock(return_value=snapshot))
    rows = asyncio.run(_server_online(SimpleNamespace(commands=None, notifications=None), .1))
    sources = [row for row in rows if "source_health" in row]
    assert len(sources) == 2
    assert sources[0]["status"] == "ok" and sources[1]["status"] == "failed"
    assert sources[1]["source_health"]["receive"]["error_kind"] == "dns"
    snapshot["components"]["commands"]["source_health"][1]["secret"] = "private-token-and-host"
    rows = asyncio.run(_server_online(SimpleNamespace(commands=None, notifications=None), .1))
    assert any(row["code"] == "source_health_invalid" for row in rows)
    assert "private-token-and-host" not in json.dumps(rows)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Общий degraded не дублирует сообщение компонента
#------------------------------------------------------------------------------------------------------------------
def test_component_alert() -> None:

    """
    Emit one component explanation instead of a second vague supervisor warning for the same failure.
    """

    manager = object.__new__(GatewayManager)
    component = {"state": "degraded", "reason": "source_unavailable", "source_health": source_health()}
    manager.state, manager.reason = "degraded", None
    manager.children = {"commands": SimpleNamespace(message=component)}
    manager._log_handler = manager._tray = manager._last_log = None
    manager._log, manager._alerts = Mock(), Mock()
    manager.status = Mock(return_value={"state": "degraded", "reason": None, "alerts": "enabled",
        "ui": "headless", "logging": "available", "components": {"commands": component}})
    manager._report()
    assert manager._alerts.observe.call_count == 1
    assert manager._alerts.observe.call_args.args[:3] == ("commands", "degraded", "source_unavailable")
    assert manager._alerts.observe.call_args.kwargs["sources"][1]["receive"]["error_kind"] == "dns"
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Запускайте тесты через pytest.")
#------------------------------------------------------------------------------------------------------------------
