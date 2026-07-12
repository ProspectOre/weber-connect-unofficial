"""Persistent, bounded MQTT delivery for the Weber Connect panel runtime."""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from weber_status_bridge import (
    build_mqtt_publish_plan,
    device_id_from,
    render_topic_prefix,
    slugify,
)

LOGGER = logging.getLogger("weber_connect_mqtt")
CONNECT_TIMEOUT = 10.0
PUBLISH_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class MqttConfig:
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None
    topic_prefix: str = "weber_connect"
    discovery_prefix: str = "homeassistant"
    retain: bool = True

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> MqttConfig:
        host = str(payload.get("host") or "").strip()
        if not host:
            raise ValueError("MQTT host is required")
        return cls(
            host=host,
            port=int(payload.get("port") or 1883),
            username=payload.get("username") or None,
            password=payload.get("password") or None,
        )


class MqttSession:
    """Keep one MQTT connection alive and bound every blocking operation."""

    def __init__(
        self,
        config: MqttConfig,
        summary: dict[str, Any],
        *,
        max_probes: int,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.summary = summary
        self.max_probes = max_probes
        self._client_factory = client_factory
        self._client: Any = None
        self._connected = threading.Event()
        self._thread_lock = threading.Lock()
        self._async_lock = asyncio.Lock()
        self._closed = False
        self._discovery_cache: dict[str, str] = {}

        hub = summary.get("hub") or {}
        address = hub.get("ble_address") or ""
        self.device_id = device_id_from(summary, address)
        self.client_id = slugify(hub.get("display_name") or self.device_id) + "_bridge"
        serial = slugify(hub.get("serial_number") or self.device_id)
        topic_root = render_topic_prefix(
            config.topic_prefix,
            device_id=self.device_id,
            object_slug=slugify(hub.get("display_name") or self.device_id),
            serial=serial,
        )
        self.availability_topic = f"{topic_root}/availability"

    def _make_client(self) -> Any:
        if self._client_factory is not None:
            client = self._client_factory(client_id=self.client_id)
        else:
            try:
                import paho.mqtt.client as mqtt
            except ImportError as exc:
                raise RuntimeError("paho-mqtt is not installed") from exc
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
            )
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password)
        client.will_set(self.availability_topic, "offline", qos=1, retain=True)

        def on_connect(
            _client: Any,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any = None,
        ) -> None:
            try:
                success = int(reason_code) == 0
            except (TypeError, ValueError):
                success = reason_code == 0
            if success:
                self._connected.set()
            else:
                LOGGER.error("MQTT broker rejected connection: %s", reason_code)

        def on_disconnect(
            _client: Any,
            _userdata: Any,
            _disconnect_flags: Any,
            reason_code: Any,
            _properties: Any = None,
        ) -> None:
            self._connected.clear()
            if not self._closed and reason_code:
                LOGGER.warning("MQTT connection lost: %s", reason_code)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        return client

    def _discard_client(self) -> None:
        client, self._client = self._client, None
        self._connected.clear()
        self._discovery_cache.clear()
        if client is None:
            return
        try:
            client.loop_stop()
        except Exception:
            LOGGER.debug("Could not stop MQTT network loop", exc_info=True)
        try:
            client.disconnect()
        except Exception:
            LOGGER.debug("Could not disconnect MQTT client", exc_info=True)

    def _ensure_connected(self) -> Any:
        if self._closed:
            raise RuntimeError("MQTT session is closed")
        if self._client is not None and self._connected.is_set():
            return self._client
        self._discard_client()
        client = self._make_client()
        self._client = client
        result = client.connect(self.config.host, self.config.port, keepalive=30)
        if result != 0:
            self._discard_client()
            raise RuntimeError(f"MQTT connect failed with code {result}")
        client.loop_start()
        if not self._connected.wait(CONNECT_TIMEOUT):
            self._discard_client()
            raise TimeoutError("MQTT broker did not acknowledge the connection")
        return client

    @staticmethod
    def _wait_for_publish(result: Any, timeout: float = PUBLISH_TIMEOUT) -> None:
        result.wait_for_publish(timeout=timeout)
        if hasattr(result, "is_published") and not result.is_published():
            raise TimeoutError("MQTT publish acknowledgement timed out")
        if getattr(result, "rc", 0) != 0:
            raise RuntimeError(f"MQTT publish failed with code {result.rc}")

    def _publish_sync(self, state: dict[str, Any], poll_seconds: int) -> None:
        with self._thread_lock:
            client = self._ensure_connected()
            deadline = time.monotonic() + PUBLISH_TIMEOUT
            args = argparse.Namespace(
                topic_prefix=self.config.topic_prefix,
                discovery_prefix=self.config.discovery_prefix,
                discovery=True,
                availability=True,
                retain=self.config.retain,
                poll_seconds=poll_seconds,
                max_probes=self.max_probes,
            )
            try:
                availability = client.publish(
                    self.availability_topic,
                    "online",
                    qos=1,
                    retain=True,
                )
                self._wait_for_publish(availability, max(0.1, deadline - time.monotonic()))
                for publish in build_mqtt_publish_plan(args, state, self.summary):
                    is_discovery = publish["topic"].endswith("/config")
                    if is_discovery and self._discovery_cache.get(publish["topic"]) == publish["payload"]:
                        continue
                    result = client.publish(
                        publish["topic"],
                        publish["payload"],
                        qos=publish["qos"],
                        retain=publish["retain"],
                    )
                    self._wait_for_publish(result, max(0.1, deadline - time.monotonic()))
                    if is_discovery:
                        self._discovery_cache[publish["topic"]] = publish["payload"]
            except Exception:
                self._discard_client()
                raise

    async def publish(self, state: dict[str, Any], poll_seconds: int) -> None:
        async with self._async_lock:
            await asyncio.to_thread(self._publish_sync, state, poll_seconds)

    def _close_sync(self) -> None:
        with self._thread_lock:
            self._closed = True
            client = self._client
            if client is not None and self._connected.is_set():
                try:
                    result = client.publish(
                        self.availability_topic,
                        "offline",
                        qos=1,
                        retain=True,
                    )
                    self._wait_for_publish(result)
                except Exception:
                    LOGGER.debug("Could not publish MQTT offline status", exc_info=True)
            self._discard_client()

    async def close(self) -> None:
        async with self._async_lock:
            await asyncio.to_thread(self._close_sync)
