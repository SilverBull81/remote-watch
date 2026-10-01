# Проверки закрытых разрешений и ограничений gateway без сетевых зависимостей.
#
# Version 1.0.3
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Тесты:
# -> configuration(): Конфигурация без запуска фабрики канала.
# -> test_config_bounds(): Отклонение неверных ограничений до запуска.
# -> test_config_grants(): Запрет каскадов relay и неоднозначных прав.
# -> test_config_copy(): Отделение настроек от изменяемых списков приложения.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from remote_watch import (
    DeliveryMode,
    Destination,
    Identity,
    RetryPolicy,
)
from remote_watch.gateway.config import GatewayConfig, GatewayPrincipal


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Конфигурация без запуска фабрики канала
#------------------------------------------------------------------------------------------------------------------
def configuration(identity: Identity) -> GatewayConfig:

    """Create settings whose factory must never run during validation.

    :param identity: Exact synthetic application identity.
    :type identity: Identity

    :return: Configuration using only explicit synthetic test identities and destinations.
    :rtype: GatewayConfig
    """

    # identity - явная принадлежность вымышленного приложения.

    return GatewayConfig(destinations=(Destination(destination_id="phone", channel_factory=object,
        retry=RetryPolicy(max_attempts=1)),), principals=(GatewayPrincipal(name="app", token_env="GW_TOKEN",
        identity=identity, aliases=("phone",)),))
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отклонение неверных ограничений до запуска
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("changes", [{"capacity": 0}, {"capacity": True}, {"capacity": 257},
    {"body_timeout": float("inf")}, {"shutdown_timeout": -1}, {"future_tolerance": 31},
    {"clock_skew_tolerance": -1}, {"clock_skew_tolerance": True}, {"clock_skew_tolerance": "600"},
    {"clock_skew_tolerance": float("inf")}, {"clock_skew_tolerance": float("nan")},
    {"clock_skew_tolerance": None}, {"clock_skew_tolerance": 3601},
    {"destinations": []}, {"principals": []}, {"principals": [object()]}])
def test_config_bounds(
    identity: Identity,
    changes: dict[str, Any],
) -> None:

    """Reject invalid or unbounded server settings before resources exist.

    :param identity: Exact synthetic application identity.
    :type identity: Identity

    :param changes: Explicit configuration overrides.
    :type changes: dict[str, Any]
    """

    # identity - явная принадлежность вымышленного приложения.
    # changes - переопределяемые настройки сценария.

    with pytest.raises((ValueError, TypeError)):
        replace(configuration(identity), **changes)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Запрет каскадов relay и неоднозначных прав
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["cascade", "retry", "alias", "duplicate_alias", "duplicate_principal",
    "unknown_alias", "token_env", "empty_aliases", "principal_capacity"])
def test_config_grants(
    identity: Identity,
    case: str,
) -> None:

    """Reject ambiguous credentials, unauthorized aliases and relay cascades.

    :param identity: Exact synthetic application identity.
    :type identity: Identity

    :param case: Selected test case.
    :type case: str
    """

    # identity - явная принадлежность вымышленного приложения.
    # case - выбранный сценарий проверки.

    config = configuration(identity)
    destination = config.destinations[0]
    principal = config.principals[0]
    with pytest.raises((ValueError, TypeError)):
        if case == "cascade":
            replace(config, destinations=(replace(destination, mode=DeliveryMode.RELAY),))
        elif case == "retry":
            replace(config, destinations=(replace(destination, retry=RetryPolicy()),))
        elif case == "alias":
            replace(config, destinations=(replace(destination, destination_id="https://invalid"),))
        elif case == "duplicate_alias":
            replace(config, destinations=(destination, destination))
        elif case == "duplicate_principal":
            replace(config, principals=(principal, principal))
        elif case == "unknown_alias":
            replace(config, principals=(replace(principal, aliases=("missing",)),))
        elif case == "token_env":
            replace(principal, token_env=None)
        elif case == "empty_aliases":
            replace(principal, aliases=())
        else:
            replace(principal, capacity=257)
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отделение настроек от изменяемых списков приложения
#------------------------------------------------------------------------------------------------------------------
def test_config_copy(identity: Identity) -> None:

    """Detach permissions and destinations from caller-owned mutable lists.

    :param identity: Exact synthetic application identity.
    :type identity: Identity
    """

    # identity - явная принадлежность вымышленного приложения.

    original = configuration(identity)
    destinations = list(original.destinations)
    principals = list(original.principals)
    config = GatewayConfig(destinations=destinations, principals=principals)
    destinations.clear()
    principals.clear()
    assert len(config.destinations) == len(config.principals) == 1
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.unit.test_gateway_config не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
