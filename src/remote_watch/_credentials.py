# Проверка источника токена и чтение секретов без раскрытия значений в ошибках.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260930-122644
#
# Классы:
# -> CredentialError: Фиксированное поле и безопасное объяснение ошибки.
#    Конструктор:
#    -> __init__(): Подготовка безопасной ошибки.
#
# Функции:
# -> validate_credentials(): Проверка единственного источника токена без чтения окружения.
# -> resolve_token(): Получение проверенного токена при запуске.
#
# Константы:
# -> CREDENTIAL_HINTS: Допустимые объяснения для CLI.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import os
import re
from typing import Literal

#******************************************************************************************************************
# КОНСТАНТЫ
#******************************************************************************************************************
CREDENTIAL_HINTS = {
    "source": "Укажите ровно один источник: token или token_env.",
    "environment": "Ожидается имя переменной окружения, а не значение токена.",
    "value": "Неверный формат или длина токена.",
}


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Фиксированное поле и безопасное объяснение ошибки
#------------------------------------------------------------------------------------------------------------------
class CredentialError(ValueError):
    """Expose schema labels without including credential values."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(
        self,
        field: str,
        reason: str,
        ) -> None:

        """Restrict diagnostic labels to known constants.

        :param field: Credential field name.
        :type field: str

        :param reason: Fixed explanation identifier.
        :type reason: str
        """

        # field - поле схемы; пользовательские имена не сохраняются.
        # reason - код объяснения из фиксированного набора.

        self.field = field if field in ("token", "token_env") else "token"
        self.reason = reason if reason in CREDENTIAL_HINTS else "value"
        super().__init__(f"credential is missing or invalid: field={self.field} reason={self.reason}")
    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Проверка единственного источника токена без чтения окружения
#------------------------------------------------------------------------------------------------------------------
def validate_credentials(
    token: str | None,
    token_env: str | None,
    kind: Literal["telegram", "ntfy", "gateway"],
    ) -> None:

    """Validate literal credentials immediately and defer environment resolution.

    :param token: Literal secret or None.
    :type token: str | None

    :param token_env: Environment variable name or None.
    :type token_env: str | None

    :param kind: Credential syntax and anonymous access policy.
    :type kind: Literal["telegram", "ntfy", "gateway"]
    """

    # token - значение токена; никогда не подставляется в сообщение ошибки.
    # token_env - имя переменной; её наличие пока не проверяется.
    # kind - формат токена и допустимость отсутствия авторизации.

    if token is not None and token_env is not None:
        raise CredentialError("token", "source")
    if token is None and token_env is None and kind != "ntfy":
        raise CredentialError("token", "source")

    if token_env is not None:
        if not isinstance(token_env, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env) is None:
            raise CredentialError("token_env", "environment")
        return

    # Сохраняем прежние форматы провайдеров и более строгую длину сервисного токена.
    # Пробелы и переводы строк не исправляем молча: неверные credentials отклоняются.
    if token is not None:
        pattern = {"telegram": r"[0-9]+:[A-Za-z0-9_-]+", "ntfy": r"[A-Za-z0-9_-]+",
                   "gateway": r"[A-Za-z0-9_-]{32,512}"}[kind]
        if not isinstance(token, str) or len(token) > 512 or re.fullmatch(pattern, token) is None:
            raise CredentialError("token", "value")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Получение проверенного токена при запуске
#------------------------------------------------------------------------------------------------------------------
def resolve_token(
    token: str | None,
    token_env: str | None,
    kind: Literal["telegram", "ntfy", "gateway"],
    ) -> str | None:

    """Read a referenced secret only at resource startup.

    :param token: Literal secret or None.
    :type token: str | None

    :param token_env: Environment variable name or None.
    :type token_env: str | None

    :param kind: Credential syntax and anonymous access policy.
    :type kind: Literal["telegram", "ntfy", "gateway"]

    :return: Validated secret, or None for anonymous ntfy.
    :rtype: str | None
    """

    # token - токен из настроек.
    # token_env - альтернативная ссылка на окружение процесса.
    # kind - формат проверяемого секрета.

    validate_credentials(token, token_env, kind)
    value = token if token_env is None else os.environ.get(token_env, "")
    validate_credentials(value, None, kind)
    return value
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch._credentials не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
