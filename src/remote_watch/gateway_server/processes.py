# Владение дочерними процессами и закрытый каталог локального управления.
#
# Version 1.0.1
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261014-124120
#
# Класс:
# -> ProcessGroup: Владение собственными процессами через механизм ОС.
#    Конструктор:
#    -> __init__(): Создание объекта без запуска рабочих ресурсов.
#    Интерфейс:
#    -> spawn(): Запуск процесса с закрытым стартовым каналом.
#    -> close(): Освобождение только собственных ресурсов.
#
# Функции:
# -> parent_guard(): Привязка Linux-процесса к времени жизни родителя.
# -> private_directory(): Ограничение доступа к токену локального управления.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import sys
from pathlib import Path


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Владение собственными процессами через механизм ОС
#------------------------------------------------------------------------------------------------------------------
class ProcessGroup:

    """Own exact process handles; never discover or terminate processes by a port or stale PID."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Create a kill-on-close job before any Windows child is allowed to open resources."""

        self._handle = None
        if os.name == "nt":
            from ctypes import wintypes

            # Раскладка JOBOBJECT_EXTENDED_LIMIT_INFORMATION для поддерживаемого
            # 64-битного CPython. Проверка запрещает молчаливую ошибку ABI на x86.
            if ctypes.sizeof(ctypes.c_void_p) != 8:
                raise RuntimeError("platform_unsupported")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel = kernel
            self._handle = kernel.CreateJobObjectW(None, None)
            limits = ctypes.create_string_buffer(144)
            ctypes.c_uint32.from_buffer(limits, 16).value = 0x2000
            if not self._handle or not kernel.SetInformationJobObject(self._handle, 9, limits, len(limits)):
                self.close()
                raise RuntimeError("ownership_unavailable")
        elif sys.platform != "linux":
            raise RuntimeError("platform_unsupported")
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Запуск процесса с закрытым стартовым каналом
    #--------------------------------------------------------------------------------------------------------------
    def spawn(
        self,
        arguments: list[str],
        capture: bool = False,
    ) -> subprocess.Popen:

        """Start a gated bootstrap with inherited output discarded and no interactive window.

        :param arguments: Library-built argument vector, never a shell command from JSON.
        :type arguments: list[str]


        :param capture: Capture bounded preflight output instead of discarding stdout.
        :type capture: bool


        :return: Exact owned process object with a still-open startup gate on stdin.
        :rtype: subprocess.Popen
        """

        # arguments — только bootstrap библиотеки; ресурсы открываются после
        # помещения в Job и передачи стартового сообщения через закрытый pipe.
        process = subprocess.Popen(arguments, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if self._handle is not None:
            if not self._kernel.AssignProcessToJobObject(self._handle, int(process._handle)):
                process.kill()
                process.wait(timeout=5)
                process.stdin.close()
                raise RuntimeError("ownership_unavailable")
        return process
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Освобождение только собственных ресурсов
    #--------------------------------------------------------------------------------------------------------------
    def close(self) -> None:

        """Close the Windows job, terminating only its own still-running members."""

        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Привязка Linux-процесса к времени жизни родителя.
#------------------------------------------------------------------------------------------------------------------
def parent_guard(parent: int) -> None:

    """Arm Linux parent-death cleanup before reading config or opening a listener.

    :param parent: Expected living bootstrap parent, passed by library code.
    :type parent: int
    """

    # parent — проверяется после prctl, чтобы закрыть гонку смерти родителя.
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
            raise RuntimeError("ownership_unavailable")
    elif os.name != "nt":
        raise RuntimeError("platform_unsupported")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Ограничение доступа к токену локального управления.
#------------------------------------------------------------------------------------------------------------------
def private_directory(
    path: Path,
    operator_sid: str | None = None,
) -> None:

    """Protect control credentials with user/System ACL or POSIX owner-only permissions.

    :param path: Dedicated local control directory, never component data or Caddy storage.
    :type path: Path

    :param operator_sid: Explicit service operator SID with read-only control/log access.
    :type operator_sid: str | None
    """

    # path — не принимаем ссылку как каталог управления; существующие журналы
    # компонентов не затрагиваются. Новые файлы наследуют закрытый Windows DACL.
    # operator_sid — не общий Users/Authenticated Users; доступ только одному оператору.
    if operator_sid is not None and not re.fullmatch(r"S-1-5-21-(?:[0-9]{1,10}-){3}[0-9]{1,10}", operator_sid):
        raise ValueError("operator_sid")
    if path.is_symlink():
        raise RuntimeError("control_permissions")
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
        return

    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    advapi.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    token, size = wintypes.HANDLE(), wintypes.DWORD()
    sid_text, descriptor = wintypes.LPWSTR(), ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise RuntimeError("control_permissions")
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        data = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, data, len(data), ctypes.byref(size)):
            raise RuntimeError("control_permissions")
        sid = ctypes.c_void_p.from_buffer(data)
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise RuntimeError("control_permissions")
        sddl = f"D:P(A;OICI;FA;;;{sid_text.value})(A;OICI;FA;;;SY)"
        if operator_sid is not None and operator_sid != sid_text.value:
            sddl += f"(A;OICI;FR;;;{operator_sid})"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            raise RuntimeError("control_permissions")
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                                               ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise RuntimeError("control_permissions")
        if advapi.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None):
            raise RuntimeError("control_permissions")
    finally:
        if token:
            kernel.CloseHandle(token)
        if sid_text:
            kernel.LocalFree(sid_text)
        if descriptor:
            kernel.LocalFree(descriptor)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Назначение модуля
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль processes не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
