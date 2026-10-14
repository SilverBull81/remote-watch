# Закрытый bootstrap серверного компонента с heartbeat его рабочего event loop.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-110259
#
# Функции:
# -> failure_kind(): Классификация известных ошибок без их текста.
# -> run_child(): Запуск компонента и heartbeat без вывода приватных исключений.
# -> main(): Привязка к родителю и чтение одноразового стартового сообщения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import asyncio
import json
import sys
from time import monotonic

from remote_watch.commands.source_store import ProcessLock
from remote_watch.commands.storage import StoreConflict, StoreError
from remote_watch.commands.transport import CommandError
from remote_watch.gateway.command_config import load_command_gateway
from remote_watch.gateway.command_service import CommandGateway
from remote_watch.gateway.notification_json_config import load_notification_gateway_config
from remote_watch.gateway.notification_server import NotificationGateway, NotificationGatewayStartupError
from remote_watch.gateway_server.messages import REASONS, read_limited, status_message
from remote_watch.gateway_server.processes import parent_guard


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Классификация известных ошибок без их текста.
#------------------------------------------------------------------------------------------------------------------
def failure_kind(error: BaseException) -> str:

    """Classify known operational failures without inspecting private exception messages.

    :param error: Failure local to this child; never serialized or chained across IPC.
    :type error: BaseException


    :return: Fixed recovery-policy category.
    :rtype: str
    """

    # error — текст и traceback не передаются; неоднозначный сбой остаётся crash.
    if isinstance(error, StoreConflict):
        return "ownership"
    if isinstance(error, StoreError):
        return "storage"
    if isinstance(error, CommandError):
        return {"denied": "authorization", "conflict": "ownership", "unavailable": "storage",
                "capacity": "storage", "invalid": "configuration"}.get(error.code, "crash")
    if isinstance(error, (ValueError, TypeError, ImportError, NotificationGatewayStartupError, OSError)):
        return "configuration"
    return "crash"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запуск компонента и heartbeat без вывода приватных исключений.
#------------------------------------------------------------------------------------------------------------------
async def run_child(settings: dict) -> None:

    """Run one component and publish loop-owned state within bounded private HTTP requests.

    :param settings: Parent-provided startup record read only after ownership was established.
    :type settings: dict
    """

    # settings — токен передан через stdin, а не командную строку/общий config.
    import aiohttp

    name = settings["name"]
    component = None
    lock = None
    startup = None
    current = {"state": "starting", "reason": None, "time_reason": None, "failure": None, "counters": {}}
    url = f"http://127.0.0.1:{settings['control_port']}/child/{name}"
    headers = {"Authorization": "Bearer " + settings["token"]}
    last_contact = monotonic()
    timeout = aiohttp.ClientTimeout(total=min(2, settings["heartbeat_timeout"] / 3))
    try:
        try:
            if name == "commands":
                config = load_command_gateway(settings["config"])
                component = CommandGateway(config)
                startup = asyncio.create_task(component.start(host=settings["host"], port=settings["port"],
                                                               allow_loopback_http=True))
            elif name == "notifications":
                # Lock по конфигу предотвращает второй manager с другим control_dir.
                from pathlib import Path

                lock = ProcessLock(Path(settings["config"]).with_suffix(".gateway.lock"))
                lock.open()
                component = NotificationGateway(load_notification_gateway_config(settings["config"]))
                startup = asyncio.create_task(component.start(host=settings["host"], port=settings["port"]))
            elif name == "caddy":
                from remote_watch.gateway_server.caddy import ManagedCaddy

                component = ManagedCaddy(settings)
                startup = asyncio.create_task(component.start())
            else:
                raise ValueError("invalid component")
        except Exception as error:
            category = failure_kind(error)
            current.update(state="failed", reason=category, failure=category)

        async with aiohttp.ClientSession(timeout=timeout, trust_env=False,
                                         cookie_jar=aiohttp.DummyCookieJar()) as http:
            while True:
                if startup is not None and startup.done():
                    try:
                        startup.result()
                        if name == "commands":
                            health = component.stats()
                            reason = health["reason"]
                            if reason is None and any(not s.get("ready", True) for s in health["sources"]):
                                reason = "source_unavailable"
                            reason = reason if reason in REASONS else ("unavailable" if reason else None)
                            fatal = health["fatal"]
                            failure = "storage" if fatal else None
                            if fatal and reason == "source_stopped":
                                errors = {s.get("last_error") for s in health["sources"] if s.get("closed")}
                                failure = "authorization" if "denied" in errors else "configuration"
                            current.update(state="failed" if fatal else "degraded" if reason else "ready",
                                           reason=reason, time_reason=(health["hub"].get("time_reason")
                                               if health["hub"].get("time_reason") in REASONS else None),
                                           failure=failure, journals=health["journals"])
                        elif name == "notifications":
                            stats = component.stats()
                            current.update(state="ready", reason=None,
                                           counters={key: stats[key] for key in ("active", "destinations_busy")})
                        else:
                            current.update(await component.status())
                    except Exception as error:
                        category = failure_kind(error)
                        current.update(state="failed", reason=category, failure=category)
                try:
                    async with http.post(url, json=status_message(current), headers=headers,
                                         allow_redirects=False) as response:
                        body = await read_limited(response.content, 256)
                        if response.status != 200 or len(body) > 256:
                            break
                        action = json.loads(body)
                        last_contact = monotonic()
                        if action.get("stop") or current["state"] == "failed":
                            break
                except (OSError, ValueError, asyncio.TimeoutError, aiohttp.ClientError):
                    if monotonic() - last_contact >= settings["heartbeat_timeout"]:
                        break
                await asyncio.sleep(settings["heartbeat_interval"])
    finally:
        if startup is not None and not startup.done():
            startup.cancel()
        if startup is not None:
            await asyncio.gather(startup, return_exceptions=True)
        try:
            if component is not None:
                await component.close()
        finally:
            if lock is not None:
                lock.close()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Привязка к родителю и чтение одноразового стартового сообщения.
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """Read a bounded startup gate before any component configuration or provider activity.

    :return: Fixed exit status; private exception details are never printed.
    :rtype: int
    """

    try:
        parent_guard(int(sys.argv[1]))
        data = sys.stdin.buffer.readline(65537)
        if not data.endswith(b"\n") or len(data) > 65536:
            return 1
        settings = json.loads(data)
        asyncio.run(run_child(settings))
        return 0
    except BaseException:
        return 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
