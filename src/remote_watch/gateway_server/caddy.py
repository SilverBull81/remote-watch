# Управление только собственным Caddy с проверенным постоянным хранилищем CA.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-164146
#
# Классы:
# -> ManagedCaddy: Проверка хранилища и управление собственным Caddy
#    Конструктор:
#    -> __init__(): Подготовка состояния объекта.
#    Интерфейс:
#    -> start(): Подготовка ресурсов и запуск фоновой работы
#    -> status(): Чтение безопасного состояния компонентов
#    -> close(): Освобождение только собственных ресурсов
#    Служебные методы:
#    -> _spawn(): Запуск под контролем ОС до передачи настроек компоненту
#
# Функции:
# -> check_caddy(): Проверка файлового storage и закрытого admin endpoint.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from time import monotonic

from remote_watch.gateway_server.messages import read_limited
from remote_watch.gateway_server.processes import ProcessGroup

#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка файлового storage и закрытого admin endpoint.
#------------------------------------------------------------------------------------------------------------------
def check_caddy(
    value: object,
    data_dir: Path,
    ) -> str:

    """
    Verify persistent filesystem storage and a numeric loopback admin endpoint.

    :param value: Adapted Caddy JSON kept private.
    :type value: object

    :param data_dir: Explicit existing deployment storage location.
    :type data_dir: Path

    :return: Numeric loopback admin URL.
    :rtype: str
    """

    # value/data_dir — запрещаем запуск с неожиданным storage и создание другого CA.

    if type(value) is not dict:
        raise ValueError("caddy configuration")

    storage = value.get("storage", {})
    if (type(storage) is not dict or storage.get("module") != "file_system"
            or type(storage.get("root")) is not str or not Path(storage["root"]).is_absolute()
            or Path(storage["root"]).resolve() != data_dir.resolve()):
        raise ValueError("caddy storage")

    admin = value.setdefault("admin", {})
    if type(admin) is not dict or admin.get("disabled") or admin.get("enforce_origin"):
        raise ValueError("caddy admin")

    address = admin.get("listen", "127.0.0.1:2019")
    if type(address) is not str:
        raise ValueError("caddy admin")

    host, separator, port = address.rpartition(":")
    if not separator or host not in ("127.0.0.1", "[::1]") or not port.isascii() or not port.isdecimal():
        raise ValueError("caddy admin")

    if not 1 <= int(port) <= 65535:
        raise ValueError("caddy admin")

    admin["listen"] = address
    return "http://" + address
#------------------------------------------------------------------------------------------------------------------


#******************************************************************************************************************
# КЛАССЫ
#******************************************************************************************************************

#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Проверка хранилища и управление собственным Caddy
#------------------------------------------------------------------------------------------------------------------
class ManagedCaddy:
    """
    Own the launched Caddy only; external Caddy never enters this class.
    """

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        settings: dict,
        ) -> None:

        """
        Retain parent settings and prepare one bounded subprocess owner.

        :param settings: Private bootstrap paths supplied by the manager.
        :type settings: dict
        """

        # settings — неизменные установленные executable/config/data_dir, без загрузки binary.

        self.settings = settings
        self.group = ProcessGroup()
        self.process = None
        self.directory = None
        self.url = ""
        self.effective = None
        self._healthy_at = monotonic()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка ресурсов и запуск фоновой работы
    #--------------------------------------------------------------------------------------------------------------
    async def start(
        self,
        ) -> None:

        """
        Adapt within limits, verify storage, then run an immutable private JSON snapshot.
        """

        arguments = [self.settings["executable"], "adapt", "--config", self.settings["config"],
                     "--adapter", "caddyfile"]
        await self._spawn(arguments, True)
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(self.process.stdout.read, 1048577), 20)
            if len(raw) > 1048576:
                raise ValueError("caddy config size")
            code = await asyncio.wait_for(asyncio.to_thread(self.process.wait), 2)
            if code:
                raise ValueError("caddy adaptation")
        finally:
            self.group.close()
            if self.process.poll() is None:
                self.process.kill()
            await asyncio.to_thread(self.process.wait, timeout=5)
            self.process.stdout.close()

        self.effective = json.loads(raw)
        self.url = check_caddy(self.effective, Path(self.settings["data_dir"]))
        self.group = ProcessGroup()
        self.directory = tempfile.TemporaryDirectory(prefix="caddy-", dir=self.settings["runtime_dir"])
        snapshot = Path(self.directory.name) / "effective.json"
        snapshot.write_text(json.dumps(self.effective), encoding="utf-8")
        await self._spawn([self.settings["executable"], "run", "--config", str(snapshot)], False)
        self._healthy_at = monotonic()

        # Ответ /config должен относиться именно к нашему живому процессу и его настройкам.
        for _ in range(40):
            health = await self.status()
            if health["state"] == "ready":
                return
            if self.process.poll() is not None:
                raise ValueError("caddy start")
            await asyncio.sleep(.25)

        raise ValueError("caddy admin unavailable")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Чтение безопасного состояния компонентов
    #--------------------------------------------------------------------------------------------------------------
    async def status(
        self,
        ) -> dict:

        """
        Probe bounded admin responsiveness without reloading or stopping by admin API.

        :return: Fixed current state; ingress TLS remains unverified.
        :rtype: dict
        """

        import aiohttp

        state = {"state": "failed", "reason": "crash", "failure": "crash", "time_reason": None, "counters": {}}
        if self.process.poll() is not None:
            return state

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1), trust_env=False) as http:
                async with http.get(self.url + "/config/", allow_redirects=False) as response:
                    raw = await read_limited(response.content, 1048576)
                    if response.status != 200 or len(raw) > 1048576 or json.loads(raw) != self.effective:
                        raise ValueError("caddy health")
            if self.process.poll() is None:
                self._healthy_at = monotonic()
                state.update(state="ready", reason=None, failure=None)
        except (OSError, ValueError, asyncio.TimeoutError, aiohttp.ClientError):
            state.update(state="degraded", reason="caddy_unavailable", failure=None)
            if monotonic() - self._healthy_at > self.settings["heartbeat_timeout"]:
                state.update(state="failed", failure="hang")

        return state
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Освобождение только собственных ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(
        self,
        ) -> None:

        """
        Release only the exact owned group, preserving persistent CA files.
        """

        self.group.close()
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self.process.wait), 2)
            except asyncio.TimeoutError:
                self.process.kill()

        if self.directory is not None:
            self.directory.cleanup()
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # СЛУЖЕБНЫЙ МЕТОД : Запуск под контролем ОС до передачи настроек компоненту
    #--------------------------------------------------------------------------------------------------------------
    async def _spawn(
        self,
        arguments: list[str],
        capture: bool,
        ) -> None:

        """
        Use a parent-bound binary bootstrap rather than a shell or unmanaged subprocess.

        :param arguments: Fixed Caddy operation and verified paths.
        :type arguments: list[str]

        :param capture: Whether adapt stdout is read with a strict byte limit.
        :type capture: bool
        """

        # arguments/capture — Windows Job наследуется, Linux guard сохраняется через exec.

        self.process = self.group.spawn([sys.executable, "-m", "remote_watch.gateway_server.binary",
                                         str(os.getpid())], capture)
        data = (json.dumps({"arguments": arguments}) + "\n").encode()
        if len(data) > 65536:
            raise ValueError("caddy startup size")

        await asyncio.wait_for(asyncio.to_thread(self.process.stdin.write, data), 2)
        self.process.stdin.close()
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль caddy не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
