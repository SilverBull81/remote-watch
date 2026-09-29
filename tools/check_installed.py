# Проверка установленного пакета без импорта исходников из checkout.
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
import asyncio
import importlib.metadata
import importlib.util
import logging
import sys
import threading
from pathlib import Path


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запуск воспроизводимой проверки
#------------------------------------------------------------------------------------------------------------------
def main() -> int:

    """Check an isolated installed distribution before adding test dependencies.

    :return: The value described by this operation.
    :rtype: int
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("core", "extras"))
    parser.add_argument("version")
    args = parser.parse_args()
    threads, handlers = tuple(threading.enumerate()), tuple(logging.getLogger().handlers)

    # -I и отдельный venv исключают src и пользовательские site-packages.
    import remote_watch
    from remote_watch.adapters.ntfy import NtfyChannel, NtfyConfig
    from remote_watch.adapters.relay import RelayChannel, RelayConfig
    from remote_watch.adapters.telegram import TelegramChannel, TelegramConfig
    from remote_watch.gateway import Gateway
    from remote_watch.gateway_json import load_gateway_config

    assert Path(sys.prefix).resolve() in Path(remote_watch.__file__).resolve().parents
    assert Path(remote_watch.__file__).with_name("py.typed").is_file()
    assert importlib.metadata.version("remote-watch") == args.version
    assert "aiohttp" not in sys.modules
    assert tuple(threading.enumerate()) == threads
    assert tuple(logging.getLogger().handlers) == handlers
    assert importlib.util.find_spec("cryptography") is None
    assert (importlib.util.find_spec("aiohttp") is not None) == (args.mode == "extras")
    assert all("extra ==" in item for item in importlib.metadata.requires("remote-watch") or [])

    # Импорт всех интерфейсов не должен сам создавать ресурсы или читать токены.
    assert all(item is not None for item in (RelayChannel, RelayConfig, TelegramChannel,
                                            TelegramConfig, Gateway, load_gateway_config))

    #--------------------------------------------------------------------------------------------------------------
    # ФУНКЦИЯ : Проверка необязательных зависимостей и очистки
    #--------------------------------------------------------------------------------------------------------------
    async def lifecycle() -> None:

        """Open and close an anonymous channel or verify its missing-extra error."""

        channel = NtfyChannel(NtfyConfig(topic="synthetic", token_env=None))
        try:
            if args.mode == "core":
                try:
                    await channel.open()
                except ImportError:
                    pass
                else:
                    raise AssertionError("core unexpectedly opened an HTTP client")
            else:
                await channel.open()
        finally:
            await channel.close()
    #--------------------------------------------------------------------------------------------------------------

    asyncio.run(lifecycle())
    print("Installed distribution, lazy imports and optional dependencies: OK")
    return 0
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТОЧКА ВХОДА : Явный запуск проверки
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
#------------------------------------------------------------------------------------------------------------------
