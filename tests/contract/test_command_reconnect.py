# Восстановление командной сессии без повторного исполнения и потери результатов.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261013-231723
#
# Функции:
# -> restart(): Новый hub над тем же журналом.
# -> wait_for(): Конечное ожидание наблюдаемого состояния.
# -> fast_client(): Короткий интервал фонового наблюдения в тесте.
#
# Тесты:
# -> test_idle_reconnect(): Перезапуск hub и истечение сессии с новыми командами.
# -> test_lost_result_receipt(): Сверка точного результата предыдущего hub.
# -> test_running_command_blocks_reconnect(): Удержание живого callback и сохранение UNKNOWN.
# -> test_reconnect_errors(): Разделение временных и постоянных отказов.
# -> test_lost_registration_response(): Повтор одного nonce после потери ответа.
# -> test_reconnect_shutdown(): Остановка во время задержки без дальнейшей регистрации.
# -> test_dispatcher_reconnect(): Продолжение работы исполнителя после восстановления.
# -> test_reconnect_https(): Настоящий HTTPS с неизменным доверием CA.
# -> test_reconnect_lost_grant(): Сохранение неопределённости сервера без исполнения.
# -> test_reconnect_storage_failure(): Ошибка локальной фиксации новой сессии.
# -> test_two_reconnecting_clients(): Независимое восстановление двух Identity.
# -> test_reconnect_conservative_lease(): Разница локального и серверного lease.
# -> test_previous_process_result(): Сверка завершённого результата предыдущего запуска приложения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from secrets import token_hex
from unittest.mock import patch

import pytest
from test_command_hub import APP_TOKEN, SOURCE_TOKEN, Rig

from remote_watch.commands.client import CommandClient
from remote_watch.commands.hub import CommandHub
from remote_watch.commands.protocol import CommandMessage, callback_result
from remote_watch.commands.sqlite_store import SQLiteCommandStore
from remote_watch.commands.storage import StoreRole
from remote_watch.commands.transport import CommandError, CommandOffer


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Новый hub над тем же журналом
#------------------------------------------------------------------------------------------------------------------
async def restart(
    rig: Rig,
    path: Path,
) -> None:

    """Restart only the gateway owner while retaining the application's open journal.

    :param rig: Running synthetic application and hub.
    :type rig: Rig

    :param path: Directory containing their durable databases.
    :type path: Path
    """

    # rig/path — прежние права, позиции источников и файлы; меняется только поколение hub.
    config = rig.hub.config
    original_exchange = rig.transport.exchange

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Сетевая недоступность на время остановки listener
    #--------------------------------------------------------------------------------------------------------------
    async def offline(
        operation: str,
        message: CommandMessage,
    ) -> CommandMessage | CommandOffer | None:

        """Represent a stopped listener as a network outage.

        :param operation: Original operation name.
        :type operation: str

        :param message: Original typed message.
        :type message: CommandMessage

        :return: No successful response while the listener is stopped.
        :rtype: CommandMessage | CommandOffer | None
        """

        # operation/message — в настоящем HTTP закрытый listener не отвечает code=closed.
        raise CommandError("unavailable")
    #--------------------------------------------------------------------------------------------------------------

    rig.transport.exchange = offline
    await rig.hub.close()
    epoch = token_hex(16)
    rig.store = SQLiteCommandStore(path / "hub.sqlite", owner_id="hub", generation=epoch,
                                   role=StoreRole.HUB, clock=lambda: rig.now)
    rig.hub = CommandHub(config, rig.store, epoch=epoch, trusted_clock=rig.trusted, clock=lambda: rig.now)
    await rig.hub.start()
    rig.transport.hub = rig.hub
    rig.transport.exchange = original_exchange
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Конечное ожидание наблюдаемого состояния
#------------------------------------------------------------------------------------------------------------------
async def wait_for(
    predicate: Callable[[],
    bool],
) -> None:

    """Wait for asynchronous evidence with a finite test deadline.

    :param predicate: Read-only observation of the expected state.
    :type predicate: Callable[[], bool]
    """

    # predicate — ждём факт, а не предполагаем завершение по одной фиксированной задержке.
    deadline = asyncio.get_running_loop().time() + 3
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "reconnect observation timed out"
        await asyncio.sleep(0.005)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Короткий интервал фонового наблюдения в тесте
