"""Minimal Weber companion HTTPS client contracts."""

from __future__ import annotations

import gzip
import io
import json
import time
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

from custom_components.weber_connect.weber_cloud import (
    CloudConfig,
    WeberCloudAuthError,
    WeberCloudClient,
    WeberCloudError,
    resolve_associated_appliance_id,
)

DEVICE_ID = "11" * 16
APPLIANCE_ID = "22" * 16


def test_cloud_config_generates_and_validates_only_runtime_credentials() -> None:
    generated = CloudConfig.generate(DEVICE_ID)
    assert generated.device_id == DEVICE_ID
    assert len(generated.device_password) == 32
    assert generated.appliance_id is None

    parsed = CloudConfig.from_mapping(
        {
            "device_id": DEVICE_ID.upper(),
            "device_password": "password",
            "appliance_id": APPLIANCE_ID.upper(),
            "temperature_unit": "unused legacy option",
        }
    )
    assert parsed == CloudConfig(DEVICE_ID, "password", APPLIANCE_ID)

    assert len(CloudConfig.generate("invalid").device_id) == 32
    with pytest.raises(ValueError, match="device ID"):
        CloudConfig.from_mapping({"device_id": "bad", "device_password": "x"})
    with pytest.raises(ValueError, match="password"):
        CloudConfig.from_mapping({"device_id": DEVICE_ID, "device_password": ""})
    with pytest.raises(ValueError, match="appliance ID"):
        CloudConfig.from_mapping(
            {"device_id": DEVICE_ID, "device_password": "x", "appliance_id": "bad"}
        )


def test_association_resolution_never_guesses_between_multiple_hubs() -> None:
    assert resolve_associated_appliance_id([{"oven_id": APPLIANCE_ID}]) == APPLIANCE_ID
    assert (
        resolve_associated_appliance_id(
            [{"oven_id": APPLIANCE_ID}, {"appliance_id": "33" * 16}],
            APPLIANCE_ID,
        )
        == APPLIANCE_ID
    )
    assert (
        resolve_associated_appliance_id([{"oven_id": APPLIANCE_ID}, {"appliance_id": "33" * 16}])
        is None
    )
    assert resolve_associated_appliance_id([{"oven_id": "invalid"}, {"id": 4}]) is None


def test_authenticate_builds_companion_registration_and_caches_token() -> None:
    client = WeberCloudClient(CloudConfig(DEVICE_ID, "password"))
    payload = {"token": {"access_token": "token", "expires_in": 3600}}
    with patch.object(client, "_open", return_value=json.dumps(payload).encode()) as open_request:
        assert client.authenticate() == "token"
    request = open_request.call_args.args[0]
    assert request.full_url.endswith("/2/devices/register")
    assert request.method == "POST"
    body = json.loads(bytes(request.data).decode())
    assert body["device_id"] == DEVICE_ID
    assert body["password"] == "password"
    assert body["device_type"] == "companion"
    assert client.token() == "token"

    client.close()
    assert client._token is None


def test_token_refresh_and_invalid_authentication_payload() -> None:
    client = WeberCloudClient(CloudConfig(DEVICE_ID, "password"))
    client._token = "expired"
    client._token_expiry = time.time() - 1
    with patch.object(client, "authenticate", return_value="fresh") as authenticate:
        assert client.token() == "fresh"
    authenticate.assert_called_once_with()

    with patch.object(client, "_open", return_value=b"{}"):
        with pytest.raises(WeberCloudAuthError, match="access token"):
            client.authenticate()


def test_associated_appliances_filters_rows_and_authorizes_request() -> None:
    client = WeberCloudClient(CloudConfig(DEVICE_ID, "password"))
    client._token = "token"
    client._token_expiry = time.time() + 3600
    payload = {"devices": [{"oven_id": APPLIANCE_ID}, "invalid"]}
    with patch.object(client, "_open", return_value=json.dumps(payload).encode()) as open_request:
        assert client.associated_appliances() == [{"oven_id": APPLIANCE_ID}]
    request = open_request.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer token"

    with patch.object(client, "_open", return_value=b"[]"):
        with pytest.raises(WeberCloudError, match="unexpected"):
            client.associated_appliances()


def test_associate_validates_and_quotes_verification_code() -> None:
    client = WeberCloudClient(CloudConfig(DEVICE_ID, "password"))
    client._token = "token"
    client._token_expiry = time.time() + 3600
    with patch.object(client, "_open", return_value=b'{"associated": true}') as open_request:
        assert client.associate("ABC_123") == {"associated": True}
    assert open_request.call_args.args[0].full_url.endswith("/ABC_123/companion")
    with pytest.raises(ValueError, match="unsupported"):
        client.associate("not allowed!")


def test_wake_messaging_uses_https_and_bearer_token() -> None:
    client = WeberCloudClient(CloudConfig(DEVICE_ID, "password"))
    client._token = "token"
    client._token_expiry = time.time() + 3600
    with patch.object(client, "_open", return_value=b"") as open_request:
        client.wake_messaging(APPLIANCE_ID)
    request = open_request.call_args.args[0]
    assert request.full_url.startswith("https://messaging.walker-cloud.com/")
    assert request.get_header("Authorization") == "Bearer token"
    with pytest.raises(ValueError, match="appliance ID"):
        client.wake_messaging("bad")


class FakeResponse:
    def __init__(self, body: bytes, *, encoding: str | None = None) -> None:
        self.body = body
        self.headers = {"Content-Encoding": encoding} if encoding else {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_open_enforces_https_and_decodes_gzip() -> None:
    client = WeberCloudClient(CloudConfig(DEVICE_ID, "password"))
    with pytest.raises(WeberCloudError, match="non-HTTPS"):
        client._open(urllib.request.Request("http://example.com"))

    compressed = gzip.compress(b'{"ok": true}')
    with patch(
        "custom_components.weber_connect.weber_cloud.urllib.request.urlopen",
        return_value=FakeResponse(compressed, encoding="gzip"),
    ) as urlopen:
        assert client._open(urllib.request.Request("https://example.com")) == b'{"ok": true}'
    assert urlopen.call_args.kwargs["timeout"] == client.timeout


def test_open_normalizes_http_auth_server_and_network_errors() -> None:
    client = WeberCloudClient(CloudConfig(DEVICE_ID, "password"))
    client._token = "token"
    for code, error_type in (
        (401, WeberCloudAuthError),
        (403, WeberCloudAuthError),
        (500, WeberCloudError),
    ):
        error = urllib.error.HTTPError(
            "https://example.com",
            code,
            "error",
            {},
            io.BytesIO(b"detail"),
        )
        with patch(
            "custom_components.weber_connect.weber_cloud.urllib.request.urlopen",
            side_effect=error,
        ):
            with pytest.raises(error_type, match=str(code)):
                client._open(urllib.request.Request("https://example.com"))
    assert client._token is None

    with patch(
        "custom_components.weber_connect.weber_cloud.urllib.request.urlopen",
        side_effect=OSError("offline"),
    ):
        with pytest.raises(WeberCloudError, match="offline"):
            client._open(urllib.request.Request("https://example.com"))


def test_request_payload_rejects_invalid_json_and_scalar_payloads() -> None:
    client = WeberCloudClient(CloudConfig(DEVICE_ID, "password"))
    with patch.object(client, "_open", return_value=b"not-json"):
        with pytest.raises(WeberCloudError, match="invalid JSON"):
            client._request_payload("GET", "/test", authenticated=False)
    with patch.object(client, "_open", return_value=b"42"):
        with pytest.raises(WeberCloudError, match="unexpected"):
            client._request_payload("GET", "/test", authenticated=False)
