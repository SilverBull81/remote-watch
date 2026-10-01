# Настоящий TLS на loopback с временным центром сертификации и проверкой CLI.
#
# Version 1.0.2
#
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
#
# Дата и время последнего изменения: 261001-131902
#
# Классы:
# -> Provider: Счётчик попыток после проверки TLS и прав.
#    Конструктор:
#    -> __init__(): Создание объекта.
#    Интерфейс:
#    -> open(): Подготовка клиента и данных авторизации.
#    -> send(): Одна попытка отправки и проверка ответа.
#    -> close(): Закрытие клиента и освобождение ресурсов.
#
# Тесты:
# -> certificates(): Создание временного центра сертификации и сертификата сервера.
# -> test_gateway_tls_cli(): Настоящее TLS-соединение с сертификатами из CLI.
# -> test_gateway_tls_bad_files(): Отказ запуска при неверных файлах TLS.


#******************************************************************************************************************
# ИМПОРТ
#******************************************************************************************************************
from __future__ import annotations

import ipaddress
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from remote_watch import (
    Delivery,
    DeliveryResult,
    DeliveryStatus,
    Destination,
    RetryPolicy,
)
from remote_watch.adapters.relay import RelayChannel, RelayConfig
from remote_watch.diagnostics.smoke import _notification
from remote_watch.gateway.config import GatewayConfig, GatewayPrincipal
from remote_watch.gateway.server import Gateway, main

aiohttp = pytest.importorskip("aiohttp")


