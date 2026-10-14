# Управление собственными процессами gateway и закрытый локальный канал состояния.
#
# Version 1.0.4
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-142149
#
# Классы:
# -> StatusLog: Ограниченный журнал с безопасным признаком ошибки записи
#    Интерфейс:
#    -> handleError(): Учёт отказа записи без traceback и рекурсивного logging
#
# -> ChildState: Состояние текущего дочернего процесса и его бюджет восстановления
#
# -> GatewayManager: Запуск компонентов, контроль работоспособности и общая остановка
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта.
#    Интерфейс:
#    -> status(): Чтение безопасного состояния компонентов
#    -> run(): Работа до остановки с общим откатом неудачного запуска
#    Служебные методы:
#    -> _component_status(): Копия статуса с возрастом последнего снимка журнала
#    -> _open_control(): Открытие локального управления
#    -> _read(): Проверка токена и ограниченное чтение локального запроса
#    -> _control(): Статус либо запрос общей остановки текущего запуска
#    -> _heartbeat(): Приём heartbeat только от своего текущего дочернего процесса
#    -> _spawn(): Запуск под контролем ОС до передачи настроек компоненту
#    -> _tick(): Контроль зависаний и резервирование конечного числа перезапусков
#    -> _report(): Запись переходов состояния и оповещение владельца
#    -> _shutdown(): Общая остановка с конечными сроками ожидания
#
# Функции:
# -> control_request(): Локальный status/stop по защищённому файлу текущего запуска.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import monotonic

