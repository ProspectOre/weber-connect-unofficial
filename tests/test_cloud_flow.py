from __future__ import annotations

import asyncio
import gzip
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_cloud as cloud  # noqa: E402
import weber_panel as panel  # noqa: E402

DEVICE_ID = "00112233445566778899aabbccddeeff"
APPLIANCE_ID = "ffeeddccbbaa99887766554433221100"
ADDRESS = "AA:BB:CC:DD:EE:FF"


def config(**updates) -> cloud.CloudConfig:
    values = {
        "device_id": DEVICE_ID,
        "device_password": "secret",
        "enabled": True,
        "temperature_unit": "fahrenheit",
        "identity_source": "manual",
    }
    values.update(updates)
    return cloud.CloudConfig.from_mapping(values)


class ScriptedClient(cloud.WeberCloudClient):
    def __init__(self, responses: list[Any], **kwargs) -> None:
        super().__init__(config(), **kwargs)
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None, bool]] = []

    def _request_payload(self, method, path, *, body=None, authenticated=True):
        self.calls.append((method, path, body, authenticated))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)

    def live_status(self, appliance_id: str) -> dict[str, Any]:
        # REST paging tests deliberately don't open a real cloud WebSocket.
        raise cloud.WeberCloudError("live socket disabled in scripted test")


class CloudConfigTests(unittest.TestCase):
    def test_roundtrip_public_view_and_enable_toggle(self) -> None:
        value = config(
            enabled=False,
            temperature_unit="celsius",
            appliance_id=APPLIANCE_ID,
        )
        self.assertFalse(value.enabled)
        self.assertNotIn("device_password", value.public_dict())
        self.assertEqual(value.public_dict()["device_id_suffix"], "ddeeff")
        self.assertTrue(value.with_enabled(True).enabled)
        self.assertTrue(value.public_dict()["appliance_id_available"])
        self.assertEqual(cloud.CloudConfig.from_mapping(value.as_dict()), value)

    def test_validation_rejects_invalid_values(self) -> None:
        bad_rows = [
            {"device_id": "bad", "device_password": "secret"},
            {"device_id": DEVICE_ID, "device_password": ""},
            {"device_id": DEVICE_ID, "device_password": "x" * 257},
            {"device_id": DEVICE_ID, "device_password": "secret", "temperature_unit": "K"},
            {"device_id": DEVICE_ID, "device_password": "secret", "identity_source": "phone"},
            {"device_id": DEVICE_ID, "device_password": "secret", "appliance_id": "bad"},
        ]
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(ValueError):
                cloud.CloudConfig.from_mapping(row)

    def test_generate_reuses_valid_companion_or_creates_id(self) -> None:
        with mock.patch.object(
            cloud.secrets,
            "token_hex",
            side_effect=["a" * 32, "b" * 32, "c" * 32],
        ):
            same = cloud.CloudConfig.generate(DEVICE_ID)
            fresh = cloud.CloudConfig.generate("invalid")
        self.assertEqual(same.device_id, DEVICE_ID)
        self.assertEqual(same.device_password, "a" * 32)
        self.assertEqual(fresh.device_id, "b" * 32)
        self.assertEqual(fresh.device_password, "c" * 32)
        self.assertEqual(fresh.identity_source, "bridge")
        self.assertEqual(same.temperature_unit, "deci_celsius")
        self.assertEqual(fresh.temperature_unit, "deci_celsius")


