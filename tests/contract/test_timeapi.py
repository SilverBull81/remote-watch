# Проверки TimeAPI через локальный TLS: формат UTC, границы ответа и отказ при сбое.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-114053
#
# Тесты:
# -> test_timeapi_tls(): Настоящий TLS и обработка ответов API.
# -> test_timeapi_settings(): Отказ от небезопасных настроек до открытия соединения.
# -> test_timeapi_probe(): Код возврата диагностики и безопасный вывод при ошибке.
# -> test_timeapi_fraction(): Дробные секунды API, включая формат для Python 3.10.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import ssl
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
from aiohttp import web
from test_gateway_tls import certificates

from remote_watch.adapters.timeapi import TimeApiTimeSource, _parse_time
from remote_watch.commands.time import TimeSample, TimeUnavailable, TrustedClock
from remote_watch.diagnostics import time_probe


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Проверка времени через локальный HTTPS и обработка недопустимых ответов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", [
    "ok", "cached", "redirect", "untrusted", "wrong_name", "expired", "timeout",
    "disabled_verification", "wrong_zone", "dst", "mismatch", "bool_number", "bad_date",
    "duplicate", "oversized", "not_json", "compressed", "status", "cancel", "concurrent",
])
def test_timeapi_tls(
    tmp_path: Path,
    case: str,
) -> None:

    """Exercise the real TLS transport and strict TimeAPI response validation.

    :param tmp_path: Temporary certificate directory.
    :type tmp_path: Path

    :param case: Success, protocol failure or cancellation scenario.
    :type case: str
    """

    # tmp_path — временные сертификаты, не добавляемые в хранилище ОС.
    # case — выбранное нарушение ответа или проверяемый режим запроса.

    ca, cert, key = certificates(tmp_path, case)
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(cert, key)
    client_ssl = (
        ssl.create_default_context() if case == "untrusted" else ssl.create_default_context(cafile=str(ca))
    )

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Работа локального сервера и библиотечного клиента в одном цикле
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Serve synthetic UTC without using real provider credentials or public networking."""

        seen = []
        entered = asyncio.Event()
        release = asyncio.Event()

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставной ответ TimeAPI
        #----------------------------------------------------------------------------------------------------------
        async def handler(request: web.Request) -> web.Response:

            """Return one controlled TimeAPI response.

            :param request: Incoming HTTPS request to the test server.
            :type request: web.Request

            :return: Selected synthetic API response.
            :rtype: web.Response
            """

            # request — запрос, параметры которого проверяются до формирования ответа.

            assert request.method == "GET"
            assert request.query["timeZone"] == "UTC"
            assert "no-cache" in request.headers["Cache-Control"]
            seen.append(request.query["remote_watch_nonce"])
            entered.set()
            if case in ("cancel", "concurrent"):
                await release.wait()
            if case == "timeout":
                await asyncio.sleep(0.15)

            payload = {
                "year": 2026, "month": 9, "day": 30, "hour": 12, "minute": 0,
                "seconds": 0, "milliSeconds": 123,
                "dateTime": "2026-09-30T12:00:00.1234567", "timeZone": "UTC", "dstActive": False,
            }
            changes = {
                "wrong_zone": ("timeZone", "Europe/Moscow"), "dst": ("dstActive", True),
                "mismatch": ("seconds", 1), "bool_number": ("minute", False),
                "bad_date": ("dateTime", "2026-09-31T12:00:00"),
            }
            if case in changes:
                key, value = changes[case]
                payload[key] = value

            body = json.dumps(payload)
            if case == "duplicate":
                body = body[:-1] + ', "timeZone": "UTC"}'
            if case == "oversized":
                body += " " * 16384
            headers = {}
            if case == "cached":
                headers["Age"] = "0"
            if case == "redirect":
                headers["Location"] = "/another-path"
            if case == "compressed":
                headers["Content-Encoding"] = "gzip"
            status = 302 if case == "redirect" else 503 if case == "status" else 200
            return web.Response(
                text=body, headers=headers, status=status,
                content_type="text/plain" if case == "not_json" else "application/json",
            )
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_get("/time", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        source = TimeApiTimeSource(
            endpoint=f"https://127.0.0.1:{port}/time", ssl_context=client_ssl,
            timeout=0.05 if case == "timeout" else 5,
        )
        if case == "disabled_verification":
            client_ssl.check_hostname = False
            client_ssl.verify_mode = ssl.CERT_NONE

        try:
            if case in ("cancel", "concurrent"):
                task = asyncio.create_task(source.sample())
                await asyncio.wait_for(entered.wait(), timeout=3)
                if case == "concurrent":
                    with pytest.raises(TimeUnavailable):
                        await source.sample()
                    release.set()
                    await task
                else:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    release.set()
                # Завершение и отмена запроса не оставляют объект навсегда занятым.
                await source.sample()
            elif case == "ok":
                first = await source.sample()
                assert first.lower_utc == pytest.approx(1790769599.123456, abs=0.000001, rel=0)
                assert 2 <= first.upper_utc - first.lower_utc < 5
                clock = TrustedClock()
                clock.install(first)
                assert clock.check(1790769600, "a" * 32).reason is None
                assert clock.check(1790769400, "a" * 32).reason.value == "too_old"
                await source.sample()
                assert len(seen) == 2 and seen[0] != seen[1]
            else:
                with pytest.raises(TimeUnavailable) as caught:
                    await source.sample()
                assert str(caught.value) == "trusted time unavailable"
                assert len(seen) <= 1
        finally:
            release.set()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет небезопасных настроек источника
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("settings", [
    {"endpoint": "http://host/time"}, {"endpoint": "https://user:pass@host/time"},
    {"endpoint": "https://host/time?q=secret"}, {"accuracy": -1}, {"accuracy": float("nan")},
    {"accuracy": 6}, {"timeout": 0}, {"timeout": 31}, {"clock": None},
])
def test_timeapi_settings(settings: dict[str, object]) -> None:

    """Reject unsafe settings without making a network request.

    :param settings: Constructor arguments containing one invalid value.
    :type settings: dict[str, object]
    """

    # settings — набор параметров с одним недопустимым значением.

    with pytest.raises((TypeError, ValueError)):
        TimeApiTimeSource(**settings)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Конечная диагностика и безопасный отказ
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("failed", [False, True])
def test_timeapi_probe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed: bool,
) -> None:

    """Verify CLI exit codes and output using a deterministic source.

    :param monkeypatch: Dependency replacement fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured console output.
    :type capsys: pytest.CaptureFixture[str]

    :param failed: Whether the synthetic source must fail.
    :type failed: bool
    """

    # monkeypatch — подмена сетевого запроса без изменения самого CLI.
    # capsys — перехват печатаемого JSON.
    # failed — выбран успешный ответ либо ошибка получения времени.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Подставное показание времени для проверки CLI
    #--------------------------------------------------------------------------------------------------------------
    async def sample(self: TimeApiTimeSource) -> TimeSample:

        """Return a bounded interval or a private synthetic error.

        :return: Synthetic UTC bounds tied to the source monotonic clock.
        :rtype: TimeSample
        """

        if failed:
            raise TimeUnavailable("private response details")
        return TimeSample(lower_utc=1000, upper_utc=1002, observed_at=self._clock())
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(TimeApiTimeSource, "sample", sample)
    assert time_probe.main(["--samples", "1"]) == int(failed)
    row = json.loads(capsys.readouterr().out)
    assert row["status"] == ("time_unavailable" if failed else "accepted")
    assert "private" not in json.dumps(row)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Разная точность времени API без зависимости от версии fromisoformat
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fraction", ["", "1", "12", "123", "1234", "12345", "123456", "1234567"])
def test_timeapi_fraction(fraction: str) -> None:

    """Preserve decimal precision while accepting provider timestamps on Python 3.10.

    :param fraction: Fractional-second digits, including an absent fraction.
    :type fraction: str
    """

    # fraction — от нуля до семи десятичных знаков после целой секунды.

    micros = int((fraction + "000000")[:6])
    payload = {
        "year": 2026, "month": 9, "day": 30, "hour": 12, "minute": 0, "seconds": 0,
        "milliSeconds": micros // 1000, "timeZone": "UTC", "dstActive": False,
        "dateTime": "2026-09-30T12:00:00" + ("." + fraction if fraction else ""),
    }
    stamp, resolution = _parse_time(json.dumps(payload).encode("utf-8"))
    assert stamp == pytest.approx(1790769600 + micros / 1e6, abs=0.000001, rel=0)
    assert resolution == 10 ** -min(len(fraction), 6)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.contract.test_timeapi не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
