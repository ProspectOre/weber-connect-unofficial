"""Exercise the installed WebSocket library through the real session connector."""

from __future__ import annotations

import ipaddress
import ssl
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from websockets.asyncio.server import serve

from custom_components.weber_connect.weber_cloud_socket import WeberCloudSession


@pytest.mark.enable_socket
async def test_real_library_connects_with_session_options_and_binary_frames(tmp_path) -> None:
    """Use loopback TLS and synthetic credentials; never contact Weber Cloud."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "loopback-cert.pem"
    key_path = tmp_path / "loopback-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(cert_path, key_path)
    client_ssl = ssl.create_default_context(cafile=str(cert_path))
    headers = []

    async def echo(connection):
        headers.append(connection.request.headers)
        frame = await connection.recv()
        await connection.send(frame)

    class LocalHass:
        async def async_add_executor_job(self, target, *args):
            return target(*args)

    async with serve(echo, "127.0.0.1", 0, ssl=server_ssl) as server:
        port = server.sockets[0].getsockname()[1]
        cloud = SimpleNamespace(
            token=lambda: "synthetic-token",
            token_needs_refresh=lambda: False,
            wake_messaging=lambda _appliance: None,
            messaging_host=f"127.0.0.1:{port}",
            user_agent="weber-compatibility-test",
        )
        session = WeberCloudSession(LocalHass(), cloud, "22" * 16, timeout=3)
        with patch("ssl.create_default_context", return_value=client_ssl):
            connection = await session._async_connect()
        assert await session._async_connect() is connection
        await connection.send(b"synthetic-binary-frame")
        assert await connection.recv() == b"synthetic-binary-frame"
        await session._async_close_connection()
        assert session._connection is None
    assert headers[0]["Authorization"] == "Bearer synthetic-token"
    assert headers[0]["User-Agent"] == "weber-compatibility-test"