class TemperatureTests(unittest.TestCase):
    def test_temperature_conversions(self) -> None:
        self.assertEqual(cloud.normalize_cloud_temperature(212, "fahrenheit"), (212.0, 100.0))
        self.assertEqual(cloud.normalize_cloud_temperature(100, "celsius"), (212.0, 100.0))
        self.assertEqual(cloud.normalize_cloud_temperature(1000, "deci_celsius"), (212.0, 100.0))
        self.assertIsNone(cloud.normalize_cloud_temperature(0, "fahrenheit"))
        self.assertIsNone(cloud.normalize_cloud_temperature(True, "fahrenheit"))
        self.assertIsNone(cloud.normalize_cloud_temperature("212", "fahrenheit"))

    def test_snapshot_normalization_filters_bad_rows(self) -> None:
        snapshot = {
            "snapshot_id": 7,
            "server_timestamp": 99,
            "data": {
                "probe_status": [
                    {"index": 0, "temperature": 212},
                    {"index": -1, "temperature": 100},
                    {"index": True, "temperature": 100},
                    {"index": 2, "temperature": 0},
                    "bad",
                ]
            },
        }
        result = cloud.cloud_status_from_snapshot(snapshot, "fahrenheit")
        self.assertEqual(result["probe_count"], 1)
        self.assertEqual(result["probes"][0]["probe_number"], 1)
        self.assertEqual(result["probes"][0]["probe_temp_c"], 100.0)
        self.assertEqual(result["snapshot_id"], 7)

    def test_snapshot_normalizes_cavities_timers_and_notification(self) -> None:
        snapshot = {
            "snapshot_id": 8,
            "data": {
                "cavity_status": [
                    {"index": 0, "temperature": 350},
                    {"index": True, "temperature": 225},
                    {"index": 1, "temperature": 0},
                    "bad",
                ],
                "timer_status": [
                    {"index": 0, "id": "timer-a", "duration": 30_400},
                    {"index": 1, "duration": -1_000},
                    {"index": True, "duration": 1_000},
                    {"index": 2, "duration": False},
                    "bad",
                ],
                "notification": {"type": "ready"},
            },
        }

        result = cloud.cloud_status_from_snapshot(snapshot, "fahrenheit")

        self.assertEqual(
            result["cavities"],
            [{"cavity_number": 1, "temperature_f": 350.0, "temperature_c": 176.7}],
        )
        self.assertEqual(result["timers"][0]["remaining_s"], 30)
        self.assertEqual(result["timers"][1]["remaining_s"], 0)
        self.assertEqual(result["notification"], {"type": "ready"})

    def test_snapshot_handles_missing_data(self) -> None:
        self.assertEqual(cloud.cloud_status_from_snapshot({}, "fahrenheit")["probes"], [])
        self.assertEqual(
            cloud.cloud_status_from_snapshot({"data": {"probe_status": {}}}, "fahrenheit")[
                "probe_count"
            ],
            0,
        )

    def test_resolves_single_or_expected_associated_appliance(self) -> None:
        other = "11" * 16
        self.assertEqual(
            cloud.resolve_associated_appliance_id([{"oven_id": APPLIANCE_ID}]),
            APPLIANCE_ID,
        )
        self.assertEqual(
            cloud.resolve_associated_appliance_id(
                [{"oven_id": other}, {"oven_id": APPLIANCE_ID}],
                APPLIANCE_ID,
            ),
            APPLIANCE_ID,
        )
        self.assertIsNone(
            cloud.resolve_associated_appliance_id(
                [{"oven_id": other}, {"oven_id": APPLIANCE_ID}]
            )
        )


