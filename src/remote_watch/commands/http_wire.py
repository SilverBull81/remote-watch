# Строгая упаковка ответа long poll поверх командного протокола.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Функции:
# -> encode_response(): Кодирование сообщения либо предложения команды.
# -> decode_response(): Строгий разбор ограниченного ответа.
# -> _unique(): Запрет повторных ключей во всех объектах JSON.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json

from remote_watch.commands.protocol import CommandMessage, CommandRequest, decode_command, encode_command
from remote_watch.commands.transport import CommandError, CommandOffer

MAX_HTTP_BYTES = 70000
OPERATIONS = frozenset({"register", "heartbeat", "poll", "claim", "result"})


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Кодирование сообщения либо предложения команды
#------------------------------------------------------------------------------------------------------------------
def encode_response(message: CommandMessage | CommandOffer) -> bytes:

    """Encode a command response or a bounded polling envelope.

    :param message: Typed request for the selected command operation.
    :type message: CommandMessage | CommandOffer

    :return: UTF-8 protocol response encoded for the command HTTP endpoint.
    :rtype: bytes
    """

    # message — типизированное сообщение выбранной командной операции.

    if type(message) is not CommandOffer:
        return encode_command(message)
    return json.dumps({"offer": json.loads(encode_command(message.request)),
                       "sequence": message.sequence, "remaining_ttl": message.remaining_ttl},
                      separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Строгий разбор ограниченного ответа
#------------------------------------------------------------------------------------------------------------------
def decode_response(data: bytes) -> CommandMessage | CommandOffer:

    """Reject malformed or oversized responses before constructing protocol objects.

    :param data: Bounded UTF-8 response bytes.
    :type data: bytes

    :return: Validated response message or bounded polling offer.
    :rtype: CommandMessage | CommandOffer
    """

    # data — байты ответа; размер проверяется до разбора JSON.

    try:
        if type(data) is not bytes or len(data) > MAX_HTTP_BYTES:
            raise ValueError("invalid response size")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
        if type(value) is dict and "offer" in value:
            if set(value) != {"offer", "sequence", "remaining_ttl"}:
                raise ValueError("invalid offer")
            request = decode_command(json.dumps(value["offer"], allow_nan=False).encode())
            if type(request) is not CommandRequest:
                raise ValueError("invalid request")
            return CommandOffer(sequence=value["sequence"], remaining_ttl=value["remaining_ttl"], request=request)
        return decode_command(data)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise CommandError("invalid") from None
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Запрет повторных ключей во всех объектах JSON
#------------------------------------------------------------------------------------------------------------------
def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:

    """Reject repeated JSON keys at every nesting level.

    :param pairs: Field pairs from one JSON object.
    :type pairs: list[tuple[str, object]]

    :return: Object with distinct keys at this JSON nesting level.
    :rtype: dict[str, object]
    """

    # pairs — пары полей одного JSON-объекта, включая возможные повторы.

    result = {}

    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result
#------------------------------------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль http_wire не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
