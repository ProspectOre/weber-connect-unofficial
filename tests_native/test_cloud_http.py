"""Exhaustive private-cloud transport contracts without external network access."""

from __future__ import annotations

import gzip
import io
import json
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from custom_components.weber_connect import weber_cloud as cloud

DEVICE_ID = "11" * 16
APPLIANCE_ID = "22" * 16


def config(**updates: object) -> cloud.CloudConfig:
    values: dict[str, object] = {
        "device_id": DEVICE_ID,
        "device_password": "private-password",
        "temperature_unit": "deci_celsius",
        "identity_source": "home_assistant",
    }
    values.update(updates)
    return cloud.CloudConfig.from_mapping(values)


class Response:
    """Minimal urlopen response context manager."""

    def __init__(self, body: bytes, *, encoding: str | None = None) -> None:
        self.body = body
        self.headers = {"Content-Encoding": encoding} if encoding else {}

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_cloud_config_rejects_every_invalid_choice_and_supports_immutable_updates() -> None:
    with pytest.raises(ValueError, match="temperature unit"):
        config(temperature_unit="kelvin")
    with pytest.raises(ValueError, match="identity source"):
        config(identity_source="phone")
    with pytest.raises(ValueError, match="appliance ID"):
        config(appliance_id="bad")
    with pytest.raises(ValueError, match="at most 256"):
        config(device_password="x" * 257)

    generated = cloud.CloudConfig.generate("not-a-valid-id")
    assert generated.device_id != "not-a-valid-id"
    assert len(generated.device_id) == 32

    original = config(appliance_id=APPLIANCE_ID)
    assert original.with_enabled(False).enabled is False
    assert original.with_temperature_unit("celsius").temperature_unit == "celsius"
    assert original.with_appliance_id("33:" * 15 + "33").appliance_id == "33" * 16
    with pytest.raises(ValueError, match="temperature unit"):
        original.with_temperature_unit("kelvin")
    with pytest.raises(ValueError, match="appliance ID"):
        original.with_appliance_id("bad")


@pytest.mark.parametrize("raw", [None, True, "25", 0])
def test_invalid_cloud_temperatures_are_ignored(raw: object) -> None:
    assert cloud.normalize_cloud_temperature(raw, "celsius") is None


def test_snapshot_normalization_ignores_malformed_rows_and_clamps_timers() -> None:
    state = cloud.cloud_status_from_snapshot(
        {
            "snapshot_id": 7,
            "server_timestamp": 123,
            "data": {
                "probe_status": [
                    None,
                    {"index": True},
                    {"index": -1},
                    {"index": 0, "temperature": 0},
                ],
                "cavity_status": ["bad", {"index": False}, {"index": 0, "temperature": 250}],
                "timer_status": [
                    None,
                    {"index": True, "duration": 1},
                    {"index": 0, "duration": False},
                    {"index": 1, "duration": -500},
                ],
                "notification": "done",
            },
        },
        "deci_celsius",
    )
    assert state["probes"] == []
    assert state["cavities"][0]["temperature_c"] == 25.0
    assert state["timers"][0]["remaining_s"] == 0
    assert state["notification"] == "done"

    empty = cloud.cloud_status_from_snapshot({"data": "bad"}, "celsius")
    assert empty["probes"] == []
    assert empty["notification"] is None


def test_appliance_resolution_filters_duplicates_and_invalid_values() -> None:
    assert (
        cloud.resolve_associated_appliance_id(
            [
                {"oven_id": None, "id": "bad"},
                {"device_id": "22:" * 15 + "22"},
                {"appliance_id": APPLIANCE_ID},
            ]
        )
        == APPLIANCE_ID
    )
    assert cloud.resolve_associated_appliance_id([], APPLIANCE_ID) is None


def test_open_enforces_https_decodes_gzip_and_classifies_failures() -> None:
    client = cloud.WeberCloudClient(config())
    with pytest.raises(cloud.WeberCloudError, match="non-HTTPS"):
        client._open(urllib.request.Request("http://example.test"))

    compressed = gzip.compress(b"payload")
    with patch.object(
        urllib.request, "urlopen", return_value=Response(compressed, encoding="gzip")
    ):
        assert client._open(urllib.request.Request("https://example.test")) == b"payload"

    for code, error_type in (
        (401, cloud.WeberCloudAuthError),
        (403, cloud.WeberCloudAuthError),
        (500, cloud.WeberCloudError),
    ):
        client._token = "cached"
        error = urllib.error.HTTPError(
            "https://example.test",
            code,
            "failure",
            {},
            io.BytesIO(b"private endpoint rejected request"),
        )
        with patch.object(urllib.request, "urlopen", side_effect=error):
            with pytest.raises(error_type, match=str(code)):
                client._open(urllib.request.Request("https://example.test"))
        assert client._token is None if code == 401 else True

    with patch.object(urllib.request, "urlopen", side_effect=OSError("offline")):
        with pytest.raises(cloud.WeberCloudError, match="Could not reach"):
            client._open(urllib.request.Request("https://example.test"))