class HttpClientTests(unittest.TestCase):
    def test_request_json_builds_authenticated_request(self) -> None:
        client = cloud.WeberCloudClient(config())
        client._token = "token"
        client._token_expiry = 10**12
        with mock.patch.object(client, "_open", return_value=b'{"ok": true}') as opened:
            payload = client._request_json("POST", "/path", body={"x": 1})
        request = opened.call_args.args[0]
        self.assertTrue(payload["ok"])
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual(json.loads(request.data), {"x": 1})

    def test_request_json_rejects_invalid_shapes(self) -> None:
        client = cloud.WeberCloudClient(config())
        for body in (b"bad", b"[]"):
            with mock.patch.object(client, "_open", return_value=body), self.assertRaises(
                cloud.WeberCloudError
            ):
                client._request_json("GET", "/path", authenticated=False)

    def test_request_payload_accepts_top_level_arrays(self) -> None:
        client = cloud.WeberCloudClient(config())
        with mock.patch.object(client, "_open", return_value=b'[{"id": 1}]'):
            payload = client._request_payload("GET", "/path", authenticated=False)
        self.assertEqual(payload, [{"id": 1}])

    def test_open_decodes_gzip(self) -> None:
        encoded = gzip.compress(b'{"ok":true}')

        class Response:
            def __init__(self) -> None:
                self.headers = {"Content-Encoding": "gzip"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return encoded

        client = cloud.WeberCloudClient(config())
        with mock.patch.object(cloud.urllib.request, "urlopen", return_value=Response()):
            result = client._open(cloud.urllib.request.Request("https://example.invalid"))
        self.assertEqual(result, b'{"ok":true}')

    def test_open_maps_http_and_network_errors(self) -> None:
        client = cloud.WeberCloudClient(config())
        for code, expected in ((401, cloud.WeberCloudAuthError), (403, cloud.WeberCloudAuthError), (500, cloud.WeberCloudError)):
            client._token = "cached"
            error = urllib.error.HTTPError(
                "https://example.invalid", code, "bad", {}, io.BytesIO(b"details")
            )
            with self.subTest(code=code), mock.patch.object(
                cloud.urllib.request, "urlopen", side_effect=error
            ), self.assertRaises(expected) as raised:
                client._open(cloud.urllib.request.Request("https://example.invalid"))
            self.assertIn("details", str(raised.exception))
            self.assertEqual(client._token, None if code == 401 else "cached")
        with mock.patch.object(
            cloud.urllib.request, "urlopen", side_effect=OSError("offline")
        ), self.assertRaises(cloud.WeberCloudError):
            client._open(cloud.urllib.request.Request("https://example.invalid"))


class LiveSocketClientTests(unittest.TestCase):
    class FakeSocket:
        def __init__(self) -> None:
            self.closed = False
            self.session_calls: list[tuple[str, dict[str, Any], str]] = []
            self.timer_calls: list[tuple[str, int, str, int]] = []

        def live_status(self, appliance_id: str) -> dict[str, Any]:
            return {
                "appliance_id": appliance_id,
                "active_cook": {"active": True, "title": "Brisket"},
                "timers": [{"timer_number": 4, "remaining_s": 999}],
            }

        def session_command(
            self, appliance_id: str, active_cook: dict[str, Any], command: str
        ) -> None:
            self.session_calls.append((appliance_id, active_cook, command))

        def timer_command(
            self, appliance_id: str, timer_index: int, action: str, duration_s: int
        ) -> None:
            self.timer_calls.append((appliance_id, timer_index, action, duration_s))

        def close(self) -> None:
            self.closed = True

    def test_live_facade_merges_rest_only_fields_and_routes_commands(self) -> None:
        client = cloud.WeberCloudClient(config())
        socket = self.FakeSocket()
        client._socket_client = socket
        history = {
            "cavities": [{"cavity_number": 1, "temperature_f": 350}],
            "timers": [{"timer_number": 1, "remaining_s": 30}],
            "notification": {"type": "ready"},
            "snapshot_id": 9,
            "server_timestamp": 10,
        }

        merged = client._merge_live_status(APPLIANCE_ID, history)
        active_cook = merged["active_cook"]
        client.session_command(APPLIANCE_ID, active_cook, "confirm")
        client.timer_command(APPLIANCE_ID, 1, "start", 30)

        self.assertEqual(merged["kind"], "cloud_live_session")
        self.assertEqual(merged["timers"], history["timers"])
        self.assertEqual(merged["cavities"], history["cavities"])
        self.assertEqual(socket.session_calls, [(APPLIANCE_ID, active_cook, "confirm")])
        self.assertEqual(socket.timer_calls, [(APPLIANCE_ID, 1, "start", 30)])
        self.assertEqual(client.config_host, cloud.API_HOST)
        self.assertEqual(client.user_agent, cloud.USER_AGENT)

        client.close()
        client.close()
        self.assertTrue(socket.closed)
        self.assertIsNone(client._socket_client)

    def test_live_failure_falls_back_and_lazy_socket_is_cached(self) -> None:
        client = cloud.WeberCloudClient(config())
        history = {"kind": "cloud_cook_history", "probes": []}
        with mock.patch.object(
            client, "live_status", side_effect=RuntimeError("socket offline")
        ):
            self.assertIs(client._merge_live_status(APPLIANCE_ID, history), history)
        self.assertEqual(client.socket_error, "socket offline")

        created = object()
        with mock.patch(
            "weber_cloud_socket.WeberCloudSocketClient", return_value=created
        ) as factory:
            self.assertIs(client._socket(), created)
            self.assertIs(client._socket(), created)
        factory.assert_called_once_with(client)


class ApiFlowTests(unittest.TestCase):
    def test_authentication_and_token_cache(self) -> None:
        client = ScriptedClient([{"token": {"access_token": "abc", "expires_in": 1000}}])
        with mock.patch.object(cloud.time, "time", return_value=100):
            self.assertEqual(client.authenticate(), "abc")
            self.assertEqual(client.token(), "abc")
        method, path, body, authenticated = client.calls[0]
        self.assertEqual((method, path, authenticated), ("POST", "/2/devices/register", False))
        self.assertEqual(body["device_id"], DEVICE_ID)

    def test_authentication_rejects_missing_token_and_handles_odd_expiry(self) -> None:
        client = ScriptedClient([{}, {"access_token": "abc", "expires_in": "bad"}])
        with self.assertRaises(cloud.WeberCloudAuthError):
            client.authenticate()
        self.assertEqual(client.authenticate(), "abc")

    def test_associated_appliances_filters_response(self) -> None:
        client = ScriptedClient([{"devices": [{"id": 1}, "bad"]}, {"devices": {}}])
        self.assertEqual(client.associated_appliances(), [{"id": 1}])
        self.assertEqual(client.associated_appliances(), [])

    def test_associate_validates_and_posts_quoted_code(self) -> None:
        client = ScriptedClient([{"success": True}])
        self.assertTrue(client.associate("A_B-2")["success"])
        self.assertIn("A_B-2", client.calls[0][1])
        with self.assertRaises(ValueError):
            client.associate("bad code")

    def test_latest_session_selection_and_empty_shapes(self) -> None:
        client = ScriptedClient(
            [
                {"sessions": [{"session_id": "old", "updated_at": "9"}, {"id": "new", "updated_at": "10"}]},
                [{"session_id": "array", "updated_at": "11"}],
                {"sessions": []},
                {"sessions": ["bad"]},
            ]
        )
        self.assertEqual(client.latest_session_id("hub"), "new")
        self.assertEqual(client.latest_session_id("hub"), "array")
        self.assertIsNone(client.latest_session_id("hub"))
        self.assertIsNone(client.latest_session_id("hub"))

    def test_snapshot_paging_and_invalid_page(self) -> None:
        first_page = [{"snapshot_id": value} for value in range(1, 1001)]
        client = ScriptedClient([{"snapshots": first_page}, {"snapshots": [{"snapshot_id": 1001}]}])
        rows = client.snapshots("hub", "session", 0)
        self.assertEqual(len(rows), 1001)
        self.assertIn("after_id=1000", client.calls[1][1])

        invalid = ScriptedClient([{"snapshots": {}}])
        with self.assertRaises(cloud.WeberCloudError):
            invalid.snapshots("hub", "session", 0)

    def test_snapshot_paging_stops_without_numeric_ids(self) -> None:
        client = ScriptedClient([{"snapshots": [{"snapshot_id": True}, {"x": 1}]}])
        self.assertEqual(len(client.snapshots("hub", "session", -1)), 2)

    def test_snapshot_paging_stops_when_full_page_does_not_advance(self) -> None:
        client = ScriptedClient([{"snapshots": [{"snapshot_id": 1}] * 1000}])
        self.assertEqual(len(client.snapshots("hub", "session", 1)), 1000)

    def test_poll_tracks_session_cursor_and_normalizes(self) -> None:
        client = ScriptedClient(
            [
                {"sessions": [{"session_id": "cook"}]},
                {"snapshots": [{"snapshot_id": 4, "data": {"probe_status": [{"index": 0, "temperature": 212}]}}]},
                {"sessions": [{"session_id": "cook"}]},
                {"snapshots": []},
                {"sessions": []},
            ]
        )
        with mock.patch.object(cloud.time, "monotonic", side_effect=[100, 120]):
            result = client.poll("hub")
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.after_id, 4)
            self.assertEqual(result.status["probes"][0]["probe_temp_c"], 100.0)
            cached = client.poll("hub")
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached.snapshot_count, 0)
        self.assertIn("after_id=4", client.calls[3][1])
        self.assertIsNone(client.poll("hub"))

    def test_poll_expires_cached_snapshot_after_grace(self) -> None:
        client = ScriptedClient(
            [
                {"sessions": [{"session_id": "cook"}]},
                {"snapshots": [{"snapshot_id": 1}]},
                {"sessions": [{"session_id": "cook"}]},
                {"snapshots": []},
            ]
        )
        with mock.patch.object(cloud.time, "monotonic", side_effect=[100, 161]):
            self.assertIsNotNone(client.poll("hub"))
            self.assertIsNone(client.poll("hub"))

    def test_poll_uses_live_socket_when_history_has_not_advanced(self) -> None:
        client = ScriptedClient(
            [
                {"sessions": [{"session_id": "cook"}]},
                {"snapshots": []},
            ]
        )
        live = {
            "probe_count": 1,
            "probes": [{"probe_number": 1, "probe_temp_f": 145}],
            "active_cook": {"active": True, "title": "Baby Back Ribs"},
        }
        with mock.patch.object(client, "live_status", return_value=live):
            result = client.poll("hub")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status["active_cook"]["title"], "Baby Back Ribs")
        self.assertEqual(result.status["kind"], "cloud_live_session")
        self.assertEqual(result.snapshot_count, 0)


