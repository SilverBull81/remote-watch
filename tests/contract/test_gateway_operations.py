# Эксплуатация gateway: штатная остановка, безопасная диагностика и общий получатель.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260929-202056
#
# Классы:
# -> Provider: Канал с управляемым завершением попытки.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Открытие без сетевых обращений.
#    -> send(): Одна попытка с сохранением принадлежности и результата.
#    -> close(): Завершение и освобождение ресурсов.
#
# Функции и тесты:
# -> test_cli_summary(): Единственная сводка после завершения CLI.
# -> test_config_categories(): Безопасные категории ошибок JSON.
# -> test_cli_private_arguments(): Отсутствие приватных аргументов в ошибке CLI.
# -> test_shared_alias(): Конкуренция двух приложений за общего получателя.
# -> test_cli_process_stop(): Штатная остановка настоящего дочернего процесса.
# -> test_mixed_field(): Полевой сценарий через relay и прямой ntfy.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from remote_watch import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    Notification,
    RetryPolicy,
    field_smoke,
)
from remote_watch import gateway as cli
from remote_watch.gateway import Gateway
from remote_watch.gateway_config import GatewayConfig, GatewayPrincipal
from remote_watch.gateway_json import GatewayConfigError, load_gateway_config
from remote_watch.relay import RelayRequest, decode_response


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Канал с управляемым завершением попытки
#------------------------------------------------------------------------------------------------------------------
class Provider:
    """Hold one attempt to make competing requests deterministic."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Create barriers in the test event loop."""

        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.seen: list[Delivery] = []
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Открытие без сетевых обращений
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Open without reading provider credentials."""

        pass
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка с сохранением принадлежности и результата
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
        ) -> DeliveryResult:

        """Retain identity and correlation until the test releases the provider.

        :param delivery: Prepared delivery.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленная доставка.

        self.seen.append(delivery)
        self.entered.set()
        await self.release.wait()
        return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, provider_message_id=delivery.delivery_id)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Завершение и освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Release the local barrier on cleanup."""

        self.release.set()
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Единственная сводка после завершения CLI
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["stop", "cancel", "startup", "stale"])
def test_cli_summary(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ) -> None:

    """Emit one bounded summary after cleanup for every managed lifecycle outcome.

    :param mode: Selected scenario.
    :type mode: str

    :param tmp_path: Temporary test directory.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param capsys: Captured console output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # mode - выбранный сценарий.
    # tmp_path - временный каталог.
    # monkeypatch - подмена окружения и зависимостей.
    # capsys - перехват вывода консоли.

    path = tmp_path / "private_path.stop"
    if mode == "stale":
        path.touch()
    closed = []

    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Подставной gateway для проверки CLI
    #--------------------------------------------------------------------------------------------------------------
    class Managed:
        """Replace networking while retaining the real CLI lifecycle."""

        #----------------------------------------------------------------------------------------------------------
        # КОНСТРУКТОР
        #----------------------------------------------------------------------------------------------------------
        def __init__(
            self,
            config: object,
            ) -> None:

            """Accept the synthetic configuration.

            :param config: Validated or synthetic configuration.
            :type config: object
            """

            # config - настройки сценария.

            self.port = 12345
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Управляемый исход запуска
        #----------------------------------------------------------------------------------------------------------
        async def start(
            self,
            **kwargs: object,
            ) -> None:

            """Exercise normal start, cancellation and a sanitized startup failure.

            :param kwargs: Listener settings.
            :type kwargs: object
            """

            # kwargs - параметры прослушивания.

            if mode == "startup":
                raise cli.GatewayStartupError("providers")
            if mode == "cancel":
                raise asyncio.CancelledError
            path.touch()
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Завершение и освобождение ресурсов
        #----------------------------------------------------------------------------------------------------------
        async def close(self) -> None:

            """Record cleanup before statistics are obtained."""

            closed.append(True)
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Сводка после завершения очистки
        #----------------------------------------------------------------------------------------------------------
        def stats(self) -> dict[str, int]:

            """Return counters only after cleanup.

            :return: The value described by this operation.
            :rtype: dict[str, int]
            """

            assert closed
            return {"active": 0, "destinations_busy": 0}
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(cli, "Gateway", Managed)
    if mode == "stop":
        asyncio.run(cli._serve(None, "127.0.0.1", 0, None, path))
    else:
        expected = {"cancel": asyncio.CancelledError, "startup": cli.GatewayStartupError, "stale": ValueError}
        with pytest.raises(expected[mode]):
            asyncio.run(cli._serve(None, "127.0.0.1", 0, None, path))
    output = capsys.readouterr().out
    assert "private_path" not in output
    rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert rows == [{"kind": "gateway_summary", "stats": {"active": 0, "destinations_busy": 0}}]
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Безопасные категории ошибок JSON
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("raw", "code", "field"), [
    (None, "config_read", "config"), (b"{private", "config_json", "config"),
    (b'{"private_key": "private_value"}', "config_fields", "config"),
    (b'{"schema_version":2,"destinations":[],"principals":[]}', "config_schema", "schema_version"),
    (b'{"schema_version":1,"destinations":[],"principals":[]}', "config_value", "destinations"),
])
def test_config_categories(
    raw: bytes | None,
    code: str,
    field: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    ) -> None:

    """Expose only predefined error codes and schema groups, never arbitrary JSON keys.

    :param raw: Synthetic input bytes or absent file.
    :type raw: bytes | None

    :param code: Expected fixed code.
    :type code: str

    :param field: Expected schema group.
    :type field: str

    :param tmp_path: Temporary test directory.
    :type tmp_path: Path

    :param capsys: Captured console output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # raw - синтетические байты либо отсутствие файла.
    # code - ожидаемый безопасный код.
    # field - ожидаемая группа полей.
    # tmp_path - временный каталог.
    # capsys - перехват вывода консоли.

    path = tmp_path / "private_config.json"
    if raw is not None:
        path.write_bytes(raw)
    with pytest.raises(GatewayConfigError) as error:
        load_gateway_config(path)
    assert (error.value.code, error.value.field) == (code, field)
    assert cli.main(["--config", str(path), "--check-config"]) == 1
    output = capsys.readouterr().out
    assert f"code={code} field={field}" in output
    assert "private" not in output
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отсутствие приватных аргументов в ошибке CLI
#------------------------------------------------------------------------------------------------------------------
def test_cli_private_arguments(capsys: pytest.CaptureFixture[str]) -> None:

    """Do not echo a private argument even when argparse rejects it.

    :param capsys: Captured console output.
    :type capsys: pytest.CaptureFixture[str]
    """

    # capsys - перехват вывода консоли.

    with pytest.raises(SystemExit) as error:
        cli.main(["--port", "private_value"])
    assert error.value.code == 2
    assert "private_value" not in capsys.readouterr().err
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Конкуренция двух приложений за общего получателя
#------------------------------------------------------------------------------------------------------------------
def test_shared_alias(
    notification: Notification,
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """Keep two principals isolated while contending for one provider destination.

    :param notification: Synthetic notification.
    :type notification: Notification

    :param monkeypatch: Pytest patch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # notification - синтетическое уведомление.
    # monkeypatch - подмена окружения и зависимостей.

    aiohttp = pytest.importorskip("aiohttp")
    monkeypatch.setenv("GW_ONE", "synthetic_one_" + "x" * 32)
    monkeypatch.setenv("GW_TWO", "synthetic_two_" + "x" * 32)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение ограниченного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Reject competing and unauthorized calls before any extra provider attempt."""

        provider = Provider()
        other = replace(notification, identity=replace(notification.identity, instance_id="two"))
        config = GatewayConfig(destinations=(Destination(destination_id="shared", channel_factory=lambda: provider,
            retry=RetryPolicy(max_attempts=1)),), principals=tuple(
                GatewayPrincipal(name=name, token_env=env, identity=event.identity,
                                 aliases=("shared",), min_interval=0)
                for name, env, event in (("one", "GW_ONE", notification), ("two", "GW_TWO", other))),
            destination_interval=0)
        gateway = Gateway(config, utc_now=lambda: notification.created_at)
        first = Delivery(notification=notification, destination_id="phone", delivery_id="first")
        second = Delivery(notification=other, destination_id="phone", delivery_id="second")

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Запрос с токеном выбранного приложения
        #----------------------------------------------------------------------------------------------------------
        async def post(
            client: aiohttp.ClientSession,
            delivery: Delivery,
            env: str,
            alias: str = 'shared',
            ) -> tuple[int, dict]:

            """Submit a correlated relay envelope with the selected principal token.

            :param client: Local HTTP client.
            :type client: aiohttp.ClientSession

            :param delivery: Prepared delivery.
            :type delivery: Delivery

            :param env: Synthetic credential variable.
            :type env: str

            :param alias: Requested destination.
            :type alias: str

            :return: Bounded HTTP reply or sanitized transport failure.
            :rtype: tuple[int, dict]
            """

            # client - локальный HTTP-клиент.
            # delivery - подготовленная доставка.
            # env - имя переменной тестового токена.
            # alias - запрошенный получатель.

            body = RelayRequest(delivery=delivery, alias=alias, remaining_ttl=10, timeout=8).to_dict()
            async with client.post(f"http://127.0.0.1:{gateway.port}/v1/notifications", json=body,
                                   headers={"Authorization": "Bearer " + os.environ[env]}) as response:
                return response.status, await response.json()
        #----------------------------------------------------------------------------------------------------------

        task = None
        try:
            await gateway.start(port=0)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:
                task = asyncio.create_task(post(client, first, "GW_ONE"))
                await asyncio.wait_for(provider.entered.wait(), 2)
                status, body = await post(client, second, "GW_TWO")
                assert status == 429
                assert len(provider.seen) == 1
                assert (await post(client, first, "GW_TWO"))[0] == 403
                assert (await post(client, second, "GW_TWO", "foreign"))[0] == 403
                provider.release.set()
                status, body = await task
                assert status == 200
                assert decode_response(body, first).provider_message_id == "first"
                status, body = await post(client, second, "GW_TWO")
                assert status == 200
                assert decode_response(body, second).provider_message_id == "second"
                assert [item.notification.identity for item in provider.seen] == [
                    notification.identity, other.identity,
                ]
        finally:
            provider.release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await gateway.close()
        assert gateway.stats()["active"] == 0
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Штатная остановка настоящего дочернего процесса
#------------------------------------------------------------------------------------------------------------------
def test_cli_process_stop(tmp_path: Path) -> None:

    """Stop a real gateway child process through the local file on Windows and Unix.

    :param tmp_path: Temporary test directory.
    :type tmp_path: Path
    """

    # tmp_path - временный каталог.

    pytest.importorskip("aiohttp")
    factory = tmp_path / "synthetic_factory.py"
    factory.write_text(
        "from remote_watch import Destination, Identity, RetryPolicy\n"
        "from remote_watch.gateway_config import GatewayConfig, GatewayPrincipal\n"
        "class Channel:\n"
        "    async def open(self): pass\n"
        "    async def close(self): pass\n"
        "    async def send(self, delivery): raise AssertionError('no deliveries expected')\n"
        "def create():\n"
        "    identity = Identity(service='s', environment='e', region='r', host='h', instance_id='i')\n"
        "    return GatewayConfig(destinations=(Destination(destination_id='phone', channel_factory=Channel,\n"
        "        retry=RetryPolicy(max_attempts=1)),), principals=(GatewayPrincipal(name='app',\n"
        "        token_env='GW_PROCESS_TEST', identity=identity, aliases=('phone',)),))\n", encoding="utf-8")
    environment = dict(os.environ, GW_PROCESS_TEST="synthetic_process_" + "x" * 32,
                       PYTHONPATH=os.pathsep.join((str(tmp_path), str(Path(cli.__file__).resolve().parents[1]))))
    stop = tmp_path / "gateway.stop"
    output = tmp_path / "process.log"
    with output.open("wb") as stream:
        process = subprocess.Popen([sys.executable, "-u", "-m", "remote_watch.gateway", "synthetic_factory:create",
                                    "--port", "0", "--stop-file", str(stop)], cwd=tmp_path, env=environment,
                                   stdout=stream, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 8
            while b"TCP" not in output.read_bytes():
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.02)
            stop.touch()
            assert process.wait(timeout=8) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    # Проверяем факт завершения и итог после настоящей очистки CLI-процесса.
    data = output.read_bytes()
    rows = [json.loads(line) for line in data.splitlines() if line.startswith(b"{")]
    assert len(rows) == 1 and rows[0]["kind"] == "gateway_summary"
    assert rows[0]["stats"]["active"] == 0
    assert b"synthetic_process_" not in data
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Полевой сценарий через relay и прямой ntfy
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("authorized", [True, False])
def test_mixed_field(
    authorized: bool,
    notification: Notification,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ) -> None:

    """Run the mixed CLI through real relay HTTP and direct ntfy without Telegram credentials.

    :param authorized: Whether the relay token is authorized.
    :type authorized: bool

    :param notification: Synthetic notification.
    :type notification: Notification

    :param tmp_path: Temporary test directory.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    """

    # authorized - допустимость токена relay.
    # notification - синтетическое уведомление.
    # tmp_path - временный каталог.
    # monkeypatch - подмена окружения и зависимостей.

    pytest.importorskip("aiohttp")
    from aiohttp import web

    monkeypatch.setenv("GW_FIELD", "synthetic_field_" + "x" * 32)
    original_run = field_smoke._run

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Ускорение расписания без подмены доставки
    #--------------------------------------------------------------------------------------------------------------
    def fast_run(
        config: field_smoke.FieldConfig,
        destinations: tuple[Destination, ...],
        output: Path,
        ) -> int:

        """Accelerate sample scheduling while leaving the real delivery worker untouched.

        :param config: Validated or synthetic configuration.
        :type config: field_smoke.FieldConfig

        :param destinations: Configured delivery paths.
        :type destinations: tuple[Destination, ...]

        :param output: New local output path.
        :type output: Path

        :return: The value described by this operation.
        :rtype: int
        """

        # config - настройки сценария.
        # destinations - настроенные способы доставки.
        # output - путь нового отчёта.

        clock = [0.0]

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Продвижение часов расписания
        #----------------------------------------------------------------------------------------------------------
        def advance(delay: float) -> None:

            """Advance only the application scheduling clock.

            :param delay: Synthetic elapsed seconds.
            :type delay: float
            """

            # delay - сдвиг расписания в секундах.

            clock[0] += delay
        #----------------------------------------------------------------------------------------------------------

        return original_run(config, destinations, output, monotonic=lambda: clock[0], sleep=advance)
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr(field_smoke, "_run", fast_run)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Выполнение ограниченного асинхронного сценария
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Keep the gateway event loop running while the field CLI owns its runtime thread."""

        ntfy_calls = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Локальное подтверждение ntfy
        #----------------------------------------------------------------------------------------------------------
        async def publish(request: web.Request) -> web.Response:

            """Accept one direct synthetic notification.

            :param request: Local HTTP request.
            :type request: web.Request

            :return: The value described by this operation.
            :rtype: web.Response
            """

            # request - локальный HTTP-запрос.

            payload = await request.json()
            ntfy_calls.append(payload)
            return web.json_response({"event": "message", "id": "synthetic", "topic": payload["topic"]})
        #----------------------------------------------------------------------------------------------------------

        app = web.Application()
        app.router.add_post("/", publish)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        server = await asyncio.get_running_loop().create_server(runner.server, "127.0.0.1", 0)
        provider = Provider()
        provider.release.set()
        identity = replace(notification.identity, service="remote-watch-field", environment="field-test")
        gateway = Gateway(GatewayConfig(destinations=(Destination(
            destination_id="tg", channel_factory=lambda: provider,
            retry=RetryPolicy(max_attempts=1)),), principals=(GatewayPrincipal(name="field", token_env="GW_FIELD",
                identity=identity, aliases=("tg",), min_interval=0),), destination_interval=0))
        credentials = tmp_path / "synthetic.json"
        output = tmp_path / "mixed.jsonl"
        before = set(os.environ)
        try:
            await gateway.start(port=0)
            credentials.write_text(json.dumps({
                "relay": {"token": os.environ["GW_FIELD"] if authorized else "invalid_private_token" * 2,
                          "endpoint": f"http://127.0.0.1:{gateway.port}", "alias": "tg", "allow_http": True},
                "ntfy": {"token": "ntfy_private_token", "topic": "private_topic", "allow_http": True,
                         "endpoint": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"},
            }), encoding="utf-8")
            arguments = ["mixed", "--credentials", str(credentials), "--region", identity.region,
                         "--host", identity.host, "--instance-id", identity.instance_id,
                         "--duration", "4", "--interval", "1", "--output", str(output)]
            code = await asyncio.wait_for(asyncio.to_thread(field_smoke.main, arguments), 10)
            assert code == (0 if authorized else 1)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            assert rows[0]["delivery_modes"] == {"telegram": "relay", "ntfy": "direct"}
            assert rows[-1]["provider_acceptance_complete"] is authorized
            assert len(ntfy_calls) == 4
            assert len(provider.seen) == (4 if authorized else 0)
            text = output.read_text(encoding="utf-8")
            assert all(value not in text for value in ("private_token", "private_topic", os.environ["GW_FIELD"]))
            assert not [key for key in set(os.environ) - before if key.startswith("REMOTE_WATCH_FIELD_")]
        finally:
            await gateway.close()
            server.close()
            await server.wait_closed()
            await runner.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_gateway_operations не предназначен для прямого запуска.",
    )
#------------------------------------------------------------------------------------------------------------------