def test_json_request_builds_headers_and_rejects_bad_responses() -> None:
    client = cloud.WeberCloudClient(config())
    client._token = "token"
    client._token_expiry = time.time() + 1000
    captured: list[urllib.request.Request] = []

    def open_request(request: urllib.request.Request) -> bytes:
        captured.append(request)
        return b'{"ok": true}'

    with patch.object(client, "_open", side_effect=open_request):
        assert client._request_json("POST", "/path", body={"a": 1}) == {"ok": True}
    request = captured[0]
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer token"
    assert request.get_header("Content-type") == "application/json; charset=UTF-8"
    assert json.loads(request.data or b"{}") == {"a": 1}

    with patch.object(client, "_open", return_value=b"not-json"):
        with pytest.raises(cloud.WeberCloudError, match="invalid JSON"):
            client._request_payload("GET", "/path", authenticated=False)
    with patch.object(client, "_open", return_value=b"1"):
        with pytest.raises(cloud.WeberCloudError, match="unexpected response"):
            client._request_payload("GET", "/path", authenticated=False)
    with patch.object(client, "_request_payload", return_value=[]):
        with pytest.raises(cloud.WeberCloudError, match="unexpected response"):
            client._request_json("GET", "/path")


def test_authentication_cache_association_and_wake_contracts() -> None:
    client = cloud.WeberCloudClient(config())
    with patch.object(
        client,
        "_request_json",
        return_value={"token": {"access_token": "access", "expires_in": "unexpected"}},
    ) as request:
        assert client.authenticate() == "access"
    assert request.call_args.kwargs["authenticated"] is False
    assert client.token() == "access"

    client._token_expiry = 0
    with patch.object(client, "authenticate", return_value="refreshed") as authenticate:
        assert client.token() == "refreshed"
        authenticate.assert_called_once()

    with patch.object(client, "_request_json", return_value={}):
        with pytest.raises(cloud.WeberCloudAuthError, match="access token"):
            client.authenticate()

    with patch.object(
        client,
        "_request_json",
        return_value={"devices": [{"id": 1}, "bad"]},
    ):
        assert client.associated_appliances() == [{"id": 1}]
    with patch.object(client, "_request_json", return_value={"devices": "bad"}):
        assert client.associated_appliances() == []

    with pytest.raises(ValueError, match="unsupported"):
        client.associate("bad code!")
    with patch.object(client, "_request_json", return_value={"associated": True}) as request:
        assert client.associate("abc_123") == {"associated": True}
        assert "abc_123" in request.call_args.args[1]

    with pytest.raises(ValueError, match="appliance ID"):
        client.wake_messaging("bad")
    with (
        patch.object(client, "token", return_value="access"),
        patch.object(client, "_open") as opened,
    ):
        client.wake_messaging(APPLIANCE_ID)
        wake_request = opened.call_args.args[0]
        assert wake_request.get_header("Authorization") == "Bearer access"


def test_socket_lifecycle_and_live_merge_fallbacks() -> None:
    client = cloud.WeberCloudClient(config())
    socket = MagicMock()
    client._socket_client = socket
    socket.live_status.return_value = {"probes": [{"probe_number": 1}]}
    with patch.object(client, "wake_messaging", side_effect=RuntimeError("best effort")):
        assert client.live_status(APPLIANCE_ID)["probes"]

    merged = client._merge_live_status(
        APPLIANCE_ID,
        {"timers": [{"timer_number": 1}], "snapshot_id": 9},
    )
    assert merged["kind"] == "cloud_live_session"
    assert merged["snapshot_id"] == 9

    socket.live_status.side_effect = RuntimeError("socket offline")
    history = {"kind": "cloud_cook_history"}
    assert client._merge_live_status(APPLIANCE_ID, history) is history
    assert client.socket_error == "socket offline"

    client.close()
    socket.close.assert_called_once()
    client.close()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([], None),
        ({"sessions": []}, None),
        ({"items": ["bad"]}, None),
        ([{"session_id": "old", "server_timestamp": "a"}, {"id": "new", "updated_at": 2}], "new"),
        ([{"session_id": "dated", "created_at": "2026-07-19"}], "dated"),
    ],
)
def test_latest_session_accepts_supported_shapes(payload: object, expected: str | None) -> None:
    client = cloud.WeberCloudClient(config())
    with patch.object(client, "_request_payload", return_value=payload):
        assert client.latest_session_id(APPLIANCE_ID) == expected
    if expected is not None:
        assert "fields" in client.session_schema


