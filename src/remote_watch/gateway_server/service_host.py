# Headless host службы: готовность SCM и штатная остановка единственного manager.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-124120
#
# Классы:
# -> TransientServiceFailure: Временный startup отказ после полного rollback manager.
#
# Функции:
# -> supervise(): Связь событий SCM с обычным manager без второго event loop владельца.
# -> run_service(): Подключение текущего Python-процесса к SCM через optional pywin32.
#
# Внутренний класс функции run_service:
# -> Host: Один host SCM без запуска GUI.
#    Конструктор:
#    -> __init__(): Регистрация обработчика до загрузки конфигов.
#    Интерфейс:
#    -> SvcStop(): Штатная остановка по запросу SCM.
#    -> SvcShutdown(): Остановка при завершении Windows.
#    -> GetAcceptedControls(): Приём предварительной остановки ОС.
#    -> SvcOtherEx(): Общая остановка для PRESHUTDOWN.
#    -> SvcInterrogate(): Повтор текущего состояния без ложного RUNNING.
#    -> SvcRun(): Работа службы до полной остановки manager.
#    Служебные методы:
#    -> _report(): Согласованный статус и checkpoint SCM.
#    -> _watch_stop(): Конечные сроки запуска и остановки собственного host.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from dataclasses import replace
from time import monotonic

