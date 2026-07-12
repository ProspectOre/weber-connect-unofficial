from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

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
        self.publications: list[tuple[str, str, int, bool]] = []
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
        availability = [row for row in client.publications if row[0].endswith("/availability")]
        self.assertEqual([row[1] for row in availability], ["online", "online", "offline"])
        discovery = [row for row in client.publications if row[0].endswith("/config")]
        discovery_payloads = [row[1] for row in discovery if row[1]]
        self.assertEqual(len(discovery), 3)
        self.assertTrue(all("availability_topic" in payload for payload in discovery_payloads))
        self.assertTrue(client.disconnected)

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


if __name__ == "__main__":
    unittest.main()