class FakeCloudClient:
    def __init__(self, cloud_config: cloud.CloudConfig) -> None:
        self.config = cloud_config
        self.authentications = 0
        self.associations: list[str] = []
        self.access_checks: list[str] = []
        self.polls: list[str] = []
        self.appliances: list[dict] = [{"oven_id": APPLIANCE_ID}]
        self.poll_result: cloud.CloudPollResult | None = cloud.CloudPollResult(
            status=cloud.cloud_status_from_snapshot(
                {
                    "snapshot_id": 9,
                    "data": {"probe_status": [{"index": 0, "temperature": 212}]},
                },
                "fahrenheit",
            ),
            session_id="cook-1",
            after_id=9,
            snapshot_count=2,
        )
        self.error: Exception | None = None
        self.access_error: Exception | None = None
        self.session_commands: list[tuple[str, dict, str]] = []
        self.timer_commands: list[tuple[str, int, str, int]] = []

    def authenticate(self) -> str:
        self.authentications += 1
        if self.error:
            raise self.error
        return "token"

    def associated_appliances(self) -> list[dict]:
        if self.error:
            raise self.error
        return self.appliances

    def associate(self, code: str) -> dict:
        if self.error:
            raise self.error
        self.associations.append(code)
        if not self.appliances:
            self.appliances.append({"oven_id": APPLIANCE_ID})
        return {"success": True}

    def latest_session_id(self, appliance_id: str) -> str | None:
        if self.error:
            raise self.error
        if self.access_error:
            raise self.access_error
        self.access_checks.append(appliance_id)
        return "cook-1"

    def poll(self, appliance_id: str) -> cloud.CloudPollResult | None:
        if self.error:
            raise self.error
        self.polls.append(appliance_id)
        return self.poll_result

    def session_command(self, appliance_id: str, active_cook: dict, command: str) -> None:
        self.session_commands.append((appliance_id, active_cook, command))

    def timer_command(
        self, appliance_id: str, timer_index: int, action: str, duration_s: int
    ) -> None:
        self.timer_commands.append((appliance_id, timer_index, action, duration_s))


class CloudPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.clients: list[FakeCloudClient] = []
        self.default_appliances: list[dict] = [{"oven_id": APPLIANCE_ID}]

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def make_controller(
        self,
        *,
        paired: bool = True,
        read_status=None,
        **dependency_overrides,
    ) -> panel.HubController:
        def factory(value: cloud.CloudConfig) -> FakeCloudClient:
            client = FakeCloudClient(value)
            client.appliances = list(self.default_appliances)
            self.clients.append(client)
            return client

        dependency_values = {
            "cloud_factory": factory,
            "read_status": read_status or panel.read_status_once,
        }
        dependency_values.update(dependency_overrides)
        dependencies = panel.ControllerDependencies(**dependency_values)
        controller = panel.HubController(self.data_dir, mqtt=None, dependencies=dependencies)
        if paired:
            controller.summary = {
                "companion_id": DEVICE_ID,
                "companion_records": [{"companion_id": DEVICE_ID}],
                "hub": {
                    "display_name": "Weber Connect Hub",
                    "model": "Connect Hub",
                    "ble_address": ADDRESS,
                    "appliance_id": APPLIANCE_ID,
                },
                "pairing_response": {"verification_code": 123456},
            }
            controller.settings = controller.settings.with_address(ADDRESS)
        return controller

    async def test_universal_cloud_pair_registers_before_ble_and_commits_identity(self) -> None:
        new_id = "12" * 16
        keys = {
            "companion_id": new_id,
            "companion_public_key": "34" * 64,
            "companion_private_key": "56" * 64,
            "display_name": "Home Assistant",
        }

        def key_loader(**kwargs):
            panel.write_json_atomic(kwargs["path"], keys)
            return keys

        async def pair_bridge(_args, received_keys):
            self.assertIs(received_keys, keys)
            self.assertEqual(self.clients[-1].authentications, 1)
            self.clients[-1].appliances = [{"oven_id": APPLIANCE_ID}]
            return {
                "pairing_response": {
                    "status": "CONFIRMED",
                    "appliance_id": APPLIANCE_ID,
                    "appliance_public_key": "78" * 64,
                }
            }

        def summary_builder(**kwargs):
            return {
                "companion_id": kwargs["keys"]["companion_id"],
                "companion_records": [
                    {"companion_id": kwargs["keys"]["companion_id"]}
                ],
                "hub": {
                    "ble_address": kwargs["address"],
                    "appliance_id": APPLIANCE_ID,
                },
                "pairing_response": kwargs["pairing_response"],
            }

        controller = self.make_controller(
            key_loader=key_loader,
            pair=pair_bridge,
            summary_builder=summary_builder,
            release=lambda _address: True,
        )
        result = await controller.update_cloud({"action": "pair"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["pairing_started"])
        self.assertEqual(self.clients[-1].authentications, 0)
        while controller.runtime.pairing:
            await asyncio.sleep(0)

        self.assertEqual(self.clients[-1].authentications, 1)
        self.assertEqual(controller.runtime.cloud_state, "ready")
        self.assertEqual(controller.cloud_config.device_id, new_id)
        self.assertEqual(controller.cloud_config.appliance_id, APPLIANCE_ID)
        self.assertEqual(controller.settings.handoff_minutes, 0)
        self.assertTrue(controller.runtime.handoff_active)
        self.assertIsNone(controller.runtime.handoff_until)
        self.assertTrue(controller.key_file.exists())
        self.assertFalse(controller.pending_cloud_key_file.exists())
        await controller.stop()

    async def test_first_run_phone_pair_is_cloud_ready_after_one_hub_confirmation(self) -> None:
        new_id = "9a" * 16
        keys = {
            "companion_id": new_id,
            "companion_public_key": "ab" * 64,
            "companion_private_key": "cd" * 64,
            "display_name": "Home Assistant",
        }

        def key_loader(**kwargs):
            panel.write_json_atomic(kwargs["path"], keys)
            return keys

        async def pair_bridge(_args, received_keys):
            self.assertIs(received_keys, keys)
            self.assertEqual(self.clients[-1].authentications, 1)
            self.clients[-1].appliances = [{"oven_id": APPLIANCE_ID}]
            return {
                "pairing_response": {
                    "status": "CONFIRMED",
                    "appliance_id": APPLIANCE_ID,
                    "appliance_public_key": "ef" * 64,
                }
            }

        def summary_builder(**kwargs):
            return {
                "companion_id": kwargs["keys"]["companion_id"],
                "companion_records": [
                    {"companion_id": kwargs["keys"]["companion_id"]}
                ],
                "hub": {
                    "ble_address": kwargs["address"],
                    "appliance_id": APPLIANCE_ID,
                },
                "pairing_response": kwargs["pairing_response"],
            }

        controller = self.make_controller(
            paired=False,
            key_loader=key_loader,
            pair=pair_bridge,
            summary_builder=summary_builder,
            release=lambda _address: True,
        )
        result = await controller.pair(ADDRESS, phone_coexistence=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["phone_coexistence"])
        self.assertEqual(controller.runtime.cloud_state, "pairing")
        while controller.runtime.pairing:
            await asyncio.sleep(0)

        self.assertTrue(controller.paired)
        self.assertEqual(controller.runtime.cloud_state, "ready")
        self.assertEqual(controller.cloud_config.device_id, new_id)
        self.assertEqual(controller.cloud_config.appliance_id, APPLIANCE_ID)
        self.assertTrue(controller.key_file.exists())
        self.assertFalse(controller.pending_cloud_key_file.exists())
        await controller.stop()

    async def test_first_run_phone_pair_cleans_pending_identity_on_prepare_failure(self) -> None:
        def key_loader(**kwargs):
            keys = {
                "companion_id": "7b" * 16,
                "companion_public_key": "8c" * 64,
                "companion_private_key": "9d" * 64,
                "display_name": "Home Assistant",
            }
            panel.write_json_atomic(kwargs["path"], keys)
            return keys

        def failing_factory(_config):
            raise RuntimeError("registration unavailable")

        controller = self.make_controller(
            paired=False,
            key_loader=key_loader,
            cloud_factory=failing_factory,
        )
        result = await controller.pair(ADDRESS, phone_coexistence=True)

        self.assertFalse(result["ok"])
        self.assertIn("Could not prepare phone coexistence", result["error"])
        self.assertFalse(controller.pending_cloud_key_file.exists())
        self.assertFalse(controller.runtime.pairing)
        await controller.stop()

    async def test_create_test_toggle_remove_and_private_snapshot(self) -> None:
        controller = self.make_controller()
        created = await controller.update_cloud({"action": "create"})
        self.assertTrue(created["ok"])
        self.assertEqual(created["associated_appliances"], 1)
        self.assertTrue(controller.cloud_file.exists())
        self.assertNotIn("device_password", created["cloud"])
        self.assertEqual(self.clients[-1].authentications, 1)
        self.assertEqual(self.clients[-1].access_checks, [APPLIANCE_ID])

        tested = await controller.update_cloud({"action": "test"})
        self.assertTrue(tested["ok"])
        self.assertEqual(self.clients[-1].authentications, 2)
        self.assertEqual(self.clients[-1].access_checks, [APPLIANCE_ID, APPLIANCE_ID])
        self.assertTrue((await controller.snapshot())["cloud"]["appliance_id_available"])

        controller.runtime.last_source = "cloud"
        controller.runtime.last_read_ok = True
        disabled = await controller.update_cloud({"action": "disable"})
        self.assertFalse(disabled["cloud"]["enabled"])
        self.assertFalse(controller._can_cloud())
        self.assertFalse(controller.runtime.last_read_ok)
        enabled = await controller.update_cloud({"action": "enable"})
        self.assertTrue(enabled["cloud"]["enabled"])

        removed = await controller.update_cloud({"action": "remove"})
        self.assertTrue(removed["ok"])
        self.assertFalse(controller.cloud_file.exists())
        self.assertEqual(controller.runtime.cloud_state, "unconfigured")

    async def test_opt_in_remote_commands_are_validated_and_routed(self) -> None:
        controller = self.make_controller()
        self.assertTrue((await controller.update_cloud({"action": "create"}))["ok"])
        self.assertTrue(
            (await controller.update_settings({"remote_controls_enabled": True}))["ok"]
        )
        active_cook = {
            "active": True,
            "program_id": "12345678-1234-5678-9abc-def012345678",
            "plan_id": 42,
            "session_type_value": 1,
            "session_index": 0,
            "step_id": 7,
        }
        controller.runtime.last_good_state = {"status": {"active_cook": active_cook}}

        self.assertTrue(
            (
                await controller.panel_command(
                    {"type": "cook", "action": "confirm"}
                )
            )["ok"]
        )
        self.assertTrue(
            (
                await controller.panel_command(
                    {
                        "type": "timer",
                        "action": "start",
                        "number": 2,
                        "duration_s": 30,
                    }
                )
            )["ok"]
        )
        self.assertTrue(
            (
                await controller.panel_command(
                    {"type": "timer", "action": "reset", "number": 2}
                )
            )["ok"]
        )

        client = self.clients[-1]
        self.assertEqual(client.session_commands, [(APPLIANCE_ID, active_cook, "confirm")])
        self.assertEqual(
            client.timer_commands,
            [(APPLIANCE_ID, 1, "start", 30), (APPLIANCE_ID, 1, "reset", 0)],
        )
        self.assertIsNotNone(controller.runtime.control_last_command_at)

        with self.assertRaises(ValueError):
            await controller.remote_command(
                "weber_connect/test/command/cook/stop", "confirm"
            )
        self.assertIn("does not match", controller.runtime.control_error or "")
        with self.assertRaisesRegex(ValueError, "Unsupported panel"):
            await controller.panel_command({"type": "grill", "action": "ignite"})

        self.assertTrue((await controller.update_cloud({"action": "disable"}))["ok"])
        self.assertFalse(controller.settings.remote_controls_enabled)
        with self.assertRaises(ValueError):
            await controller.remote_command(
                "weber_connect/test/command/timer/1/reset", "reset"
            )
        await controller.stop()

    async def test_remote_command_rejects_every_unsafe_shape_and_records_failure(self) -> None:
        controller = self.make_controller()
        with self.assertRaisesRegex(ValueError, "disabled"):
            await controller.remote_command("root/command/timer/1/start", "30")

        self.assertTrue((await controller.update_cloud({"action": "create"}))["ok"])
        self.assertTrue(
            (await controller.update_settings({"remote_controls_enabled": True}))["ok"]
        )
        saved_config = controller.cloud_config
        assert saved_config is not None

        controller.cloud_config = None
        with self.assertRaisesRegex(ValueError, "Cloud access"):
            await controller.remote_command("root/command/timer/1/start", "30")
        controller.cloud_config = saved_config

        controller.cloud_config = config(appliance_id=None)
        assert controller.summary is not None
        controller.summary["hub"].pop("appliance_id", None)
        with self.assertRaisesRegex(ValueError, "no cloud appliance ID"):
            await controller.remote_command("root/command/timer/1/start", "30")
        controller.cloud_config = saved_config

        cases = [
            ("root/no-command", "30", "Invalid command topic"),
            ("root/command/cook/confirm", "confirm", "No active cook"),
            ("root/command/timer/5/start", "30", "between 1 and 4"),
            ("root/command/timer/1/start", "30.5", "whole number"),
            ("root/command/timer/1/reset", "start", "reset payload"),
            ("root/command/grill/ignite", "ignite", "Unsupported"),
        ]
        for topic, payload, message in cases:
            with self.subTest(topic=topic), self.assertRaisesRegex(ValueError, message):
                await controller.remote_command(topic, payload)
            self.assertIsNotNone(controller.runtime.control_error)

        active_cook = {"active": True, "plan_id": 42}
        controller.runtime.last_good_state = {"status": {"active_cook": active_cook}}
        client = self.clients[-1]
        with mock.patch.object(
            client, "session_command", side_effect=RuntimeError("cloud rejected command")
        ), self.assertRaisesRegex(RuntimeError, "cloud rejected"):
            await controller.remote_command("root/command/cook/stop", "stop")
        self.assertEqual(controller.runtime.control_error, "cloud rejected command")
        await controller.stop()

    async def test_create_automatically_uses_pairing_verification_code(self) -> None:
        self.default_appliances = []
        controller = self.make_controller()
        created = await controller.update_cloud({"action": "create"})
        self.assertTrue(created["ok"])
        self.assertTrue(created["association_attempted"])
        self.assertEqual(self.clients[-1].associations, ["123456"])
        await controller.stop()

    async def test_manual_save_and_association_actions(self) -> None:
        controller = self.make_controller()
        saved = await controller.update_cloud(
            {
                "action": "save",
                "device_id": DEVICE_ID,
                "device_password": "personal-secret",
                "temperature_unit": "deci_celsius",
            }
        )
        self.assertTrue(saved["ok"])
        self.assertEqual(self.clients[-1].config.temperature_unit, "deci_celsius")

        associated = await controller.update_cloud(
            {"action": "associate", "verification_code": "ABC-123"}
        )
        self.assertTrue(associated["ok"])
        self.assertEqual(self.clients[-1].associations, ["ABC-123"])

        associated_from_pairing = await controller.update_cloud({"action": "associate"})
        self.assertTrue(associated_from_pairing["ok"])
        self.assertEqual(self.clients[-1].associations[-1], "123456")
        await controller.stop()

    async def test_cloud_action_validation_and_errors(self) -> None:
        controller = self.make_controller(paired=False)
        self.assertFalse((await controller.update_cloud({}))["ok"])
        self.assertFalse((await controller.update_cloud({"action": "create"}))["ok"])
        self.assertFalse((await controller.update_cloud({"action": "test"}))["ok"])
        self.assertFalse((await controller.update_cloud({"action": "enable"}))["ok"])
        self.assertFalse((await controller.update_cloud({"action": "mystery"}))["ok"])

        controller.cloud_config = config()
        controller._new_cloud_client()
        self.assertFalse((await controller.update_cloud({"action": "associate"}))["ok"])
        self.clients[-1].error = RuntimeError("cloud down")
        failed = await controller.update_cloud({"action": "test"})
        self.assertFalse(failed["ok"])
        self.assertEqual(controller.runtime.cloud_state, "error")
        await controller.stop()

    async def test_cloud_test_rejects_authenticated_but_unauthorized_identity(self) -> None:
        controller = self.make_controller()
        controller.cloud_config = config()
        client = controller._new_cloud_client()
        client.access_error = cloud.WeberCloudAuthError("Weber cloud returned HTTP 403")
        failed = await controller.update_cloud({"action": "test"})
        self.assertFalse(failed["ok"])
        self.assertIn("not authorized for this hub", failed["error"])
        self.assertEqual(controller.runtime.cloud_state, "error")
        await controller.stop()

    async def test_loads_valid_and_reports_invalid_cloud_file(self) -> None:
        panel.write_json_atomic(self.data_dir / "cloud_credentials.json", config().as_dict())
        loaded = self.make_controller(paired=False)
        self.assertEqual(loaded.runtime.cloud_state, "ready")
        await loaded.stop()

        self.data_dir.joinpath("cloud_credentials.json").write_text("[]", encoding="utf-8")
        with self.assertLogs("weber_connect_panel", level="WARNING"):
            invalid = self.make_controller(paired=False)
        self.assertEqual(invalid.runtime.cloud_state, "error")
        await invalid.stop()

    async def test_load_migrates_bridge_temperature_unit(self) -> None:
        old_config = config(identity_source="bridge").as_dict()
        panel.write_json_atomic(self.data_dir / "cloud_credentials.json", old_config)
        loaded = self.make_controller(paired=False)
        self.assertIsNotNone(loaded.cloud_config)
        assert loaded.cloud_config is not None
        self.assertEqual(loaded.cloud_config.temperature_unit, "deci_celsius")
        persisted = json.loads(
            (self.data_dir / "cloud_credentials.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["temperature_unit"], "deci_celsius")
        await loaded.stop()

    async def test_ble_failure_falls_back_to_cloud(self) -> None:
        async def fail_ble(**_kwargs):
            raise RuntimeError("radio down")

        controller = self.make_controller(read_status=fail_ble)
        controller.cloud_config = config()
        with self.assertLogs("weber_connect_panel", level="WARNING"):
            self.assertTrue(await controller._read_cycle_once())
        self.assertEqual(controller.runtime.last_source, "cloud")
        self.assertEqual(controller.runtime.cloud_state, "online")
        self.assertEqual(controller.runtime.cloud_after_id, 9)
        self.assertEqual(self.clients[-1].polls, [APPLIANCE_ID])
        saved = json.loads(controller.status_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], "cloud")
        await controller.stop()

    async def test_handoff_uses_cloud_without_touching_ble(self) -> None:
        async def unexpected_ble(**_kwargs):
            raise AssertionError("BLE should remain released")

        controller = self.make_controller(read_status=unexpected_ble)
        controller.cloud_config = config()
        controller.runtime.handoff_active = True
        self.assertTrue(await controller._read_cycle_once())
        self.assertEqual(controller.runtime.last_source, "cloud")
        await controller.stop()

    async def test_cloud_idle_is_healthy_and_keeps_normal_polling(self) -> None:
        controller = self.make_controller()
        controller.cloud_config = config()
        controller.runtime.handoff_active = True
        controller._new_cloud_client().poll_result = None
        self.assertTrue(await controller._read_cycle_once())
        self.assertEqual(controller.runtime.cloud_state, "idle")
        self.assertIsNone(controller.runtime.cloud_error)
        self.assertTrue(controller.runtime.last_read_ok)
        self.assertIsNone(controller.runtime.last_error)
        self.assertEqual(controller.runtime.consecutive_failures, 0)
        self.assertEqual(controller.runtime.last_good_state["active_cook"], {})
        await controller.stop()

    async def test_cloud_poll_failure_and_missing_appliance(self) -> None:
        controller = self.make_controller()
        controller.cloud_config = config()
        controller.runtime.handoff_active = True
        client = controller._new_cloud_client()
        client.error = RuntimeError("service unavailable")
        with self.assertLogs("weber_connect_panel", level="WARNING"):
            self.assertFalse(await controller._read_cycle_once())
        self.assertEqual(controller.runtime.cloud_state, "error")

        controller.summary["hub"].pop("appliance_id")
        with self.assertRaises(RuntimeError):
            await controller._read_cloud_once()
        controller.summary = None
        self.assertFalse(await controller._accept_status({}, source="cloud"))
        await controller.stop()


if __name__ == "__main__":
    unittest.main()
