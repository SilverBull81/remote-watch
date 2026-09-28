# Расчёт задержки повторной отправки с ограничением и учётом ответа сервиса.
#
# Version 1.0.0
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 260928-142747
#
# Функции:
# -> retry_delay(): Расчёт full jitter с нижней границей retry-after.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import math

from ._validation import require_int, require_number
from .config import RetryPolicy

#******************************************************************************************************************
# ФУНКЦИИ
#******************************************************************************************************************


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Расчёт full jitter с нижней границей retry-after
#------------------------------------------------------------------------------------------------------------------
def retry_delay(
    policy: RetryPolicy,
    failed_attempt: int,
    retry_after: float | None,
    random_value: float,
    ) -> float:

    """Calculate capped exponential full jitter with a provider-imposed lower bound.

    :param policy: Validated retry settings.
    :type policy: RetryPolicy

    :param failed_attempt: One-based attempt that just failed.
    :type failed_attempt: int

    :param retry_after: Optional minimum provider delay in seconds.
    :type retry_after: float | None

    :param random_value: Injected random fraction in the inclusive interval [0, 1].
    :type random_value: float

    :return: Finite delay; the caller must also enforce TTL and shutdown deadlines.
    :rtype: float
    """

    # policy, failed_attempt - настройки и номер завершившейся попытки.
    # retry_after - минимальная задержка из ответа сервиса, если она задана.
    # random_value - случайная доля; тесты задают её явно для воспроизводимости.

    require_int(failed_attempt, "failed_attempt")
    require_number(random_value, "random_value", allow_zero=True)

    if random_value > 1:
        raise ValueError("random_value must not exceed one")

    if retry_after is not None:
        require_number(retry_after, "retry_after", allow_zero=True)

    # Не вычисляем огромную степень двойки. Разность логарифмов работает даже тогда,
    # когда отношение cap/base уже не помещается в float.
    exponent = failed_attempt - 1
    saturation = math.ceil(math.log2(policy.backoff_cap) - math.log2(policy.backoff_base))

    if exponent >= saturation:
        upper = policy.backoff_cap
    else:
        upper = min(policy.backoff_cap, math.ldexp(policy.backoff_base, exponent))

    # Retry-after может быть больше локального cap: сокращать ожидание, требуемое сервисом, нельзя.
    return max(upper * random_value, retry_after if retry_after is not None else 0.0)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        'Модуль remote_watch._retry не предназначен для прямого запуска.',
    )
#------------------------------------------------------------------------------------------------------------------