def test_snapshot_pagination_filters_rows_and_stops_safely() -> None:
    client = cloud.WeberCloudClient(config())
    first_page = [{"snapshot_id": index} for index in range(1, 1001)] + ["bad"]
    second_page = [{"snapshot_id": 1001}]
    with patch.object(
        client,
        "_request_json",
        side_effect=[{"snapshots": first_page}, {"snapshots": second_page}],
    ) as request:
        rows = client.snapshots(APPLIANCE_ID, "session/id", -1)
    assert len(rows) == 1001
    assert "after_id=1000" in request.call_args_list[1].args[1]
    assert client.snapshot_schema["fields"] == ["snapshot_id"]

    with patch.object(client, "_request_json", return_value={"snapshots": "bad"}):
        with pytest.raises(cloud.WeberCloudError, match="snapshot page"):
            client.snapshots(APPLIANCE_ID, "session", 0)

    stalled = [{"snapshot_id": 5} for _ in range(1000)]
    with patch.object(client, "_request_json", return_value={"snapshots": stalled}) as request:
        assert len(client.snapshots(APPLIANCE_ID, "session", 5)) == 1000
        request.assert_called_once()

    no_ids = [{"snapshot_id": "bad"} for _ in range(1000)]
    with patch.object(client, "_request_json", return_value={"snapshots": no_ids}) as request:
        assert len(client.snapshots(APPLIANCE_ID, "session", 0)) == 1000
        request.assert_called_once()


def test_poll_covers_session_reset_live_stale_and_snapshot_paths() -> None:
    client = cloud.WeberCloudClient(config())
    with (
        patch.object(client, "latest_session_id", return_value=None),
        patch.object(
            client,
            "live_status",
            return_value={"probes": [{"probe_number": 3}]},
        ),
    ):
        live_without_history = client.poll(APPLIANCE_ID)
    assert live_without_history is not None
    assert live_without_history.session_id == ""
    assert live_without_history.status["probes"][0]["probe_number"] == 3
    assert live_without_history.status["kind"] == "cloud_live_session"
    assert client._session_id is None

    with (
        patch.object(client, "latest_session_id", return_value=None),
        patch.object(client, "live_status", side_effect=RuntimeError("offline")),
    ):
        assert client.poll(APPLIANCE_ID) is None
        assert client.socket_error == "offline"

    client._session_id = "old"
    client._after_id = 99
    with (
        patch.object(client, "latest_session_id", return_value="new"),
        patch.object(client, "snapshots", return_value=[]),
        patch.object(client, "live_status", return_value={"probes": []}),
    ):
        result = client.poll(APPLIANCE_ID)
    assert result is not None
    assert result.session_id == "new"
    assert result.snapshot_count == 0
    assert result.status["kind"] == "cloud_live_session"

    client._last_status = {"timers": [{"timer_number": 1}], "snapshot_id": 4}
    with (
        patch.object(client, "latest_session_id", return_value="new"),
        patch.object(client, "snapshots", return_value=[]),
        patch.object(client, "live_status", side_effect=RuntimeError("offline")),
        patch.object(cloud.time, "monotonic", return_value=10),
    ):
        client._last_snapshot_at = 5
        stale = client.poll(APPLIANCE_ID)
    assert stale is not None
    assert stale.status["snapshot_id"] == 4

    with (
        patch.object(client, "latest_session_id", return_value="new"),
        patch.object(client, "snapshots", return_value=[]),
        patch.object(client, "live_status", side_effect=RuntimeError("offline")),
        patch.object(cloud.time, "monotonic", return_value=100),
    ):
        client._last_snapshot_at = 1
        assert client.poll(APPLIANCE_ID) is None

    snapshots = [
        {"snapshot_id": True, "data": {}},
        {"snapshot_id": 12, "data": {"probe_status": [{"index": 0, "temperature": 250}]}},
    ]
    with (
        patch.object(client, "latest_session_id", return_value="new"),
        patch.object(client, "snapshots", return_value=snapshots),
        patch.object(client, "_merge_live_status", side_effect=lambda _id, status: status),
        patch.object(cloud.time, "monotonic", return_value=50),
    ):
        result = client.poll(APPLIANCE_ID)
    assert result is not None
    assert result.after_id == 12
    assert result.snapshot_count == 2
    assert result.status["probe_count"] == 1


def test_poll_overlaps_latest_snapshot_for_in_place_cloud_updates() -> None:
    client = cloud.WeberCloudClient(config())
    client._session_id = "active"
    client._after_id = 2080
    with (
        patch.object(client, "latest_session_id", return_value="active"),
        patch.object(client, "snapshots", return_value=[]) as snapshots,
        patch.object(client, "live_status", side_effect=RuntimeError("offline")),
    ):
        client.poll(APPLIANCE_ID)
    snapshots.assert_called_once_with(APPLIANCE_ID, "active", 2079)
