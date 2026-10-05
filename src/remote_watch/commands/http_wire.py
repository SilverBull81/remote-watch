# Строгая упаковка ответа long poll поверх командного протокола.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261005-200546
#
# Функции:
# -> encode_response(): Кодирование сообщения либо предложения команды.
# -> decode_response(): Строгий разбор ограниченного ответа.
# -> decode_error(): Проверка безопасного кода отказа и его HTTP-статуса.
# -> _unique(): Запрет повторных ключей во всех объектах JSON.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import json

from remote_watch.commands.protocol import CommandMessage, CommandRequest, decode_command, encode_command
from remote_watch.commands.transport import CommandError, CommandOffer

MAX_HTTP_BYTES = 70000
MAX_HTTP_ERROR_BYTES = 256
# Одна таблица для сервера и клиента: код нельзя принимать при чужом HTTP-статусе.
ERROR_HTTP_STATUS = {
    "denied": 403, "invalid": 400, "busy": 429,
    "conflict": 409, "stale_session": 409, "already_started": 409,
    "outcome_conflict": 409, "expired": 409,
    "unavailable": 503, "capacity": 503, "closed": 503,
}
OPERATIONS = frozenset({"register", "heartbeat", "poll", "claim", "result", "release"})


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
# ФУНКЦИЯ : Проверка безопасного кода отказа и его HTTP-статуса
#------------------------------------------------------------------------------------------------------------------
def decode_error(
    data: bytes,
    status: int,
) -> CommandError:

    """Accept only a bounded error envelope matching the server status contract.

    :param data: Bounded UTF-8 body, or empty bytes when the response cannot be trusted.
    :type data: bytes

    :param status: Observed HTTP response status.
    :type status: int

    :return: Safe error with a known code and the observed numeric status.
    :rtype: CommandError
    """

    # data — тело без доверия к произвольным полям; status — уже полученный HTTP-статус.
    # Пустое/чужое тело сохраняет прежнюю классификацию, включая ответы reverse proxy.
    code = {401: "denied", 403: "denied", 409: "conflict", 429: "busy"}.get(status, "unavailable")

    try:
        if type(data) is not bytes or len(data) > MAX_HTTP_ERROR_BYTES:
            raise ValueError("invalid error response size")
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
        if (type(value) is dict and set(value) == {"code"} and type(value["code"]) is str
                and ERROR_HTTP_STATUS.get(value["code"]) == status):
            code = value["code"]
    except (ValueError, TypeError, RecursionError):
        # Ни текст JSON, ни исключение декодера не становятся причиной публичной ошибки.
        pass

    return CommandError(code, http_status=status)
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