from .config import load_deployment_config
from .manager import GatewayManager
from .service import ServiceSpec


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Временный отказ начального запуска после полного отката manager
#------------------------------------------------------------------------------------------------------------------
class TransientServiceFailure(RuntimeError):
    """Request the finite SCM crash recovery policy only after owned resources were released."""
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Связь событий SCM с обычным manager без второго event loop владельца
#------------------------------------------------------------------------------------------------------------------
async def supervise(
    spec: ServiceSpec,
    stopped: threading.Event,
    report: Callable[[str], None],
) -> int:

    """Run one headless manager and preserve its original lifecycle and ownership.

    :param spec: Exact service config and explicit local operator binding.
    :type spec: ServiceSpec
    :param stopped: Thread-safe STOP/SHUTDOWN request from SCM.
    :type stopped: threading.Event
    :param report: Fixed service state reporter with bounded wait hints.
    :type report: Callable[[str], None]
    :return: Original manager shutdown result.
    :rtype: int
    """

    # spec/stopped/report — наличие tray в конфиге относится к сеансу пользователя.
    # Session 0 не импортирует GUI и не зависит от видимости иконки/наличия RDP.
    config = load_deployment_config(spec.config)
    config = replace(config, application=replace(config.application, ui="headless"))
    if stopped.is_set():
        return 0
    manager = GatewayManager(config, operator_sid=spec.operator_sid)
    task = asyncio.create_task(manager.run())
    state = "starting"
    report(state)
    next_report = monotonic() + 1
    try:
        while not task.done():
            if stopped.is_set():
                manager.stop_event.set()
                state = "stopping"
            elif manager.state in ("stopping", "stopped"):
                state = "stopping"
            elif manager.state in ("ready", "degraded"):
                state = "running"
            # Checkpoint растёт лишь во время pending. RUNNING отражает запуск
            # manager, не подтверждает ingress/provider/application readiness.
            if monotonic() >= next_report:
                report(state)
                next_report = monotonic() + 1
            await asyncio.sleep(0.2)
        result = await task
        if result and not stopped.is_set() and manager.reason in (
                "startup_timeout", "time_unavailable", "source_unavailable", "crash", "hang",
                "heartbeat_timeout", "caddy_unavailable", "unavailable"):
            # manager.run уже завершил свой rollback. Повторяется весь запуск
            # ОС-службы с теми же журналами, а не callback/незавершённая команда.
            raise TransientServiceFailure("service_startup_transient")
        return result
    finally:
        manager.stop_event.set()
        try:
            report("stopping")
        except Exception:
            pass
        if not task.done():
            await task
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Подключение текущего Python-процесса к SCM через optional pywin32
#------------------------------------------------------------------------------------------------------------------
def run_service(spec: ServiceSpec) -> int:

    """Host the original venv interpreter directly, avoiding global PythonService DLL setup.

    :param spec: Explicit service name, operator and original config path.
    :type spec: ServiceSpec
    :return: Zero when the SCM dispatcher exits normally.
    :rtype: int
    """

    # spec — никакие credentials не передаются C framework или Event Log.
    import servicemanager
    import win32serviceutil

    # spec — класс создаётся после lazy import, поэтому --help/core и Linux import
    # не требуют Windows DLL. C framework вызывает SvcRun в своём service thread.
    #--------------------------------------------------------------------------------------------------------------
    # КЛАСС : Один host SCM без запуска GUI
    #--------------------------------------------------------------------------------------------------------------
    class Host(win32serviceutil.ServiceFramework):
        """Translate native SCM controls without opening an additional deployment owner."""

        _svc_name_ = spec.name

        #----------------------------------------------------------------------------------------------------------
        # КОНСТРУКТОР
        #----------------------------------------------------------------------------------------------------------
        def __init__(
            self,
            args: list[str],
        ) -> None:

            """Register the control handler before loading deployment resources.

            :param args: SCM-provided service name, never application secrets.
            :type args: list[str]
            """

            # args — базовый класс регистрирует handler; ресурсы открываются в SvcRun.
            self.stopped = threading.Event()
            self.finished = threading.Event()
            self.lock = threading.RLock()
            self.state = "starting"
            self.stop_at = None
            self.stop_budget = 120.0
            self.start_at = monotonic()
            self.start_budget = 120.0
            super().__init__(args)
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Штатная остановка по запросу SCM
        #----------------------------------------------------------------------------------------------------------
        def SvcStop(self) -> None:

            """Signal shutdown promptly without blocking the SCM control handler."""

            self.stopped.set()
            self._report("stopping")
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Остановка при завершении Windows
        #----------------------------------------------------------------------------------------------------------
        def SvcShutdown(self) -> None:

            """Use the identical shutdown path for an operating-system shutdown."""

            self.SvcStop()
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Приём предварительной остановки Windows
        #----------------------------------------------------------------------------------------------------------
        def GetAcceptedControls(self) -> int:

            """Allow a bounded preshutdown interval before Windows' final shutdown deadline.

            :return: STOP, SHUTDOWN and PRESHUTDOWN service controls.
            :rtype: int
            """

            return (1 | 4 | 0x100) if self.state == "running" else 0
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Общая остановка для PRESHUTDOWN
        #----------------------------------------------------------------------------------------------------------
        def SvcOtherEx(
            self,
            control: int,
            event_type: int,
            data: object,
        ) -> None:

            """Handle preshutdown without forwarding opaque event data to logs.

            :param control: Native service control number.
            :type control: int
            :param event_type: Native event subtype, unused.
            :type event_type: int
            :param data: Native opaque payload, deliberately unused.
            :type data: object
            """

            # control/event_type/data — только фиксированный номер влияет на lifecycle.
            if control == 15:
                self.SvcStop()
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Повтор текущего состояния без ложного RUNNING
        #----------------------------------------------------------------------------------------------------------
        def SvcInterrogate(self) -> None:

            """Report the actual pending state rather than the base class's unconditional RUNNING."""

            with self.lock:
                self._report(self.state)
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # ИНТЕРФЕЙС : Работа службы до полной остановки manager
        #----------------------------------------------------------------------------------------------------------
        def SvcRun(self) -> None:

            """Report pending until initial manager startup and sanitize every exception crossing SCM."""

            self._report("starting")
            guard = threading.Thread(target=self._watch_stop, name="gateway-service-stop", daemon=True)
            guard.start()
            try:
                config = load_deployment_config(spec.config)
                self.stop_budget = config.lifecycle.shutdown_timeout + config.lifecycle.kill_timeout + 15
                self.start_budget = config.lifecycle.startup_timeout + 15
                os.chdir(spec.config.parent)
                result = asyncio.run(supervise(spec, self.stopped, self._report))
                if result:
                    raise RuntimeError("gateway_manager_failed")
            except TransientServiceFailure:
                if not self.stopped.is_set():
                    try:
                        servicemanager.LogErrorMsg("Remote Watch gateway service: code=service_startup_transient.")
                    except Exception:
                        pass
                    # Владельцы уже закрыты. Аварийный exit выбирает только
                    # конечные SCM actions; ошибки конфигурации сюда не попадают.
                    os._exit(1)
            except Exception:
                # Event Log не получает JSON, traceback поставщика или токены.
                # C framework фиксирует STOPPED с ненулевым exit code при исключении.
                try:
                    servicemanager.LogErrorMsg("Remote Watch gateway service: code=service_runtime_failed. "
                                              "Run --check-config and inspect gateway_server.jsonl.")
                except Exception:
                    pass
                raise RuntimeError("gateway_service_failed") from None
            finally:
                self.finished.set()
                guard.join(1)
            self._report("stopping")
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # СЛУЖЕБНЫЙ МЕТОД : Согласованный статус и checkpoint SCM
        #----------------------------------------------------------------------------------------------------------
        def _report(
            self,
            state: str,
        ) -> None:

            """Serialize reports from the SCM handler and the manager loop.

            :param state: Fixed starting/running/stopping service state.
            :type state: str
            """

            # state — после STOP никакой запоздалый heartbeat не возвращает RUNNING.
            with self.lock:
                if self.stopped.is_set():
                    state = "stopping"
                self.state = state
                self.ReportServiceStatus({"starting": 2, "running": 4, "stopping": 3}[state], waitHint=5000)
        #----------------------------------------------------------------------------------------------------------

        #----------------------------------------------------------------------------------------------------------
        # СЛУЖЕБНЫЙ МЕТОД : Конечный срок завершения собственного host
        #----------------------------------------------------------------------------------------------------------
        def _watch_stop(self) -> None:

            """Terminate only this service process if graceful stop cannot finish within its bound."""

            while not self.finished.wait(0.5):
                if not self.stopped.is_set() and self.state != "stopping":
                    if self.state == "starting" and monotonic() - self.start_at > self.start_budget:
                        try:
                            servicemanager.LogErrorMsg(
                                "Remote Watch gateway service: code=service_startup_timeout.")
                        except Exception:
                            pass
                        os._exit(1)
                    continue
                if self.stop_at is None:
                    self.stop_at = monotonic()
                if monotonic() - self.stop_at > self.stop_budget:
                    # Последняя защита от зависшего manager: закрытие его handles
                    # завершит собственные Job children. Чужих PID/Caddy не трогаем.
                    try:
                        servicemanager.LogErrorMsg("Remote Watch gateway service: code=service_shutdown_timeout.")
                    except Exception:
                        pass
                    try:
                        self.ReportServiceStatus(1, win32ExitCode=0)
                    finally:
                        os._exit(0)
        #----------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(Host)
    servicemanager.StartServiceCtrlDispatcher()
    return 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль service_host не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
