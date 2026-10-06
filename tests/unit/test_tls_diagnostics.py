# Безопасные поля TLS и их восстановление без исходных сетевых исключений.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261006-163320
#
# Тесты:
# -> test_tls_classification(): Коды проверки и запрет произвольного текста.
# -> test_tls_field_validation(): Отсечение непроверенных типов и значений.
# -> test_tls_health_recovery(): Ошибка heartbeat не скрывается успехом другого этапа.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
import ssl

import pytest

from remote_watch import DeliveryResult, DeliveryStatus
from remote_watch._tls import tls_diagnostics
from remote_watch.commands.health import HealthState
from remote_watch.commands.transport import CommandError


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Коды проверки и запрет произвольного текста
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("code", "reason"), [(9, "certificate_not_yet_valid"), (10, "certificate_expired"),
    (20, "issuer_not_found"), (62, "hostname_mismatch"), (64, "hostname_mismatch"),
    (98765, "certificate_verification_failed"), (None, "certificate_verification_failed")])
def test_tls_classification(
    code: int | None,
    reason: str,
) -> None:

    """Retain numeric verification evidence without interpreting arbitrary exception messages.

    :param code: Synthetic OpenSSL number, including an unknown valid number.
    :type code: int | None

    :param reason: Expected fixed public classification.
    :type reason: str
    """

    # code/reason — контролируемые значения; PRIVATE имитирует URL или credential.
    native = ssl.SSLCertVerificationError("PRIVATE certificate_expired")
    native.verify_code = code
    native.verify_message = "PRIVATE hostname mismatch"
    fields = tls_diagnostics(native)
    command = CommandError("unavailable", **fields)
    result = DeliveryResult(status=DeliveryStatus.PERMANENT_FAILURE, **fields)
    assert command.error_kind == result.error_kind == "tls_certificate"
    assert command.verify_code == result.verify_code == code
    assert command.tls_reason == result.tls_reason == reason
    assert "PRIVATE" not in str(command) + repr(result) + repr(fields)
    assert command.__cause__ is command.__context__ is None
    assert tls_diagnostics(OSError("PRIVATE")) == {}
    handshake = CommandError("unavailable", **tls_diagnostics(ssl.SSLError("PRIVATE")))
    assert handshake.error_kind == "tls_handshake" and handshake.verify_code is None
    assert handshake.tls_reason == "handshake_failed"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отсечение непроверенных типов и значений
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [True, -1, 1000000, "PRIVATE", ["PRIVATE"]])
def test_tls_field_validation(value: object) -> None:

    """Reject unbounded verification codes and sanitize mutable public exception attributes.

    :param value: Unsupported primitive or container supplied by custom application code.
    :type value: object
    """

    # value — произвольное значение не должно становиться частью безопасной ошибки.
    command = CommandError("unavailable", error_kind="tls_certificate", verify_code=value)
    assert command.verify_code is None and "PRIVATE" not in str(command)
    assert CommandError("unavailable", error_kind=value, verify_code=10).error_kind is None
    with pytest.raises((ValueError, TypeError)):
        DeliveryResult(status=DeliveryStatus.PERMANENT_FAILURE, error_kind="tls_certificate", verify_code=value)
    with pytest.raises(ValueError):
        DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED, error_kind="tls_certificate")
    with pytest.raises(ValueError):
        DeliveryResult(status=DeliveryStatus.PERMANENT_FAILURE, verify_code=10)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Ошибка heartbeat не скрывается успехом другого этапа
#------------------------------------------------------------------------------------------------------------------
def test_tls_health_recovery() -> None:

    """Keep TLS failure active until the failing stage itself recovers."""

    health = HealthState()
    health.lease(100.0)
    health.pending(False)
    failure = CommandError("unavailable", error_kind="tls_certificate", verify_code=10)
    health.failure("heartbeat", failure, object())
    health.success("poll")
    failed = health.snapshot(1.0, 0.0)
    assert not failed.ready
    assert failed.heartbeat.error_kind == "tls_certificate"
    assert failed.heartbeat.verify_code == 10 and failed.heartbeat.tls_reason == "certificate_expired"
    health.success("heartbeat")
    recovered = health.snapshot(2.0, 0.0)
    assert recovered.ready and recovered.last_error == "unavailable"
    stage = recovered.heartbeat
    assert stage.error_kind is stage.verify_code is stage.tls_reason is None
    assert recovered.heartbeat.recoveries == recovered.heartbeat.failures == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Модуль test_tls_diagnostics не предназначен для прямого запуска. Используйте pytest.")
#------------------------------------------------------------------------------------------------------------------
