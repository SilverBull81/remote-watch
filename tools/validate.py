# Воспроизводимая сборка, чистая установка и тесты выбранной среды.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260929-202056
#
# Функции:
# -> main(): Запуск воспроизводимой проверки.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import time
import venv
import zipfile
from pathlib import Path


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запуск воспроизводимой проверки
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """Build a wheel from sdist and test it in a new isolated virtual environment.

    :return: The value described by this operation.
    :rtype: int
    """

    parser = argparse.ArgumentParser(description="Проверка wheel из sdist: core или все extras.")
    parser.add_argument("--mode", choices=("core", "extras"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="Новый каталог внутри build")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if (root / "build").resolve() not in output.parents:
        parser.error("Каталог проверки должен находиться внутри build.")
    output.mkdir(parents=True, exist_ok=False)
    match = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert match is not None
    version = match[1]
    report = {"python": platform.python_version(), "system": platform.system(), "version": version,
              "mode": args.mode, "passed": False, "steps": []}
    environment = os.environ.copy()
    # Ни live opt-in, ни PYTHONPATH родителя не должны менять смысл проверки.
    for name in tuple(environment):
        if name.startswith("REMOTE_WATCH_") or name in ("PYTHONPATH", "PYTHONHOME"):
            environment.pop(name)

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Ограниченный запуск этапа с локальным журналом
    #--------------------------------------------------------------------------------------------------------------
    def run(
        stage: str,
        command: list[str],
        ) -> None:

        """Run one bounded validation stage and retain its local diagnostic log.

        :param stage: Validation stage label.
        :type stage: str

        :param command: Argument list passed without a shell.
        :type command: list[str]
        """

        # stage - название этапа проверки.
        # command - список аргументов без оболочки.

        started = time.monotonic()
        try:
            with (output / (stage + ".log")).open("w", encoding="utf-8") as stream:
                result = subprocess.run(command, cwd=root, env=environment, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=600, check=False)
        except subprocess.TimeoutExpired:
            # Незавершённый этап тоже попадает в отчёт, а не выглядит пропущенным.
            report["steps"].append({"stage": stage, "returncode": None, "timeout": True,
                                    "seconds": round(time.monotonic() - started, 3)})
            raise
        report["steps"].append({"stage": stage, "returncode": result.returncode,
                                "seconds": round(time.monotonic() - started, 3)})
        print(f"{stage}: {'OK' if result.returncode == 0 else 'FAIL'}", flush=True)
        if result.returncode:
            raise RuntimeError(f"Stage failed: {stage}; see the validation output directory")
    #--------------------------------------------------------------------------------------------------------------

    try:
        # Проверяется исходный архив, а устанавливается wheel, заново построенный из него.
        run("build", [sys.executable, "-m", "build", "--outdir", str(output / "dist"), str(root)])
        sdist = next((output / "dist").glob("*.tar.gz"))
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
            assert any(name.endswith("tools/validate.py") for name in names)
            assert any(name.endswith("docs/GATEWAY_SERVER.md") for name in names)
            assert any(name.endswith("tests/contract/test_gateway.py") for name in names)
            assert not any(name.endswith((".local.json", "gateway_config.json", "gateway_settings.py"))
                           or "/build/" in name or "/runs/" in name for name in names)
        run("wheel-from-sdist", [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir",
                                 str(output / "wheel"), str(sdist)])
        wheel = next((output / "wheel").glob("*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            assert all(name.startswith(("remote_watch/", f"remote_watch-{version}.dist-info/"))
                       for name in archive.namelist())
            for name in archive.namelist():
                source = root / "src" / name
                if source.is_file():
                    assert archive.read(name) == source.read_bytes(), name

        venv.EnvBuilder(with_pip=True).create(output / "env")
        python = output / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        suffix = "[telegram,ntfy,relay,gateway]" if args.mode == "extras" else ""
        run("install", [str(python), "-I", "-m", "pip", "install", str(wheel) + suffix])
        run("installed", [str(python), "-I", str(root / "tools/check_installed.py"), args.mode, version])
        run("test-dependencies", [str(python), "-I", "-m", "pip", "install", "pytest>=8", "cryptography>=43"])
        # Отключаем pytest pythonpath=src: тестировать надо установленный wheel.
        run("pytest", [str(python), "-I", "-m", "pytest", "-q", "-o", "pythonpath=", "-m", "not integration",
                       "--junitxml=" + str(output / "tests.xml"), str(root / "tests")])
        report["passed"] = True
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явный запуск проверки
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
