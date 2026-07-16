from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_mqtt as weber_mqtt  # noqa: E402
from weber_mqtt import MqttConfig, MqttSession  # noqa: E402
from weber_status_bridge import build_state  # noqa: E402


def summary() -> dict:
    return {
        "companion_id": "00112233445566778899aabbccddeeff",
        "hub": {
            "display_name": "Weber Connect Hub",
            "serial_number": "TESTSERIAL",
            "ble_address": "AA:BB:CC:DD:EE:FF",
        },
    }


class Publish:
    rc = 0

    def wait_for_publish(self, timeout=None) -> None:
        pass

    def is_published(self) -> bool:
        return True


class FailingPublish(Publish):
    rc = 5


class BaseClient:
    connect_result = 0
    connect_reason = 0

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self.on_connect = None
        self.on_disconnect = None
        self.publications: list = []
        self.disconnected = False

    def username_pw_set(self, username, password) -> None:
        pass

    def will_set(self, topic, payload, qos, retain) -> None:
        pass

    def connect(self, host, port, keepalive) -> int:
        return self.connect_result

    def loop_start(self) -> None:
        self.on_connect(self, None, None, self.connect_reason, None)

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        self.disconnected = True

    def publish(self, topic, payload, qos, retain):
        self.publications.append((topic, payload, qos, retain))
        return Publish()


def run_publish(session: MqttSession, connected: bool = True) -> None:
    state = build_state(summary(), {}, "AA:BB:CC:DD:EE:FF", connected, 1)

    async def scenario() -> None:
        await session.publish(state, 30)
        await session.close()

    asyncio.run(scenario())


class MqttConfigTests(unittest.TestCase):
    def test_missing_host_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MqttConfig.from_mapping({"host": "  "})

    def test_full_mapping_is_parsed(self) -> None:
        config = MqttConfig.from_mapping(
            {"host": "mqtt.local", "port": "1884", "username": "u", "password": "p"}
        )
        self.assertEqual(config.port, 1884)
        self.assertEqual(config.username, "u")
        self.assertEqual(config.password, "p")


class MqttSessionFlowTests(unittest.TestCase):
    def _session(self, factory, **kwargs) -> MqttSession:
        return MqttSession(
            MqttConfig(host="mqtt.local"),
            summary(),
            max_probes=1,
            client_factory=factory,
            **kwargs,
        )

    def test_unreadable_discovery_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            cache.write_text("[]", encoding="utf-8")  # non-object → ValueError
            session = self._session(BaseClient, discovery_cache_file=cache)
            self.assertEqual(session._discovery_cache, {})

    def test_persist_discovery_cache_failure_is_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            session = self._session(BaseClient, discovery_cache_file=cache)
            with mock.patch.object(
                weber_mqtt, "write_json_atomic", side_effect=OSError("full")
            ):
                run_publish(session, connected=True)
            # No crash; publications still happened.

    def test_reason_code_non_int_falls_back_to_equality(self) -> None:
        class ReasonCode:
            def __int__(self):
                raise TypeError("not int")

            def __eq__(self, other):
                return other == 0

        class Client(BaseClient):
            connect_reason = ReasonCode()

        session = self._session(Client)
        run_publish(session, connected=True)  # connects fine via == 0 fallback

    def test_broker_rejection_times_out(self) -> None:
        class Client(BaseClient):
            connect_reason = 5  # non-zero → connection never acknowledged

        session = self._session(Client)
        state = build_state(summary(), {}, "AA:BB:CC:DD:EE:FF", False, 1)

        async def scenario() -> None:
            with mock.patch.object(weber_mqtt, "CONNECT_TIMEOUT", 0.1):
                with self.assertRaises(TimeoutError):
                    await session.publish(state, 30)
            await session.close()

        with self.assertLogs("weber_connect_mqtt", level="ERROR"):
            asyncio.run(scenario())

    def test_connect_nonzero_result_raises(self) -> None:
        class Client(BaseClient):
            connect_result = 1

        session = self._session(Client)
        state = build_state(summary(), {}, "AA:BB:CC:DD:EE:FF", False, 1)

        async def scenario() -> None:
            with self.assertRaises(RuntimeError):
                await session.publish(state, 30)
            await session.close()

        asyncio.run(scenario())

    def test_on_disconnect_logs_when_unexpected(self) -> None:
        captured: list = []

        class Client(BaseClient):
            def loop_start(self) -> None:
                captured.append(self)
                self.on_connect(self, None, None, 0, None)

        session = self._session(Client)
        state = build_state(summary(), {}, "AA:BB:CC:DD:EE:FF", True, 1)

        async def scenario() -> None:
            await session.publish(state, 30)
            client = captured[0]
            with self.assertLogs("weber_connect_mqtt", level="WARNING"):
                client.on_disconnect(client, None, None, 7, None)
            await session.close()

        asyncio.run(scenario())

    def test_discard_client_swallows_loop_and_disconnect_errors(self) -> None:
        class Client(BaseClient):
            def loop_stop(self) -> None:
                raise RuntimeError("loop stuck")

            def disconnect(self) -> None:
                raise RuntimeError("cannot disconnect")

        session = self._session(Client)
        run_publish(session, connected=True)  # close triggers _discard_client

    def test_publish_after_close_raises(self) -> None:
        session = self._session(BaseClient)
        state = build_state(summary(), {}, "AA:BB:CC:DD:EE:FF", False, 1)

        async def scenario() -> None:
            await session.close()
            with self.assertRaises(RuntimeError):
                await session.publish(state, 30)

        asyncio.run(scenario())

    def test_publish_nonzero_rc_raises(self) -> None:
        class Client(BaseClient):
            def publish(self, topic, payload, qos, retain):
                self.publications.append((topic, payload, qos, retain))
                return FailingPublish()

        session = self._session(Client)
        state = build_state(summary(), {}, "AA:BB:CC:DD:EE:FF", True, 1)

        async def scenario() -> None:
            with self.assertRaises(RuntimeError):
                await session.publish(state, 30)
            await session.close()

        asyncio.run(scenario())

    def test_make_client_uses_real_paho_backend(self) -> None:
        session = MqttSession(
            MqttConfig(host="mqtt.local", username="u", password="p"),
            summary(),
            max_probes=1,
        )
        client = session._make_client()  # no factory → real paho client
        self.assertIsNotNone(client.on_connect)
        self.assertIsNotNone(client.on_disconnect)

    def test_close_swallows_offline_publish_error(self) -> None:
        clients: list = []

        class Client(BaseClient):
            def __init__(self, client_id: str) -> None:
                super().__init__(client_id)
                clients.append(self)

        session = self._session(Client)
        state = build_state(summary(), {}, "AA:BB:CC:DD:EE:FF", True, 1)

        async def scenario() -> None:
            await session.publish(state, 30)

            def raising_publish(*args, **kwargs):
                raise RuntimeError("broker gone")

            clients[0].publish = raising_publish
            with self.assertLogs("weber_connect_mqtt", level="DEBUG"):
                await session.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
