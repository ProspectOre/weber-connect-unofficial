from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

from weber_mqtt import MqttConfig, MqttSession  # noqa: E402
from weber_status_bridge import build_state  # noqa: E402


class FakePublishInfo:
    rc = 0

    def __init__(self) -> None:
        self.timeouts: list[float | None] = []

    def wait_for_publish(self, timeout=None) -> None:
        self.timeouts.append(timeout)

    def is_published(self) -> bool:
        return True


class TimedOutPublishInfo(FakePublishInfo):
    def is_published(self) -> bool:
        return False


class FakeClient:
    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.publications: list[tuple[str, str, int, bool]] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.connect_calls = 0
        self.loop_starts = 0
        self.loop_stops = 0
        self.disconnected = False
        self.will = None

    def username_pw_set(self, username, password) -> None:
        self.credentials = (username, password)

    def will_set(self, topic, payload, qos, retain) -> None:
        self.will = (topic, payload, qos, retain)

    def connect(self, _host, _port, keepalive) -> int:
        self.connect_calls += 1
        self.keepalive = keepalive
        return 0

    def loop_start(self) -> None:
        self.loop_starts += 1
        self.on_connect(self, None, None, 0, None)

    def publish(self, topic, payload, qos, retain):
        self.publications.append((topic, payload, qos, retain))
        return FakePublishInfo()

    def subscribe(self, topic, qos):
        self.subscriptions.append((topic, qos))

    def loop_stop(self) -> None:
        self.loop_stops += 1

    def disconnect(self) -> None:
        self.disconnected = True