#------------------------------------------------------------------------------------------------------------------
# КЛАСС : Счётчик попыток после проверки TLS и прав
#------------------------------------------------------------------------------------------------------------------
class Provider:
    """Count provider calls reached only after TLS and gateway authorization."""

    #--------------------------------------------------------------------------------------------------------------
    # КОНСТРУКТОР
    #--------------------------------------------------------------------------------------------------------------
    def __init__(self) -> None:

        """Create an empty attempt counter."""

        self.calls = 0
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Подготовка клиента и данных авторизации
    #--------------------------------------------------------------------------------------------------------------
    async def open(self) -> None:

        """Keep startup free from network side effects."""

    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна попытка отправки и проверка ответа
    #--------------------------------------------------------------------------------------------------------------
    async def send(
        self,
        delivery: Delivery,
    ) -> DeliveryResult:

        """Accept one synthetic notification.

        :param delivery: Immutable delivery attempt.
        :type delivery: Delivery

        :return: Sanitized provider result.
        :rtype: DeliveryResult
        """

        # delivery - подготовленные данные одной попытки.

        self.calls += 1
        return DeliveryResult(status=DeliveryStatus.PROVIDER_ACCEPTED)
    #--------------------------------------------------------------------------------------------------------------

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Закрытие клиента и освобождение ресурсов
    #--------------------------------------------------------------------------------------------------------------
    async def close(self) -> None:

        """Keep cleanup deterministic."""

    #--------------------------------------------------------------------------------------------------------------
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ФУНКЦИЯ : Создание временного центра сертификации и сертификата сервера
#------------------------------------------------------------------------------------------------------------------
def certificates(
    tmp_path: Path,
    mode: str,
) -> tuple[Path, Path, Path]:

    """Generate a fresh CA and leaf certificate without modifying system trust stores.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param mode: Selected failure or success scenario.
    :type mode: str

    :return: Paths to the temporary CA certificate, server certificate and private key.
    :rtype: tuple[Path, Path, Path]
    """

    # tmp_path - временный каталог теста.
    # mode - выбранный сценарий ответа сервера.

    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Remote Watch temporary test CA")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=10))
          .not_valid_after(now + timedelta(days=10))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Temporary gateway")])
    san = (x509.DNSName("wrong.invalid") if mode == "wrong_name"
           else x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    certificate = (x509.CertificateBuilder().subject_name(leaf_name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if mode == "expired" else 1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.SubjectAlternativeName([san]), False)
        .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256()))

    # Все ключи создаются заново в tmp_path. Тест не устанавливает корневой сертификат
    # в Windows/Linux и не хранит готовый закрытый ключ в репозитории.
    ca_path, cert_path, key_path = (tmp_path / name for name in ("ca.pem", "server.pem", "key.pem"))
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_path, cert_path, key_path
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Настоящее TLS-соединение с сертификатами из CLI
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["trusted", "untrusted", "expired", "wrong_name", "plaintext"])
def test_gateway_tls_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:

    """Exercise CLI certificate loading and actual TLS verification on both endpoints.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param mode: Selected failure or success scenario.
    :type mode: str
    """

    # tmp_path - временный каталог теста.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # mode - выбранный сценарий ответа сервера.

    ca_path, cert_path, key_path = certificates(tmp_path, mode)
    provider = Provider()
    event = _notification()
    config = GatewayConfig(destinations=(Destination(destination_id="phone", channel_factory=lambda: provider,
        retry=RetryPolicy(max_attempts=1)),), principals=(GatewayPrincipal(name="app",
        token_env="RW_TLS_TEST", identity=event.identity, aliases=("phone",)),))
    monkeypatch.setenv("RW_TLS_TEST", "synthetic_tls_credential_123456789012345")
    module = ModuleType("synthetic_tls_settings")
    module.build_config = lambda: config
    monkeypatch.setitem(sys.modules, module.__name__, module)
    client_context = ssl.create_default_context()
    if mode != "untrusted":
        client_context.load_verify_locations(cafile=str(ca_path))
    assert client_context.check_hostname and client_context.verify_mode == ssl.CERT_REQUIRED
    original_connector = aiohttp.TCPConnector

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Доверие тестовому центру без отключения проверок TLS
    #--------------------------------------------------------------------------------------------------------------
    def connector(**kwargs: Any) -> aiohttp.TCPConnector:

        """Trust only this test's additional CA while preserving all verification checks.

        :param kwargs: Captured request or client keyword arguments.
        :type kwargs: Any

        :return: HTTP connector using the temporary test certificate trust context.
        :rtype: aiohttp.TCPConnector
        """

        # kwargs - именованные параметры запроса или клиента.

        return original_connector(ssl=client_context, **kwargs)
    #--------------------------------------------------------------------------------------------------------------

    # Подменяется лишь источник доверия тестового клиента. Реальный TLS-handshake,
    # проверка срока и имени, HTTP-клиент, ACL и обработка gateway выполняются полностью.
    monkeypatch.setattr(aiohttp, "TCPConnector", connector)
    outcomes: list[DeliveryResult] = []

    #--------------------------------------------------------------------------------------------------------------
    # ИНТЕРФЕЙС : Одна HTTPS-попытка вместо постоянного ожидания CLI
    #--------------------------------------------------------------------------------------------------------------
    async def serve(
        config: GatewayConfig,
        host: str,
        port: int,
        context: ssl.SSLContext | None,
    ) -> None:

        """Replace the endless CLI wait with one real HTTPS relay attempt.

        :param config: Validated provider configuration.
        :type config: GatewayConfig

        :param host: Explicit listener address.
        :type host: str

        :param port: Explicit TCP port.
        :type port: int

        :param context: Configured server TLS context.
        :type context: ssl.SSLContext | None
        """

        # config - проверенные настройки сервиса.
        # host - адрес прослушивания сервера.
        # port - TCP-порт сервера.
        # context - подготовленные настройки TLS сервера.

        assert context is not None
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
        gateway = Gateway(config)
        await gateway.start(host=host, port=port, ssl_context=context)
        scheme = "http" if mode == "plaintext" else "https"
        relay = RelayChannel(RelayConfig(endpoint=f"{scheme}://127.0.0.1:{gateway.port}", alias="phone",
            token_env="RW_TLS_TEST", allow_http=mode == "plaintext"))
        try:
            await relay.open()
            delivery = Delivery(notification=event, destination_id="phone", delivery_id="tls")
            outcomes.append(await relay.send(delivery))
        finally:
            await relay.close()
            await gateway.close()
    #--------------------------------------------------------------------------------------------------------------

    monkeypatch.setattr("remote_watch.gateway.server._serve", serve)
    assert main(["synthetic_tls_settings:build_config", "--port", "0",
                 "--cert", str(cert_path), "--key", str(key_path)]) == 0
    assert len(outcomes) == 1
    if mode == "trusted":
        assert outcomes[0].status is DeliveryStatus.PROVIDER_ACCEPTED and provider.calls == 1
    else:
        assert outcomes[0].status is not DeliveryStatus.PROVIDER_ACCEPTED and provider.calls == 0
        if mode != "plaintext":
            assert outcomes[0].reason_code == "tls_certificate"
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# ТЕСТ : Отказ запуска при неверных файлах TLS
#------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["missing_key", "invalid_cert", "mismatched_key"])
def test_gateway_tls_bad_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:

    """Reject incomplete or invalid TLS configuration before starting the listener.

    :param tmp_path: Temporary directory fixture.
    :type tmp_path: Path

    :param monkeypatch: Pytest patch and environment fixture.
    :type monkeypatch: pytest.MonkeyPatch

    :param mode: Selected failure or success scenario.
    :type mode: str
    """

    # tmp_path - временный каталог теста.
    # monkeypatch - фикстура подмены зависимостей и окружения.
    # mode - выбранный сценарий ответа сервера.

    _, cert_path, key_path = certificates(tmp_path, "trusted")
    module = ModuleType("synthetic_tls_settings")
    event = _notification()
    module.build_config = lambda: GatewayConfig(destinations=(Destination(destination_id="phone",
        channel_factory=Provider, retry=RetryPolicy(max_attempts=1)),), principals=(GatewayPrincipal(name="app",
        token_env="RW_TLS_TEST", identity=event.identity, aliases=("phone",)),))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    if mode == "invalid_cert":
        cert_path.write_text("not a certificate", encoding="ascii")
    elif mode == "mismatched_key":
        key_path.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    arguments = ["synthetic_tls_settings:build_config", "--cert", str(cert_path)]
    if mode != "missing_key":
        arguments += ["--key", str(key_path)]
    serve = AsyncMock()
    monkeypatch.setattr("remote_watch.gateway.server._serve", serve)
    assert main(arguments) == 1
    serve.assert_not_awaited()
#------------------------------------------------------------------------------------------------------------------


#------------------------------------------------------------------------------------------------------------------
# СЛУЖЕБНЫЙ БЛОК : Сообщение о назначении файла
#------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print(
        "Модуль tests.contract.test_gateway_tls не предназначен для прямого запуска. Используйте pytest.",
    )
#------------------------------------------------------------------------------------------------------------------
