# Проверка резервирования UTC, конечных ожиданий и сохранения действительного показания.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261125-102159
#
# Классы:
# -> StubSource: Управляемый источник без сетевых запросов.
#    Конструктор:
#    -> __init__(): Выбор сценария отказа или успешного ответа.
#    Интерфейс:
#    -> sample(): Одно показание либо заранее выбранная ошибка.
#
# Тесты:
# -> test_redundant_samples(): Резервирование, конфликт, погрешность и диагностика.
# -> test_refresh_anchor_policy(): Сохранение только действительного показания при временном отказе.
# -> test_time_settings(): Строгие настройки конечного набора источников.
# -> test_utctime_payload(): Согласованность полей независимого UTC API.
# -> test_time_health_rejects_private_fields(): Отказ от произвольных данных в IPC.
# -> test_cancel_refresh(): Отмена не оставляет фоновых запросов и не продлевает старое показание.
#    -> scenario(): Отмена двух одновременно ожидающих источников.
# -> test_refresh_backoff(): Паузы 15/30/60 и возврат к обычному периоду после успеха.
#    -> scenario(): Управляемый монотонный отсчёт без реального ожидания.
#       -> tick(): Продвижение локального времени на одну итерацию.
#       -> update(): Последовательность отказов и восстановления.
#       -> sweep(): Подставное обслуживание без журнала и сети.
# -> test_doctor_time_health(): Отказ резерва виден как warning, без ложного отказа всего канала.
#    -> snapshot(): Только синтетическое состояние управляющего процесса.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import json
from time import monotonic
from types import SimpleNamespace

import pytest