class MqttSessionTests(unittest.TestCase):
    def summary(self) -> dict:
        return {
            "companion_id": "00112233445566778899aabbccddeeff",
            "hub": {
                "display_name": "Weber Connect Hub",
                "serial_number": "TESTSERIAL",
                "ble_address": "AA:BB:CC:DD:EE:FF",
            },
        }

    def test_session_reuses_connection_and_publishes_availability(self) -> None:
        clients: list[FakeClient] = []

        def factory(*, client_id: str) -> FakeClient:
            client = FakeClient(client_id)
            clients.append(client)
            return client

        session = MqttSession(
            MqttConfig(host="mqtt.local"),
            self.summary(),
            max_probes=1,
            client_factory=factory,
        )
        state = build_state(
            self.summary(),
            {"probe_count": 0, "probes": []},
            "AA:BB:CC:DD:EE:FF",
            True,
            1,
        )

        async def scenario() -> None:
            await session.publish(state, 30)
            await session.publish(state, 30)
            await session.close()

        asyncio.run(scenario())

        self.assertEqual(len(clients), 1)
        client = clients[0]
        self.assertEqual(client.connect_calls, 1)
        self.assertEqual(client.will[1:], ("offline", 1, True))
        # The hub availability topic reports the live link once per change,
        # not on every publish, and goes offline on close.
        hub_availability = [
            row for row in client.publications if row[0].endswith("/weber_connect_testserial/availability")
        ]
        self.assertEqual([row[1] for row in hub_availability], ["online", "offline"])
        # The absent probe is announced unavailable on its own topic.
        probe_availability = [
            row for row in client.publications if row[0].endswith("/probe_1/availability")
        ]
        self.assertEqual([row[1] for row in probe_availability], ["offline"])
        discovery = [row for row in client.publications if row[0].endswith("/config")]
        # probe1 temp/state + cook monitoring + connectivity + last_publish;
        # all discovery is deduped on republish.
        self.assertEqual(len(discovery), 14)
        temperature = [row for row in discovery if row[0].endswith("_probe_1_temperature/config")]
        self.assertTrue(all("availability_topic" in row[1] for row in temperature))
        self.assertTrue(client.disconnected)

    def _probe_status(self) -> dict:
        return {
            "probe_count": 1,
            "probes": [
                {
                    "probe_number": 1,
                    "probe_temp_f": 120.0,
                    "probe_temp_c": 48.9,
                    "state": "PROBED",
                    "battery_level": 77,
                    "probe_type": "WIRELESS",
                }
            ],
        }

    def test_offline_online_cycle_never_deletes_or_recreates_discovery(self) -> None:
        clients: list[FakeClient] = []

        def factory(*, client_id: str) -> FakeClient:
            client = FakeClient(client_id)
            clients.append(client)
            return client

        session = MqttSession(
            MqttConfig(host="mqtt.local"),
            self.summary(),
            max_probes=1,
            client_factory=factory,
        )
        online = build_state(
            self.summary(),
            self._probe_status(),
            "AA:BB:CC:DD:EE:FF",
            True,
            1,
            probe_names={1: "Brisket"},
        )
        offline = build_state(
            self.summary(),
            {},
            "AA:BB:CC:DD:EE:FF",
            False,
            1,
            probe_names={1: "Brisket"},
        )

        async def scenario() -> None:
            await session.publish(online, 30)
            await session.publish(offline, 30)
            await session.publish(online, 30)
            await session.close()

        asyncio.run(scenario())

        client = clients[0]
        discovery = [row for row in client.publications if row[0].endswith("/config")]
        # No discovery config is ever republished (deduped) and none carries an
        # empty (entity-deleting) payload, so entities survive the cycle.
        self.assertTrue(all(row[1] for row in discovery))
        self.assertEqual(len(discovery), len({row[0] for row in discovery}))
        battery = [row for row in discovery if row[0].endswith("_probe_1_battery/config")]
        self.assertEqual(len(battery), 1)
        self.assertTrue(
            all(
                "Brisket · Probe 1" in json.loads(row[1])["name"]
                for row in discovery
                if "_probe_1_" in row[0]
            )
        )
        hub_availability = [
            row[1]
            for row in client.publications
            if row[0].endswith("/weber_connect_testserial/availability")
        ]
        # Hub availability follows the link: online, offline on disconnect,
        # online again, then offline on close.
        self.assertEqual(hub_availability, ["online", "offline", "online", "offline"])

    def test_discovery_cache_persists_across_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "discovery_cache.json"
            state = build_state(self.summary(), self._probe_status(), "AA:BB:CC:DD:EE:FF", True, 1)

            first_clients: list[FakeClient] = []
            first = MqttSession(
                MqttConfig(host="mqtt.local"),
                self.summary(),
                max_probes=1,
                client_factory=lambda **kw: first_clients.append(FakeClient(kw["client_id"]))
                or first_clients[-1],
                discovery_cache_file=cache_file,
            )

            async def run(session) -> None:
                await session.publish(state, 30)
                await session.close()

            asyncio.run(run(first))
            self.assertTrue(cache_file.exists())
            first_discovery = [r for r in first_clients[0].publications if r[0].endswith("/config")]
            self.assertGreater(len(first_discovery), 0)

            second_clients: list[FakeClient] = []
            second = MqttSession(
                MqttConfig(host="mqtt.local"),
                self.summary(),
                max_probes=1,
                client_factory=lambda **kw: second_clients.append(FakeClient(kw["client_id"]))
                or second_clients[-1],
                discovery_cache_file=cache_file,
            )
            asyncio.run(run(second))
            second_discovery = [r for r in second_clients[0].publications if r[0].endswith("/config")]
            # A restart reannounces retained discovery in case the broker was
            # also restarted; stable unique IDs prevent entity recreation.
            self.assertEqual(len(second_discovery), len(first_discovery))
            self.assertEqual(
                {row[0] for row in second_discovery},
                {row[0] for row in first_discovery},
            )

    def test_custom_topic_template_is_used_for_availability(self) -> None:
        session = MqttSession(
            MqttConfig(host="mqtt.local", topic_prefix="outdoor/{device_id}"),
            self.summary(),
            max_probes=1,
            client_factory=lambda **kwargs: FakeClient(kwargs["client_id"]),
        )
        self.assertEqual(
            session.availability_topic,
            "outdoor/weber_connect_testserial/availability",
        )

    def test_publish_timeout_discards_connection_for_next_retry(self) -> None:
        clients: list[FakeClient] = []

        class TimeoutClient(FakeClient):
            def publish(self, topic, payload, qos, retain):
                self.publications.append((topic, payload, qos, retain))
                return TimedOutPublishInfo()

        def factory(*, client_id: str) -> FakeClient:
            client = TimeoutClient(client_id)
            clients.append(client)
            return client

        session = MqttSession(
            MqttConfig(host="mqtt.local", username="user", password="secret"),
            self.summary(),
            max_probes=1,
            client_factory=factory,
        )
        state = build_state(self.summary(), {}, "AA:BB:CC:DD:EE:FF", False, 1)

        async def scenario() -> None:
            with self.assertRaises(TimeoutError):
                await session.publish(state, 30)
            with self.assertRaises(TimeoutError):
                await session.publish(state, 30)
            await session.close()

        asyncio.run(scenario())

        self.assertEqual(len(clients), 2)
        self.assertTrue(all(client.disconnected for client in clients))
        self.assertEqual(clients[0].credentials, ("user", "secret"))

    def test_command_subscription_decodes_messages_and_rejects_bad_input(self) -> None:
        received: list[tuple[str, str]] = []
        client = FakeClient("commands")
        session = MqttSession(
            MqttConfig(host="mqtt.local"),
            self.summary(),
            max_probes=1,
            client_factory=lambda **_kwargs: client,
            command_handler=lambda topic, payload: received.append((topic, payload)),
        )
        state = build_state(self.summary(), {}, "AA:BB:CC:DD:EE:FF", True, 1)

        async def scenario() -> None:
            await session.publish(state, 10)
            assert client.on_message is not None
            client.on_message(
                client,
                None,
                SimpleNamespace(
                    topic=f"{session.topic_root}/command/timer/1/start",
                    payload=b"30",
                ),
            )
            client.on_message(
                client,
                None,
                SimpleNamespace(topic="bad", payload=b"\xff"),
            )
            await session.close()

        asyncio.run(scenario())

        self.assertEqual(
            client.subscriptions,
            [(f"{session.topic_root}/command/#", 1)],
        )
        self.assertEqual(
            received,
            [(f"{session.topic_root}/command/timer/1/start", "30")],
        )

    def test_disabling_controls_removes_only_control_discovery_once(self) -> None:
        client = FakeClient("control-migration")
        session = MqttSession(
            MqttConfig(host="mqtt.local"),
            self.summary(),
            max_probes=1,
            client_factory=lambda **_kwargs: client,
        )
        status = {
            "active_cook": {"active": True, "title": "Brisket"},
            "probe_count": 0,
            "probes": [],
        }
        enabled = build_state(
            self.summary(),
            status,
            "AA:BB:CC:DD:EE:FF",
            True,
            1,
            remote_controls_enabled=True,
        )
        disabled = build_state(
            self.summary(), status, "AA:BB:CC:DD:EE:FF", True, 1
        )

        async def scenario() -> None:
            await session.publish(enabled, 10)
            await session.publish(disabled, 10)
            first_delete_count = len(
                [row for row in client.publications if row[1] == "" and row[3]]
            )
            await session.publish(disabled, 10)
            self.assertEqual(
                len([row for row in client.publications if row[1] == "" and row[3]]),
                first_delete_count,
            )
            await session.close()

        asyncio.run(scenario())

        deleted = [row for row in client.publications if row[1] == "" and row[3]]
        self.assertEqual(len(deleted), 10)
        self.assertTrue(all(row[0].endswith("/config") for row in deleted))


if __name__ == "__main__":
    unittest.main()