#------------------------------------------------------------------------------------------------------------------
def fast_client(rig: Rig) -> None:

    """Shorten observation timers without extending any execution or registration deadline.

    :param rig: Client whose first heartbeat task has not yet resumed.
    :type rig: Rig
    """

    # rig — только частота наблюдения; настоящие сроки разрешений остаются прежними.
    rig.client._session = replace(rig.client.session, remaining_ttl=0.03)
    rig.client._reconnect_delay = 0.01
    rig.client._reconnect_max_delay = 0.02
    heartbeat = rig.client._heartbeat_once

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Сохранение короткого интервала после очередного heartbeat
    #--------------------------------------------------------------------------------------------------------------
    async def beat() -> None:

        """Retain short observation intervals after successful heartbeats."""

        await heartbeat()
        rig.client._session = replace(rig.client.session, remaining_ttl=0.03)
    #--------------------------------------------------------------------------------------------------------------

    rig.client._heartbeat_once = beat
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Перезапуск hub и истечение сессии с новыми командами
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["restart", "expiry"])
def test_idle_reconnect(
    tmp_path: Path,
    mode: str,
) -> None:

    """Resume fresh commands with a new nonce while retaining the source cursor.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path

    :param mode: NotificationGateway restart or prolonged loss of heartbeat.
    :type mode: str
    """

    # mode — оба случая требуют новой сессии, но не нового владельца локальной БД.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка восстановления и сохранения старой адресации
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Execute one command before and another after automatic registration."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        try:
            await rig.submit(rig.request(1))
            ticket = await rig.client.acquire()
            assert rig.client.begin(ticket)
            await rig.client.complete(ticket,
                callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "ok"))
            old_session = rig.client.session
            old_request = rig.request(2)
            if mode == "restart":
                await restart(rig, tmp_path)
            else:
                rig.now += 61
            await wait_for(lambda: rig.client.health.reconnects == 1)
            assert rig.client.session.session_id != old_session.session_id
            assert rig.client.health.connection_state == "connected"
            # Сообщение, привязанное до разрыва, не становится командой новой сессии.
            await rig.submit(old_request)
            assert await rig.client.acquire() is None
            await rig.submit(rig.request(3))
            next_ticket = await rig.client.acquire()
            assert next_ticket.grant.request.ref.session_id == rig.client.session.session_id
            assert rig.client.begin(next_ticket)
            await rig.client.complete(next_ticket,
                callback_result(next_ticket.grant.request.ref, next_ticket.grant.claim_id, "new"))
            assert rig.client.health.ready
            assert await rig.hub.source_cursor(SOURCE_TOKEN) == 2
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Сверка точного результата предыдущего hub
#------------------------------------------------------------------------------------------------------------------
def test_lost_result_receipt(tmp_path: Path) -> None:

    """Recover a committed result receipt after a gateway restart without replaying the callback.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — оба журнала переживают смену hub, приложение остаётся тем же владельцем.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Потеря квитанции после устойчивой записи
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Obtain the exact old result receipt under current principal authorization."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        try:
            await rig.submit(rig.request())
            ticket = await rig.client.acquire()
            assert rig.client.begin(ticket)
            result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "executed once")
            rig.transport.lose = "result"
            with pytest.raises(CommandError, match="unavailable"):
                await rig.client.complete(ticket, result)
            await restart(rig, tmp_path)
            await wait_for(lambda: rig.client.health.reconnects == 1)
            assert not await rig.client.pending()
            saved = await rig.client.lookup(result.ref)
            assert saved.acknowledged and saved.record.result == result
            assert await rig.client.acquire() is None
            assert rig.client.health.ready
            with pytest.raises(CommandError, match="denied"):
                await rig.hub.finish(SOURCE_TOKEN, result)
            with pytest.raises(CommandError, match="outcome_conflict"):
                await rig.hub.finish(APP_TOKEN, replace(result, text="different"))
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Удержание живого callback и сохранение UNKNOWN
#------------------------------------------------------------------------------------------------------------------
def test_running_command_blocks_reconnect(tmp_path: Path) -> None:

    """Keep the old execution slot and expose a durable unknown outcome after restart.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — UNKNOWN на сервере не заменяется поздним благополучным результатом клиента.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Остановка hub между началом и завершением callback
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Prove that active work prevents replacement registration and callback replay."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        try:
            await rig.submit(rig.request())
            ticket = await rig.client.acquire()
            assert rig.client.begin(ticket)
            old = rig.client.session
            await restart(rig, tmp_path)
            await wait_for(lambda: rig.client.health.reconnect_reason == "execution_pending")
            assert rig.client.session.session_id == old.session_id
            assert rig.client._active is ticket
            with pytest.raises(CommandError, match="stale_session"):
                await rig.client.acquire()
            result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "finished")
            deadline = asyncio.get_running_loop().time() + 3
            while True:
                assert asyncio.get_running_loop().time() < deadline
                try:
                    # Как и dispatcher, повторяем лишь фиксацию/доставку итога.
                    # Координатор в это время вправе читать тот же однопоточный журнал.
                    if rig.client._active is ticket:
                        await rig.client.complete(ticket, result)
                    else:
                        await rig.client.flush_results()
                except CommandError as error:
                    if error.code == "outcome_conflict":
                        break
                    assert error.code == "busy"
                await asyncio.sleep(0.01)
            await wait_for(lambda: rig.client.health.connection_state == "blocked")
            assert rig.client.health.reconnects == 0
            assert not rig.client.health.ready
            rows = await rig.client.pending()
            assert len(rows) == 1 and rows[0].record.result.text == "finished"
            assert rig.store.get(ticket.grant.request.ref).record.result.outcome.value == "unknown"
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Разделение временных и постоянных отказов
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("failure", ["unavailable", "busy", "denied", "conflict", "tls_certificate"])
def test_reconnect_errors(
    tmp_path: Path,
    failure: str,
) -> None:

    """Retry temporary registration failures and fence authentication or trust failures.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path

    :param failure: Safe failure category injected into replacement registration.
    :type failure: str
    """

    # failure — сетевые отказы повторяются; доверие, права и чужая Identity требуют вмешательства.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Управляемый отказ регистрации
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Observe bounded retries without creating multiple candidate session identifiers."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        original = rig.transport.exchange
        attempts = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подмена одного ответа регистрации
        #----------------------------------------------------------------------------------------------------------
        async def exchange(
            operation: str,
            message: CommandMessage,
        ) -> CommandMessage | CommandOffer | None:

            """Fail the first replacement request with a safe synthetic error.

            :param operation: Requested transport operation.
            :type operation: str

            :param message: Original typed protocol message.
            :type message: CommandMessage

            :return: Unchanged response after the injected error.
            :rtype: CommandMessage | CommandOffer | None
            """

            # operation/message — приватные значения не включаются в ошибку.
            if operation == "register":
                attempts.append(message.session_id)
                if len(attempts) == 1:
                    raise (CommandError("unavailable", error_kind="tls_certificate", verify_code=10)
                           if failure == "tls_certificate" else CommandError(failure))
            return await original(operation, message)
        #----------------------------------------------------------------------------------------------------------

        try:
            with patch.object(rig.transport, "exchange", exchange):
                await restart(rig, tmp_path)
                if failure in {"busy", "unavailable"}:
                    await wait_for(lambda: rig.client.health.reconnects == 1)
                    assert len(attempts) == 2 and attempts[0] == attempts[1]
                else:
                    if failure == "conflict":
                        rig.now += 61
                    await wait_for(lambda: rig.client.health.connection_state == "blocked")
                    assert len(attempts) == 1
                    assert rig.client.health.reconnect_reason == failure
                    if failure == "tls_certificate":
                        assert rig.client.health.heartbeat.verify_code == 10
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Повтор одного nonce после потери ответа
#------------------------------------------------------------------------------------------------------------------
def test_lost_registration_response(tmp_path: Path) -> None:

    """Retry the same registration after the hub committed it but its reply was lost.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — сервер уже занял Identity, повтор не должен создать конфликт с самим собой.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Потеря ответа на новую регистрацию
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Recover through idempotent registration without growing the issued-session table."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        try:
            await restart(rig, tmp_path)
            rig.transport.lose = "register"
            await wait_for(lambda: rig.client.health.reconnects == 1)
            assert len(rig.hub._issued) == 1
            assert rig.client.health.reconnect_attempts >= 2
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Остановка во время задержки без дальнейшей регистрации
#------------------------------------------------------------------------------------------------------------------
def test_reconnect_shutdown(tmp_path: Path) -> None:

    """Cancel a delayed recovery without reopening resources after client shutdown.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — после close не остаётся отдельного таймера или фонового регистратора.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Закрытие клиента в состоянии восстановления
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Stop the only heartbeat and recovery coordinator during its backoff."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        rig.client._reconnect_delay = 10
        rig.client._reconnect_max_delay = 10
        try:
            await restart(rig, tmp_path)
            await wait_for(lambda: rig.client.health.connection_state == "reconnecting")
            await rig.client.close()
            await asyncio.sleep(0)
            assert rig.client._heartbeat.done()
            assert rig.client.health.connection_state == "closed"
            assert not rig.hub._issued
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Исполнитель продолжает опрос после новой регистрации
#------------------------------------------------------------------------------------------------------------------
def test_dispatcher_reconnect(tmp_path: Path) -> None:

    """Resume application callbacks through the actual dispatcher after reconnect.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — выполняем настоящий dispatcher, а не только ручные вызовы acquire/complete.
    from test_command_dispatcher import configured

    from remote_watch import CommandRegistry

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Две команды по обе стороны перезапуска
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Run exactly one callback for each distinct fresh command."""

        calls = []

        #----------------------------------------------------------------------------------------------------------
        # ФУНКЦИЯ : Подставное изменение приложения
        #----------------------------------------------------------------------------------------------------------
        def resume() -> str:

            """Record one observable application side effect.

            :return: Safe callback reply.
            :rtype: str
            """

            calls.append("resume")
            return "ok"
        #----------------------------------------------------------------------------------------------------------

        rig, dispatcher = configured(tmp_path, CommandRegistry.from_callbacks({"resume_load": resume}))
        await rig.hub.start()
        await dispatcher.start()
        fast_client(rig)
        try:
            await rig.submit(rig.request(1))
            await wait_for(lambda: dispatcher.stats.completed == 1 and dispatcher.stats.ready)
            await restart(rig, tmp_path)
            await wait_for(lambda: rig.client.health.reconnects == 1)
            await rig.submit(rig.request(2))
            await wait_for(lambda: dispatcher.stats.completed == 2 and dispatcher.stats.ready)
            assert calls == ["resume", "resume"]
            assert not dispatcher.stats.closed
            assert rig.client.health.poll.current_error is None
        finally:
            await dispatcher.close()
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Восстановление по настоящему HTTPS с тем же доверенным CA
#------------------------------------------------------------------------------------------------------------------
def test_reconnect_https(tmp_path: Path) -> None:

    """Restart a TLS listener on the same endpoint and resume an installed transport.

    :param tmp_path: Isolated journals and temporary certificate authority.
    :type tmp_path: Path
    """

    # tmp_path — сертификаты только тестовые, доверие ОС и настоящие gateway не меняются.
    pytest.importorskip("aiohttp")
    import ssl

    from test_gateway_tls import certificates

    from remote_watch.adapters.command_http import HttpsCommandTransport
    from remote_watch.commands.client import CommandClient
    from remote_watch.gateway.command_server import CommandHubServer

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Смена listener и эпохи без пересоздания транспорта клиента
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Use the same verified TLS context and persistent client journal after restart."""

        ca, cert, key = certificates(tmp_path, "trusted")
        server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_tls.load_cert_chain(cert, key)
        client_tls = ssl.create_default_context(cafile=str(ca))
        rig = Rig(tmp_path)
        server = CommandHubServer(rig.hub)
        await server.start(ssl_context=server_tls)
        port = server.port
        transport = HttpsCommandTransport(f"https://127.0.0.1:{port}", APP_TOKEN, ssl_context=client_tls)
        rig.client = CommandClient(rig.registration, transport, rig.local, clock=lambda: rig.now)
        await rig.client.start()
        fast_client(rig)
        try:
            await server.close()
            # Создаём новый hub, но start принадлежит HTTP server, поэтому здесь
            # ещё не открываем его журнал и фоновые задачи.
            epoch = token_hex(16)
            store = SQLiteCommandStore(tmp_path / "hub.sqlite", owner_id="hub", generation=epoch,
                                       role=StoreRole.HUB, clock=lambda: rig.now)
            rig.hub = CommandHub(rig.hub.config, store, epoch=epoch,
                                 trusted_clock=rig.trusted, clock=lambda: rig.now)
            server = CommandHubServer(rig.hub)
            await server.start(port=port, ssl_context=server_tls)
            await wait_for(lambda: rig.client.health.reconnects == 1)
            await rig.submit(rig.request())
            ticket = await rig.client.acquire()
            assert rig.client.begin(ticket)
            await rig.client.complete(ticket,
                callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "TLS recovered"))
            assert rig.client.health.ready
            assert client_tls.verify_mode == ssl.CERT_REQUIRED and client_tls.check_hostname
        finally:
            await rig.client.close()
            await server.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Неисполненный потерянный grant не переносится в новую сессию
#------------------------------------------------------------------------------------------------------------------
def test_reconnect_lost_grant(tmp_path: Path) -> None:

    """Retire a locally unstarted claim while preserving the hub's unknown execution record.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — сервер успел выдать grant, но клиент его не получил и callback не запускал.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Потеря grant перед перезапуском
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Keep old source and grant references fenced after a fresh registration."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        try:
            request = rig.request(1)
            await rig.submit(request)
            rig.transport.lose = "claim"
            with pytest.raises(CommandError, match="unavailable"):
                await rig.client.acquire()
            assert rig.client._active is None
            await restart(rig, tmp_path)
            await wait_for(lambda: rig.client.health.reconnects == 1)
            saved = await rig.client.lookup(request.ref)
            assert saved.acknowledged and saved.record.phase.value == "expired"
            assert rig.store.get(request.ref).execution_active
            assert await rig.client.acquire() is None
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибка локальной записи не превращается в готовность
#------------------------------------------------------------------------------------------------------------------
def test_reconnect_storage_failure(tmp_path: Path) -> None:

    """Retry a failed local binding without reopening or clearing the client's journal.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — ошибка относится к storage; успешный HTTP register её не скрывает.
    from remote_watch.commands.storage import StoreError

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Отказ хранения после успешного register
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Keep one server registration while waiting for local storage recovery."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        generation = rig.local._generation
        try:
            with patch.object(rig.local, "bind_session", side_effect=StoreError("synthetic failure")):
                await restart(rig, tmp_path)
                await wait_for(lambda: rig.client.health.storage.current_error == "unavailable")
                assert not rig.client.health.ready
                assert rig.client.health.reconnects == 0
                assert len(rig.hub._issued) == 1
            await wait_for(lambda: rig.client.health.reconnects == 1)
            assert rig.local._generation == generation
            assert rig.client.health.storage.current_error is None
            assert len(rig.hub._issued) == 1
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Два приложения независимо восстанавливаются на одном hub
#------------------------------------------------------------------------------------------------------------------
def test_two_reconnecting_clients(tmp_path: Path) -> None:

    """Re-register two identities independently without changing their source routing.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — один hub, два отдельных журнала и две неизменяемые Identity.
    from types import SimpleNamespace

    from test_command_hub import DirectTransport

    from remote_watch.commands.client import CommandClient

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Независимая адресация после общего рестарта
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Execute only the commands addressed to each client's replacement session."""

        rig = Rig(tmp_path)
        second_identity = replace(rig.identity, instance_id="two")
        config = rig.hub.config
        rig.hub.config = replace(config,
            principals=(replace(config.principals[0], identities=(rig.identity, second_identity)),),
            sources=(replace(config.sources[0], access=(config.sources[0].access[0],
                replace(config.sources[0].access[0], identity=second_identity))),))
        registration = replace(rig.registration, identity=second_identity, session_id=token_hex(16))
        second_transport = DirectTransport(rig.hub)
        second = CommandClient(registration, second_transport,
            SQLiteCommandStore(tmp_path / "second.sqlite", owner_id="second", generation=registration.session_id,
                               role=StoreRole.CLIENT, clock=lambda: rig.now), clock=lambda: rig.now)
        await rig.start()
        fast_client(rig)
        await second.start()
        fast_client(SimpleNamespace(client=second))
        try:
            # На короткое время выключаем второй транспорт как недоступный listener.
            with patch.object(second_transport, "exchange", side_effect=CommandError("unavailable")):
                await restart(rig, tmp_path)
                second_transport.hub = rig.hub
            await wait_for(lambda: rig.client.health.reconnects == 1 and second.health.reconnects == 1)
            assert rig.client.session.session_id != second.session.session_id
            for number, client in enumerate((rig.client, second), 1):
                request = rig.request(number)
                request = replace(request, ref=replace(request.ref, identity=client.session.identity,
                                                       session_id=client.session.session_id))
                await rig.submit(request)
                ticket = await client.acquire()
                assert ticket.grant.request.ref.identity == client.session.identity
                assert client.begin(ticket)
                await client.complete(ticket,
                    callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "ok"))
            assert len(rig.hub._issued) == 2
        finally:
            await second.close()
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Короткий разрыв между локальным и серверным lease
#------------------------------------------------------------------------------------------------------------------
def test_reconnect_conservative_lease(tmp_path: Path) -> None:

    """Wait for the old server lease when conservative local expiry occurs first.

    :param tmp_path: Isolated journal directory.
    :type tmp_path: Path
    """

    # tmp_path — воспроизводим вычет полного RTT, не продлевая право клиента запускать команды.

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Временный конфликт со своей предыдущей регистрацией
    #--------------------------------------------------------------------------------------------------------------
    async def scenario() -> None:

        """Bound self-conflict retries by the last observed server lifetime."""

        rig = Rig(tmp_path)
        await rig.start()
        fast_client(rig)
        try:
            rig.client._lease = replace(rig.client._lease, expires_at=100.1)
            rig.client._health.lease(100.1)
            rig.now = 100.2
            await wait_for(lambda: rig.client.health.reconnect_reason == "previous_lease")
            assert not rig.client.health.ready
            assert len(rig.hub._issued) == 1
            rig.now = 161
            await wait_for(lambda: rig.client.health.reconnects == 1)
            assert len(rig.hub._issued) == 2
            assert rig.client.health.heartbeat.current_error is None
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Завершённый результат прежнего процесса не теряется при новой регистрации
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["confirmed", "same_hub", "on_reconnect", "unknown"])
def test_previous_process_result(
    tmp_path: Path,
    mode: str,
) -> None:

    """Recover a lost receipt across application and hub restarts without replaying execution.

    :param tmp_path: Isolated persistent journals retained by both process generations.
    :type tmp_path: Path

    :param mode: Whether the previous hub persisted the exact completed result.
    :type mode: str
    """

    # tmp_path/mode — результат клиента есть в обоих случаях; истина на hub различается.
    async def scenario() -> None:

        """Retain old references and require a matching durable gateway receipt."""

        rig = Rig(tmp_path)
        await rig.start()
        executions = 0
        try:
            await rig.submit(rig.request())
            ticket = await rig.client.acquire()
            assert rig.client.begin(ticket)
            executions += 1
            result = callback_result(ticket.grant.request.ref, ticket.grant.claim_id, "previous process")
            if mode != "unknown":
                rig.transport.lose = "result"
                with pytest.raises(CommandError, match="unavailable"):
                    await rig.client.complete(ticket, result)
            else:
                # Потеря до записи hub: после его restart останется UNKNOWN.
                with patch.object(rig.transport, "exchange", side_effect=CommandError("unavailable")):
                    with pytest.raises(CommandError, match="unavailable"):
                        await rig.client.complete(ticket, result)
            await rig.client.close()
            if mode == "same_hub":
                rig.now += 61
            else:
                await restart(rig, tmp_path)

            # Новый процесс использует ту же БД, но новый owner generation и session_id.
            generation = token_hex(16)
            rig.local = SQLiteCommandStore(tmp_path / "client.sqlite", owner_id="app", generation=generation,
                                          role=StoreRole.CLIENT, clock=lambda: rig.now)
            rig.registration = replace(rig.registration, session_id=generation)
            rig.client = CommandClient(rig.registration, rig.transport, rig.local, clock=lambda: rig.now)
            await rig.client.start()
            fast_client(rig)
            assert rig.client.health.has_pending_results
            assert not rig.client.health.ready
            if mode == "unknown":
                with pytest.raises(CommandError, match="outcome_conflict"):
                    await rig.client.flush_results()
                assert not (await rig.client.lookup(result.ref)).acknowledged
                assert rig.store.get(result.ref).record.result.outcome.value == "unknown"
                assert executions == 1
                return

            if mode == "on_reconnect":
                await restart(rig, tmp_path)
                await wait_for(lambda: rig.client.health.reconnects == 1)
            else:
                await rig.client.flush_results()
            saved = await rig.client.lookup(result.ref)
            assert saved.acknowledged and saved.record.result == result
            assert not rig.client.health.has_pending_results
            assert executions == 1
            if mode != "on_reconnect":
                await restart(rig, tmp_path)
                await wait_for(lambda: rig.client.health.reconnects == 1)
            assert rig.client.health.ready
            assert await rig.client.acquire() is None
            assert executions == 1
        finally:
            await rig.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(scenario())
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение тестового файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Тесты reconnect запускаются через pytest.")
#------------------------------------------------------------------------------------------------------------------