from remote_watch.adapters.trusted_time import RedundantTimeSource, parse_time_sources, validate_time_health
from remote_watch.adapters.utctime import UtcTimeSource
from remote_watch.commands.hub import CommandHub
from remote_watch.commands.time import TimeSample, TimeUnavailable, TrustedClock
from remote_watch.diagnostics.gateway_doctor import _server_online
from remote_watch.gateway_server.messages import status_message

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Управляемый источник без сетевых запросов
#------------------------------------------------------------------------------------------------------------------
class StubSource:
    """
    Simulate provider failures and delay without sockets or production settings.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        mode: str,
        ) -> None:

        """
        Select one deterministic behavior.

        :param mode: Fixed sample or failure scenario.
        :type mode: str
        """

        # mode — только синтетические сценарии, не адреса внешних серверов.

        self.mode = mode
        self.active = False
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одно показание либо заранее выбранная ошибка
    #--------------------------------------------------------------------------------------------------------------
    async def sample(
        self,
        ) -> TimeSample:

        """
        Return bounded UTC or a sanitized synthetic failure.

        :return: Time interval anchored to the current monotonic instant.
        :rtype: TimeSample
        """

        self.active = True
        try:
            if self.mode == "hang":
                await asyncio.sleep(60)
            if self.mode == "timeout":
                raise TimeUnavailable(reason="timeout", transient=True)
            if self.mode == "http":
                raise TimeUnavailable(reason="http_status", http_status=503, transient=True)
            if self.mode == "tls":
                raise TimeUnavailable(reason="tls_certificate", verify_code=10)
            if self.mode == "invalid":
                raise ValueError("private response")
            now = monotonic()
            offset = 20 if self.mode == "ahead" else 0
            width = 6 if self.mode == "wide" else 0.1
            return TimeSample(lower_utc=1000 + now + offset, upper_utc=1000 + now + offset + width,
                              observed_at=now)
        finally:
            self.active = False
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ТЕСТЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Резервирование, конфликт, погрешность и диагностика
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("first,second,selected,error", [
    ("timeout", "ok", 2, None), ("ok", "timeout", 1, None), ("ok", "ok", 1, None),
    ("wide", "ok", 2, None), ("tls", "ok", 2, None), ("invalid", "ok", 2, None),
    ("ok", "ahead", None, "source_conflict"), ("timeout", "http", None, "source_failed"),
    ("wide", "wide", None, "source_failed"),
])
def test_redundant_samples(
    first: str,
    second: str,
    selected: int | None,
    error: str | None,
    ) -> None:

    """
    Distinguish a usable fallback, complete outage and incompatible trusted origins.

    :param first: First synthetic source behavior.
    :type first: str

    :param second: Second synthetic source behavior.
    :type second: str

    :param selected: Expected one-based chosen provider.
    :type selected: int | None

    :param error: Expected sanitized failure category.
    :type error: str | None
    """

    # first — основной источник.
    # second — независимый резерв.
    # selected — выбор определяется настройками, а не порядком завершения задач.
    # error — конфликт не разрешается усреднением или голосованием.

    source = RedundantTimeSource()
    source._sources = (StubSource(first), StubSource(second))
    if error is None:
        sample = asyncio.run(source.sample())
        clock = TrustedClock()
        clock.install(sample)
        assert clock.bounds().upper_utc - clock.bounds().lower_utc < 1
    else:
        with pytest.raises(TimeUnavailable) as caught:
            asyncio.run(source.sample())
        assert caught.value.reason == error
        assert "private" not in str(caught.value)
    health = source.stats()
    assert health["selected"] == selected
    assert len(health["sources"]) == 2
    assert all(not item.active for item in source._sources)
    assert "private" not in json.dumps(health)
    if first == "tls":
        assert health["sources"][0]["verify_code"] == 10
    health["sources"].clear()
    assert len(source.stats()["sources"]) == 2
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сохранение только действительного показания при временном отказе
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,retained", [("timeout", True), ("http", True), ("tls", False), ("invalid", False)])
def test_refresh_anchor_policy(
    mode: str,
    retained: bool,
    ) -> None:

    """
    Keep original deadlines and drift after transport failure, rejecting permanent failures.

    :param mode: Synthetic refresh failure.
    :type mode: str

    :param retained: Whether the old, still-valid sample may survive.
    :type retained: bool
    """

    # mode — ошибка сети отличается от недостоверного ответа или сертификата.
    # retained — сохранение не обновляет observed_at и не продлевает sample_ttl.

    now = [100.0]
    clock = TrustedClock(clock=lambda: now[0])
    clock.install(TimeSample(lower_utc=1000, upper_utc=1001, observed_at=100))
    before = clock.check(1000, "a" * 32).deadline
    now[0] = 110
    with pytest.raises(TimeUnavailable):
        asyncio.run(clock.refresh(StubSource(mode)))
    assert clock.refresh_error is not None
    if retained:
        assert clock.bounds().lower_utc > 1009
        after = clock.check(1000, "a" * 32).deadline
        assert after.remaining(110, "a" * 32) < before.remaining(100, "a" * 32)
        now[0] = 400
    with pytest.raises(TimeUnavailable):
        clock.bounds()
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Строгие настройки конечного набора источников
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [
    {}, {"sources": []}, {"sources": [{}]}, {"sources": [{"provider": "unknown"}]},
    {"sources": [{"provider": "utctime", "timeout": 30}]},
    {"sources": [{"provider": "utctime", "accuracy": True}]},
    {"sources": [{"provider": "utctime", "endpoint": "http://unsafe"}]},
    {"sources": [{"provider": "utctime", "endpoint": "https://user:secret@host"}]},
    {"sources": [{"provider": "https_date", "endpoint": "https://host"}]},
    {"sources": [{"provider": "utctime"}] * 2}, {"sources": [{"provider": "utctime"}] * 5},
    {"sources": [{"provider": "utctime", "secret": "private"}]},
])
def test_time_settings(
    value: object,
    ) -> None:

    """
    Reject ambiguous trust, excessive request budgets and unknown configuration keys offline.

    :param value: Invalid configuration object.
    :type value: object
    """

    # value — настройки проверяются без DNS и загрузки сертификатов.

    with pytest.raises((ValueError, TypeError)):
        parse_time_sources(value)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Согласованность полей независимого UTC API
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["ok", "bool", "mismatch", "wrong_zone", "duplicate", "oversized_number"])
def test_utctime_payload(
    case: str,
    ) -> None:

    """
    Require agreement between ISO UTC, Unix seconds and millisecond precision.

    :param case: Payload variant without network traffic.
    :type case: str
    """

    # case — нельзя принять несовместимые секунды, bool или неоднозначный JSON.

    source = UtcTimeSource()
    data = {"utc_iso": "2026-09-30T12:00:00Z", "unix": 1790769600, "unix_ms": 1790769600123}
    if case == "bool":
        data["unix"] = True
    elif case == "mismatch":
        data["unix_ms"] += 1000
    elif case == "wrong_zone":
        data["utc_iso"] = "2026-09-30T12:00:00+01:00"
    elif case == "oversized_number":
        data["unix_ms"] = 10 ** 100
    body = json.dumps(data)
    if case == "duplicate":
        body = body[:-1] + ', "unix": 1790769600}'
    if case == "ok":
        stamp, resolution = source._parse_body(body.encode())
        assert stamp == pytest.approx(1790769600.123, abs=0.000001, rel=0)
        assert resolution == 0.001
    else:
        with pytest.raises((ValueError, TypeError)):
            source._parse_body(body.encode())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ от произвольных данных в IPC
#------------------------------------------------------------------------------------------------------------------
def test_time_health_rejects_private_fields() -> None:

    """
    Prevent private strings and endpoint fields from entering a component heartbeat.
    """

    source = RedundantTimeSource()
    source._sources = (StubSource("ok"), StubSource("http"))
    asyncio.run(source.sample())
    original = source.stats()
    message = {"state": "ready", "reason": None, "time_reason": None, "failure": None, "counters": {},
               "time_health": original}
    assert status_message(message)["time_health"] == original
    for key, value in (("endpoint", "private"), ("provider", "private"), ("error_kind", "private"),
                       ("http_status", "private"), ("verify_code", True)):
        health = json.loads(json.dumps(original))
        health["sources"][0][key] = value
        with pytest.raises(ValueError):
            validate_time_health(health)
        with pytest.raises(ValueError):
            status_message({**message, "time_health": health})
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отмена не оставляет фоновых запросов и не продлевает старое показание
#------------------------------------------------------------------------------------------------------------------
def test_cancel_refresh() -> None:

    """
    Bound concurrent providers, retain a valid anchor on cancellation and release the busy gate.
    """

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Отмена двух одновременно ожидающих источников
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Cancel an in-flight refresh and verify task ownership and deadline preservation.
        """

        source = RedundantTimeSource()
        providers = (StubSource("hang"), StubSource("hang"))
        source._sources = providers
        clock = TrustedClock()
        now = monotonic()
        clock.install(TimeSample(lower_utc=1000, upper_utc=1001, observed_at=now))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(clock.refresh(source), 0.02)
        assert not source._busy
        assert all(not item.active for item in providers)
        assert clock.bounds().upper_utc < 1002
        assert clock.refresh_error == "refresh_cancelled"
        source._sources = (StubSource("ok"), StubSource("ok"))
        await clock.refresh(source)
        assert clock.refresh_error is None
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Паузы 15/30/60 и возврат к обычному периоду после успеха
#------------------------------------------------------------------------------------------------------------------
def test_refresh_backoff(
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """
    Schedule bounded sequential retries and restore the normal cadence after success.

    :param monkeypatch: Temporary monotonic-loop sleep replacement.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # monkeypatch — ускоряется только ожидание теста; рабочие часы и процессы не меняются.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Управляемый монотонный отсчёт без реального ожидания
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """
        Run the real maintenance loop using a virtual monotonic counter.
        """

        now, attempts = [0.0], []
        rig = SimpleNamespace(_clock=lambda: now[0], _closed=False, _source=object(), _time_failures=1,
                              config=SimpleNamespace(refresh_interval=60, time_retry_interval=15))

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Продвижение локального времени на одну итерацию
        #----------------------------------------------------------------------------------------------------------
        async def tick(
            seconds: float,
            ) -> None:

            """
            Advance virtual time and stop the maintenance loop after the last expected attempt.

            :param seconds: Requested loop delay.
            :type seconds: float
            """

            # seconds — штатная секундная пауза обслуживания, без реального sleep.

            now[0] += seconds
            if now[0] >= 166:
                rig._closed = True
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Последовательность отказов и восстановления
        #----------------------------------------------------------------------------------------------------------
        async def update() -> None:

            """
            Fail the first two maintenance attempts, then recover normally.
            """

            attempts.append(now[0])
            rig._time_failures = rig._time_failures + 1 if len(attempts) < 3 else 0
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставное обслуживание без журнала и сети
        #----------------------------------------------------------------------------------------------------------
        async def sweep() -> None:

            """
            Leave storage behavior outside this scheduling test.
            """

            return None
        #----------------------------------------------------------------------------------------------------------

        rig._update_time, rig.sweep = update, sweep
        monkeypatch.setattr(asyncio, "sleep", tick)
        await CommandHub._refresh_time(rig)
        assert attempts == [15, 45, 105, 165]
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ резерва виден как warning, без ложного отказа всего канала
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["partial", "retained", "expired", "invalid"])
def test_doctor_time_health(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    ) -> None:

    """
    Report cached UTC health without sending a time request or leaking arbitrary component fields.

    :param monkeypatch: Replacement of the local manager status request.
    :type monkeypatch: pytest.MonkeyPatch

    :param case: Partial outage, retained anchor, expiry or invalid IPC snapshot.
    :type case: str
    """

    # monkeypatch — doctor не подключается к настоящей службе или провайдерам.
    # case — отказ одного резерва не равен недоступности времени для команды.

    pool = RedundantTimeSource()
    pool._sources = (StubSource("ok"), StubSource("timeout"))
    asyncio.run(pool.sample())
    health = pool.stats()
    if case == "retained":
        health.update(selected=None, refresh_error="sources_unavailable")
    if case == "invalid":
        health["endpoint"] = "private"

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Только синтетическое состояние управляющего процесса
    #--------------------------------------------------------------------------------------------------------------
    async def snapshot(
        config: object,
        operation: str,
        ) -> dict:

        """
        Return a cached command component without I/O or private settings.

        :param config: Ignored synthetic component configuration.
        :type config: object

        :param operation: Expected read-only status operation.
        :type operation: str

        :return: Fixed manager snapshot for the selected test case.
        :rtype: dict
        """

        # config — пустой объект не содержит путей или учётных данных.
        # operation — вызовы stop/start этим тестом запрещены.

        assert operation == "status"
        return {"state": "ready", "components": {"commands": {
            "state": "degraded" if case == "expired" else "ready",
            "reason": "time_unavailable" if case == "expired" else None, "time_health": health}}}
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr("remote_watch.gateway_server.manager.control_request", snapshot)
    rows = asyncio.run(_server_online(SimpleNamespace(notifications=None, commands=None), 1))
    row = next(item for item in rows if item["check"] == "commands.time")
    assert row["status"] == ("failed" if case in {"expired", "invalid"} else "warning")
    assert "private" not in json.dumps(rows)
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль tests.unit.test_redundant_time не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
