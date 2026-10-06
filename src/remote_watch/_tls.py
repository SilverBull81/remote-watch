# Безопасная классификация отказов TLS без адресов и текста сетевых исключений.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-163320
#
# Функции:
# -> tls_reason(): Фиксированная причина по числовому коду OpenSSL.
# -> tls_diagnostics(): Извлечение разрешённых полей из ошибки стандартного ssl.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import ssl


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Фиксированная причина по числовому коду OpenSSL
#------------------------------------------------------------------------------------------------------------------
def tls_reason(
    verify_code: int | None,
) -> str:

    """Map certificate verification codes without retaining provider text.

    :param verify_code: Sanitized OpenSSL verification number, or None.
    :type verify_code: int | None

    :return: Fixed public reason; unknown numbers remain unclassified.
    :rtype: str
    """

    # verify_code — код проверки всей цепочки, а не номер просроченного сертификата.
    reasons = {9: "certificate_not_yet_valid", 10: "certificate_expired",
               18: "self_signed_certificate", 19: "self_signed_chain",
               20: "issuer_not_found", 21: "issuer_not_found",
               23: "certificate_revoked", 62: "hostname_mismatch", 64: "hostname_mismatch"}
    return reasons.get(verify_code, "certificate_verification_failed")
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Извлечение разрешённых полей из ошибки стандартного ssl
#------------------------------------------------------------------------------------------------------------------
def tls_diagnostics(
    error: BaseException,
) -> dict[str, str | int | None]:

    """Copy bounded TLS metadata without messages, hostnames or exception chains.

    :param error: Native SSL failure unwrapped by the HTTP adapter.
    :type error: BaseException

    :return: Keyword arguments accepted by public transport error models.
    :rtype: dict[str, str | int | None]
    """

    # error — только известный ssl-тип. Текст, reason и verify_message не копируются.
    if isinstance(error, ssl.SSLCertVerificationError):
        number = getattr(error, "verify_code", None)
        return {"error_kind": "tls_certificate",
                "verify_code": number if type(number) is int and 0 <= number <= 999999 else None}
    if isinstance(error, ssl.SSLError):
        return {"error_kind": "tls_handshake", "verify_code": None}
    return {}
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль remote_watch._tls не предназначен для прямого запуска.")
#------------------------------------------------------------------------------------------------------------------