from remote_watch.commands.source_store import ProcessLock
from remote_watch.gateway_server.application import OperatorAlerts
from remote_watch.gateway_server.config import DeploymentConfig
from remote_watch.gateway_server.messages import read_limited, status_message
from remote_watch.gateway_server.policy import FailureKind, RestartBudget
from remote_watch.gateway_server.processes import ProcessGroup, private_directory

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Ограниченный журнал с безопасным признаком ошибки записи
#------------------------------------------------------------------------------------------------------------------
class StatusLog(RotatingFileHandler):
    """
    Expose logging failure without printing exception text or file contents.
    """

    failed: bool = False

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Учёт отказа записи без traceback и рекурсивного logging
    #--------------------------------------------------------------------------------------------------------------
    def handleError(
        self,
        record: logging.LogRecord,
        ) -> None:

        """
        Latch a failed log write for local status and an independent owner alert.

        :param record: Safe status whose file write failed.
        :type record: logging.LogRecord
        """

        # record — при ошибке диска не пытаемся записать ещё одну строку через тот же handler.
        self.failed = True
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Состояние текущего дочернего процесса и его бюджет восстановления
#------------------------------------------------------------------------------------------------------------------
@dataclass
class ChildState:
    """
    Retain only current owned handles and bounded recovery state.

    :param name: Stable configured command, principal, service or component name.
    :type name: str

    :param budget: Independent restart budget for this component.
    :type budget: RestartBudget

    :param process: Owned child process handle; never rediscovered by PID.
    :type process: subprocess.Popen | None

    :param group: Process containment object owned by this component.
    :type group: ProcessGroup | None

    :param token: Private literal credential excluded from representations.
    :type token: str

    :param message: Prepared notification text or latest bounded child status message.
    :type message: dict

    :param contact: Last heartbeat time measured by the manager's monotonic clock.
    :type contact: float

    :param contacted: Whether the first child heartbeat completed bootstrap.
    :type contacted: bool

    :param launched: Monotonic time when the child process was launched.
    :type launched: float

    :param retry_at: Monotonic time of the next permitted retry.
    :type retry_at: float | None

    :param stopping: Whether only this child should receive a stop instruction.
    :type stopping: bool

    :param restarts: Number of actual component restarts.
    :type restarts: int
    """

    name: str                          # Фиксированное имя компонента.
    budget: RestartBudget              # Независимый бюджет перезапусков.
    process: subprocess.Popen | None = None  # Только собственный процесс, без поиска по PID.
    group: ProcessGroup | None = None  # Job данного компонента, не соседних gateway.
    token: str = field(default="", repr=False)  # Секрет текущего incarnation для heartbeat.
    message: dict = field(default_factory=lambda: {  # Последний безопасный heartbeat текущего запуска.
        "state": "starting", "reason": None, "time_reason": None, "failure": None, "counters": {}})
    contact: float = 0.0               # Время приёма heartbeat по часам manager.
    contacted: bool = False            # Первый heartbeat уже получен, интерпретатор завершил bootstrap.
    launched: float = 0.0              # Начало текущей попытки запуска.
    retry_at: float | None = None      # Момент следующей разрешённой попытки.
    stopping: bool = False             # Ответить stop только этому дочернему процессу.
    restarts: int = 0                  # Число фактических повторных запусков.
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Запуск компонентов, контроль работоспособности и общая остановка
#------------------------------------------------------------------------------------------------------------------
class GatewayManager:
    """
    Supervise independent gated processes without touching external Caddy or client sessions.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        config: DeploymentConfig,
        *,
        operator_sid: str | None = None,
        ) -> None:

        """
        Retain validated settings without opening deployment resources.

        :param config: Immutable deployment settings.
        :type config: DeploymentConfig

        :param operator_sid: Explicit service operator allowed to read local control state.
        :type operator_sid: str | None
        """

        # config — исходные пути сохраняются, журналы и CA никуда не переносятся.
        # operator_sid — только служебный host; токен управления не становится публичным.
        self.config = config
        self._operator_sid = operator_sid
        self.stop_event = asyncio.Event()
        self.state = "starting"
        self.reason = None
        self.generation = secrets.token_hex(16)
        self._token = secrets.token_urlsafe(32)
        self._lock = ProcessLock(config.control_dir / "manager.lock")
        self._runner = None
        self._port = 0
        self._active = 0
        self._log = logging.Logger("remote_watch.gateway_supervisor", logging.INFO)
        self._log.propagate = False
        self._log.addHandler(logging.NullHandler())
        self._last_log = None
        self._log_handler = None
        self._alerts = OperatorAlerts(config.application)
        self._alerts_failed = False
        self._tray = None
        names = [name for name in ("notifications", "commands") if getattr(config, name) is not None]
        if config.caddy.mode == "managed":
            names.insert(0, "caddy")
        self.children = {name: ChildState(name, RestartBudget(config.restart)) for name in names}
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение безопасного состояния компонентов
    #--------------------------------------------------------------------------------------------------------------
    def status(
        self,
        ) -> dict:

        """
        Read cached sanitized status without opening journals or probing providers.

        :return: Bounded operational snapshot with no credentials or filesystem paths.
        :rtype: dict
        """

        return {"schema_version": 1, "generation": self.generation, "state": self.state,
                "reason": self.reason, "ingress": "unverified", "caddy_mode": self.config.caddy.mode,
                "logging": "failed" if self._log_handler and self._log_handler.failed else "available",
                "ui": "headless" if self._tray is None else "ready" if self._tray.ready else "failed",
                "command_sessions": "re_registration_required" if self.children.get("commands")
                and self.children["commands"].restarts else "not_verified",
                "alerts": "failed" if self._alerts_failed else "enabled" if self.config.application.destinations
                else "disabled", "alert_delivery": self._alerts.stats(),
                "components": {name: self._component_status(child)
                                               for name, child in self.children.items()}}
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Работа до остановки с общим откатом неудачного запуска
    #--------------------------------------------------------------------------------------------------------------
    async def run(
        self,
        ) -> int:

        """
        Own one deployment until stop, rolling startup failures back as a single operation.

        :return: Zero after requested shutdown, one after failed startup or supervision.
        :rtype: int
        """

        # До открытия существующей lock запрещаем ссылки: раннее получение lock
        # не должно обойти прежний запрет private_directory и записать чужой файл.
        for path in (self.config.control_dir, self.config.control_dir / "manager.lock"):
            if path.is_symlink() or path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise RuntimeError("control_permissions")
        if self.config.control_dir.exists():
            # При разных аккаунтах service/operator второй --start не должен
            # менять ACL живого владельца до отказа по manager.lock.
            self._lock.open()
            try:
                private_directory(self.config.control_dir, self._operator_sid)
            except BaseException:
                self._lock.close()
                raise
        else:
            private_directory(self.config.control_dir, self._operator_sid)
            self._lock.open()
        result = 1
        try:
            private_directory(self.config.log_dir, self._operator_sid)
            handler = StatusLog(self.config.log_dir / "gateway_server.jsonl",
                                maxBytes=self.config.application.log_max_bytes,
                                backupCount=self.config.application.log_backups, encoding="utf-8")
            self._log_handler = handler
            self._log.addHandler(handler)
            await self._open_control()
            try:
                await self._alerts.start()
            except Exception:
                self._alerts_failed = True
            if self.config.application.ui == "tray":
                from remote_watch.gateway_server.tray import GatewayTray

                loop = asyncio.get_running_loop()
                self._tray = GatewayTray(lambda: loop.call_soon_threadsafe(self.stop_event.set),
                                         icon_path=self.config.application.icon_path)
                await asyncio.to_thread(self._tray.start)
            # SIGINT обрабатывается в asyncio.run; SIGTERM также идёт через общий stop.
            if sys.platform == "linux":
                asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, self.stop_event.set)
            deadline = monotonic() + self.config.lifecycle.startup_timeout
            for child in self.children.values():
                await self._spawn(child)
            started = False
            while not self.stop_event.is_set():
                await self._tick(started)
                if not started:
                    if all(child.message["state"] == "ready" for child in self.children.values()):
                        started = True
                    elif any(child.message["state"] == "failed" for child in self.children.values()):
                        self.reason = next(c.message["reason"] for c in self.children.values()
                                           if c.message["state"] == "failed")
                        break
                    elif monotonic() >= deadline:
                        self.reason = "startup_timeout"
                        break
                self.state = ("ready" if all(c.message["state"] == "ready" for c in self.children.values())
                              and not self._alerts_failed and not handler.failed
                              and (self._tray is None or self._tray.ready)
                              else "degraded") if started else "starting"
                self._report()
                try:
                    await asyncio.wait_for(self.stop_event.wait(),
                                           min(.2, self.config.lifecycle.heartbeat_interval))
                except asyncio.TimeoutError:
                    pass
            result = 0 if self.stop_event.is_set() else 1
        except asyncio.CancelledError:
            self.stop_event.set()
            result = 0
        except ImportError:
            self.reason = "dependency_missing"
        except Exception:
            self.reason = "control_unavailable"
        finally:
            if result:
                self.state = "failed"
                self._report()
            try:
                await self._shutdown()
            finally:
                # Даже отказ закрытия UI/alert transport не оставляет bot reader без владельца.
                for child in self.children.values():
                    if child.group is not None:
                        child.group.close()
                    if child.process is not None and child.process.poll() is None:
                        child.process.kill()
                self._lock.close()
        return 1 if self.reason == "shutdown_timeout" else result
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Копия статуса с возрастом последнего снимка журнала
    #--------------------------------------------------------------------------------------------------------------
    def _component_status(
        self,
        child: ChildState,
        ) -> dict:

        """
        Age cached journal observations without querying a child or changing its heartbeat.

        :param child: Current owned component state.
        :type child: ChildState

        :return: Independent sanitized component snapshot.
        :rtype: dict
        """

        # child — после последнего heartbeat возраст продолжает расти на часах
        # manager. Ни смерть процесса, ни отсутствие новых сообщений его не обнуляют.
        value = status_message(child.message)
        if "journals" in value:
            elapsed = max(0, int(monotonic() - child.contact)) if child.contacted else 0
            journals = value["journals"]
            for item in (journals["hub"], *journals["sources"]):
                if item["age_seconds"] is not None:
                    item["age_seconds"] = min(2**31 - 1, item["age_seconds"] + elapsed)
                if value["state"] in ("failed", "stopped", "stopping", "starting", "restarting"):
                    item["state"] = "unavailable"
        return dict(value, restarts=child.restarts)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Открытие локального управления
    #--------------------------------------------------------------------------------------------------------------
    async def _open_control(
        self,
        ) -> None:

        """
        Bind a numeric loopback endpoint and publish one protected generation token.
        """

        from aiohttp import web

        app = web.Application(client_max_size=16384)
        app.router.add_post("/control", self._control)
        app.router.add_post("/child/{name}", self._heartbeat)
        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=1,
                                     handler_cancellation=True, keepalive_timeout=2,
                                     max_line_size=1024, max_field_size=1024)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self._port = site._server.sockets[0].getsockname()[1]
        record = {"port": self._port, "token": self._token, "generation": self.generation}
        temporary = self.config.control_dir / (self.generation + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                if os.name != "nt":
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(record))
            temporary.replace(self.config.control_dir / "runtime.json")
        finally:
            temporary.unlink(missing_ok=True)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Проверка токена и ограниченное чтение локального запроса
    #--------------------------------------------------------------------------------------------------------------
    async def _read(
        self,
        request: object,
        token: str,
        limit: int = 4096,
        ) -> dict:

        """
        Authenticate before bounded body reads and reject unrelated browser requests.

        :param request: Private aiohttp request.
        :type request: object

        :param token: Expected secret for the precise current owner.
        :type token: str

        :param limit: Body bound for control or the larger sanitized child heartbeat.
        :type limit: int

        :return: Small decoded object.
        :rtype: dict
        """

        # request/token — только numeric loopback; Origin/redirect не создают browser API.
        # limit — управляющий запрос остаётся 4 KiB, heartbeat допускает 16 KiB.
        from aiohttp import web

        supplied = request.headers.get("Authorization", "")
        if (request.remote != "127.0.0.1" or "Origin" in request.headers or not token
                or not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode())):
            raise web.HTTPForbidden(text="denied")
        if self._active >= 8:
            raise web.HTTPServiceUnavailable(text="busy")
        if request.content_length is not None and request.content_length > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=request.content_length)
        self._active += 1
        try:
            data = await asyncio.wait_for(request.read(), 2)
            if len(data) > limit:
                raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(data))
            value = json.loads(data)
            if type(value) is not dict:
                raise ValueError("invalid request")
            return value
        except (ValueError, asyncio.TimeoutError):
            raise web.HTTPBadRequest(text="invalid") from None
        finally:
            self._active -= 1
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Статус либо запрос общей остановки текущего запуска
    #--------------------------------------------------------------------------------------------------------------
    async def _control(
        self,
        request: object,
        ) -> object:

        """
        Serve cached status or signal common shutdown for the current generation.

        :param request: Authenticated local operation candidate.
        :type request: object

        :return: Safe JSON response.
        :rtype: object
        """

        # request — нет произвольных методов, командной строки или путей файлов.
        from aiohttp import web

        value = await self._read(request, self._token)
        if set(value) != {"action", "generation"} or value["generation"] != self.generation:
            raise web.HTTPConflict(text="generation")
        if value["action"] == "stop":
            self.state = "stopping"
            self.stop_event.set()
        elif value["action"] != "status":
            raise web.HTTPBadRequest(text="invalid")
        return web.json_response(self.status())
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Приём heartbeat только от своего текущего дочернего процесса
    #--------------------------------------------------------------------------------------------------------------
    async def _heartbeat(
        self,
        request: object,
        ) -> object:

        """
        Receive event-loop state only from the active incarnation of an owned child.

        :param request: Private child heartbeat.
        :type request: object

        :return: Only the stop flag, never another child's credentials.
        :rtype: object
        """

        # request — старый token после перезапуска не может оживить новый компонент.
        from aiohttp import web

        child = self.children.get(request.match_info["name"])
        if child is None or child.process is None or child.process.poll() is not None:
            raise web.HTTPForbidden(text="denied")
        value = await self._read(request, child.token, 16384)
        try:
            value = status_message(value)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="invalid") from None
        child.contact = monotonic()
        child.contacted = True
        if not child.stopping:
            child.message = value
        return web.json_response({"stop": child.stopping or self.stop_event.is_set()})
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Запуск под контролем ОС до передачи настроек компоненту
    #--------------------------------------------------------------------------------------------------------------
    async def _spawn(
        self,
        child: ChildState,
        ) -> None:

        """
        Start a gated child and assign OS ownership before releasing startup data.

        :param child: Terminated component with a reserved restart, or its first launch.
        :type child: ChildState
        """

        # child — процесс прошлого запуска обязательно завершён до вызова.
        if child.process is not None and child.process.poll() is None:
            raise RuntimeError("ownership")
        child.token = secrets.token_urlsafe(32)
        child.stopping = False
        child.retry_at = None
        child.contact = child.launched = monotonic()
        child.contacted = False
        child.message = {"state": "starting", "reason": None, "time_reason": None,
                         "failure": None, "counters": {}}
        settings = {"name": child.name, "control_port": self._port, "token": child.token,
                    "runtime_dir": str(self.config.control_dir),
                    "heartbeat_interval": self.config.lifecycle.heartbeat_interval,
                    "heartbeat_timeout": self.config.lifecycle.heartbeat_timeout}
        if child.name == "caddy":
            settings.update({key: str(getattr(self.config.caddy, key))
                             for key in ("config", "executable", "data_dir")})
        else:
            component = getattr(self.config, child.name)
            settings.update(config=str(component.config), host=component.host, port=component.port)
        child.group = ProcessGroup()
        try:
            child.process = child.group.spawn([sys.executable, "-m", "remote_watch.gateway_server.child",
                                                str(os.getpid())])
            data = (json.dumps(settings) + "\n").encode()
            if len(data) > 65536:
                raise ValueError("startup size")
            await asyncio.wait_for(asyncio.to_thread(child.process.stdin.write, data), 2)
            child.process.stdin.close()
        except Exception:
            child.group.close()
            if child.process is not None and child.process.poll() is None:
                child.process.kill()
            child.message.update(state="failed", reason="ownership", failure="ownership")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Контроль зависаний и резервирование конечного числа перезапусков
    #--------------------------------------------------------------------------------------------------------------
    async def _tick(
        self,
        started: bool,
        ) -> None:

        """
        Observe liveness and reserve retries only after confirming process death.

        :param started: Whether all components passed the initial readiness barrier.
        :type started: bool
        """

        # started — первый запуск откатывается целиком; позже соседние процессы живут независимо.
        for child in self.children.values():
            now = monotonic()
            if child.retry_at is not None:
                if now >= child.retry_at:
                    child.restarts += 1
                    await self._spawn(child)
                continue
            if child.process is None:
                continue
            alive = child.process.poll() is None
            failure = child.message["failure"]
            if child.message["state"] == "failed" and not alive and child.token == "":
                continue
            if alive and not failure:
                if child.contacted and now - child.contact > self.config.lifecycle.heartbeat_timeout:
                    failure = "hang"
                elif child.message["state"] == "starting" and (
                        now - child.launched > self.config.lifecycle.startup_timeout):
                    failure = "hang"
                else:
                    continue
            failure = failure or "crash"
            if alive:
                # Отказ зависшего компонента не задерживает соседний цикл на graceful timeout.
                child.stopping = True
                child.group.close()
                if child.process.poll() is None:
                    child.process.kill()
                end = monotonic() + self.config.lifecycle.kill_timeout
                while child.process.poll() is None and monotonic() < end:
                    await asyncio.sleep(.05)
            if child.process.poll() is None:
                child.message.update(state="failed", reason="shutdown_timeout", failure="ownership")
                self.reason = "shutdown_timeout"
                self.stop_event.set()
                continue
            child.group.close()
            child.token = ""
            decision = child.budget.record_failure(monotonic(), FailureKind(failure),
                                                    jitter=secrets.randbelow(1001) / 1000)
            if started and decision.delay is not None:
                child.retry_at = monotonic() + decision.delay
                child.message.update(state="restarting", reason=failure, failure=None)
            else:
                child.message.update(state="failed", reason=decision.reason if decision.delay is None else failure,
                                     failure=failure)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Запись переходов состояния и оповещение владельца
    #--------------------------------------------------------------------------------------------------------------
    def _report(
        self,
        ) -> None:

        """
        Emit changed bounded status and feed coalesced owner alerts without recursion.
        """

        snapshot = self.status()
        # Счётчики меняются часто; логируем переходы, а полный снимок доступен через status.
        transition = json.dumps({"state": self.state, "reason": self.reason, "alerts": snapshot["alerts"],
                                 "ui": snapshot["ui"], "logging": snapshot["logging"],
                                 "components": {name: {k: v for k, v in item.items() if k != "counters"}
                                                for name, item in snapshot["components"].items()}}, sort_keys=True)
        if transition != self._last_log:
            self._log.info(json.dumps({"observed_at": datetime.now(timezone.utc).isoformat(),
                                       **json.loads(transition)}))
            self._last_log = transition
        if self._log_handler and self._log_handler.failed:
            self._alerts.observe("manager", "degraded", "logging_failed", monotonic())
        elif self._tray is not None and not self._tray.ready and self.state not in ("stopping", "stopped"):
            self._alerts.observe("manager", "degraded", "tray_unavailable", monotonic())
        else:
            self._alerts.observe("manager", self.state, self.reason, monotonic())
        for name, child in self.children.items():
            self._alerts.observe(name, child.message["state"], child.message["reason"], monotonic())
        if self._tray is not None:
            self._tray.update(self.state)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Общая остановка с конечными сроками ожидания
    #--------------------------------------------------------------------------------------------------------------
    async def _shutdown(
        self,
        ) -> None:

        """
        Apply one graceful deadline, then kill only exact owned process groups.
        """

        self.state = "stopping"
        self.stop_event.set()
        for child in self.children.values():
            child.stopping = True
        self._report()
        end = monotonic() + self.config.lifecycle.shutdown_timeout
        while any(c.process is not None and c.process.poll() is None for c in self.children.values()):
            if monotonic() >= end:
                break
            await asyncio.sleep(.05)
        for child in self.children.values():
            if child.group is not None:
                child.group.close()
            if child.process is not None and child.process.poll() is None:
                child.process.kill()
        end = monotonic() + self.config.lifecycle.kill_timeout
        while any(c.process is not None and c.process.poll() is None for c in self.children.values()):
            if monotonic() >= end:
                self.reason = "shutdown_timeout"
                break
            await asyncio.sleep(.05)
        for child in self.children.values():
            child.token = ""
            child.message.update(state="stopped" if child.process is None or child.process.poll() is not None
                                 else "failed", reason=None, failure=None)
        self.state = "stopped"
        self._report()
        if self._tray is not None:
            await asyncio.to_thread(self._tray.close)
        await self._alerts.close()
        if self._runner is not None:
            await self._runner.cleanup()
        (self.config.control_dir / "runtime.json").unlink(missing_ok=True)
        for handler in self._log.handlers:
            handler.close()
        self._lock.close()
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Локальный status/stop по защищённому файлу текущего запуска.
#------------------------------------------------------------------------------------------------------------------
async def control_request(
    config: DeploymentConfig | Path,
    action: str,
    ) -> dict:

    """
    Contact only the protected local endpoint; stale files never authorize process termination.

    :param config: Deployment settings used only to locate its control directory.
    :type config: DeploymentConfig | Path

    :param action: Explicit status or stop operation.
    :type action: str

    :return: Current manager snapshot; inability to contact it raises a fixed local error.
    :rtype: dict
    """

    # config/action — никаких сигналов по PID из файла, только проверенный текущий owner.
    import aiohttp

    if action not in ("status", "stop"):
        raise ValueError("invalid action")
    try:
        directory = config.control_dir if isinstance(config, DeploymentConfig) else config
        with (directory / "runtime.json").open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("invalid control")
        record = json.loads(raw)
        if (set(record) != {"port", "token", "generation"} or type(record["port"]) is not int
                or not 1 <= record["port"] <= 65535 or type(record["token"]) is not str
                or type(record["generation"]) is not str or len(record["token"]) != 43
                or len(record["generation"]) != 32):
            raise ValueError("invalid control")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3), trust_env=False) as http:
            async with http.post(f"http://127.0.0.1:{record['port']}/control",
                                 headers={"Authorization": "Bearer " + record["token"]},
                                 json={"action": action, "generation": record["generation"]},
                                 allow_redirects=False) as response:
                data = await read_limited(response.content, 16384)
                if response.status != 200 or len(data) > 16384:
                    raise ValueError("control unavailable")
                value = json.loads(data)
                if value.get("generation") != record["generation"]:
                    raise ValueError("stale control")
                return value
    except Exception:
        raise RuntimeError("control_unavailable") from None
#------------------------------------------------------------------------------------------------------------------

# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль manager не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
