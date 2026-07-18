#!/usr/bin/env python3
"""Read Weber Connect Hub probe status locally over BLE.

This uses the paired Android app's companion id, writes a read-only handshake
to the hub's session characteristic, decodes the plaintext INCOMING_STATUS TLV,
and optionally publishes Home Assistant MQTT discovery/state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import secrets
import shutil
import signal
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from saber_frames import (
    NOTIFICATION_UUID,
    RESPONSE_UUID,
    SESSION_UUID,
    STATUS_UUID,
    build_command_frame,
    build_handshake_body,
    bytes_to_hex,
    decode_hex_frame,
)
from weber_persistence import write_json_atomic as write_private_json_atomic

DEFAULT_PAIRING_SUMMARY = Path("weber_probe/weber_android_pairing_summary.json")
DEFAULT_JSON_OUT = Path("weber_probe/weber_status_latest.json")
DEFAULT_TOPIC_ROOT = "weber_connect"
STATE_TOPIC_SUFFIX = "state"
VERSION = "2.1.0"
HEX_16_BYTES_RE = re.compile(r"^[0-9a-fA-F]{32}$")
LOGGER = logging.getLogger("weber_connect_bridge")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in text).strip("_")


def normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def validate_companion_id(companion_id: str) -> str:
    companion_id = companion_id.replace(":", "").replace("-", "").strip()
    if not HEX_16_BYTES_RE.fullmatch(companion_id):
        raise ValueError("companion id must be 16 bytes / 32 hex characters")
    return companion_id.lower()


def device_id_from(summary: dict[str, Any], address: str) -> str:
    hub = summary.get("hub", {})
    serial = normalize_optional(hub.get("serial_number"))
    if serial:
        return slugify(f"weber_connect_{serial}")
    if address:
        return slugify(f"weber_connect_{address}")
    return slugify(f"weber_connect_{summary['companion_id'][-8:]}")


def load_pairing_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    companions = summary.get("companion_records") or []
    if not companions:
        raise ValueError(f"No companion_records found in {path}")
    companion_id = companions[0].get("companion_id")
    if not companion_id:
        raise ValueError(f"No companion_id found in {path}")
    summary["companion_id"] = companion_id
    return summary


def build_summary_from_args(args: argparse.Namespace) -> dict[str, Any]:
    companion_id = validate_companion_id(args.companion_id)
    summary = build_unpaired_summary_from_args(args)
    summary["companion_id"] = companion_id
    summary["companion_records"] = [{"companion_id": companion_id}]
    return summary


def build_unpaired_summary_from_args(args: argparse.Namespace) -> dict[str, Any]:
    hub = {
        "display_name": normalize_optional(args.hub_name) or "Weber Connect Hub",
        "serial_number": normalize_optional(args.hub_serial),
        "model": normalize_optional(args.hub_model) or "Connect Hub",
        "software_revision": normalize_optional(args.hub_software_revision),
        "wifi_mac": normalize_optional(args.hub_wifi_mac),
        "ble_address": normalize_optional(args.address),
    }
    return {
        "companion_id": "0" * 32,
        "companion_records": [],
        "hub": hub,
    }


def load_bridge_summary(args: argparse.Namespace, *, allow_unpaired: bool = False) -> dict[str, Any]:
    if args.pairing_summary and args.pairing_summary.exists():
        return load_pairing_summary(args.pairing_summary)
    if args.companion_id:
        return build_summary_from_args(args)
    if allow_unpaired:
        return build_unpaired_summary_from_args(args)
    raise ValueError("Provide --companion-id or a readable --pairing-summary file")


def load_mqtt_credentials(args: argparse.Namespace) -> None:
    if not args.mqtt_credentials_file:
        if args.mqtt_password and not args.mqtt_username:
            raise ValueError("MQTT password was provided without MQTT username")
        return

    credentials = json.loads(args.mqtt_credentials_file.read_text(encoding="utf-8"))
    if not args.mqtt_username:
        args.mqtt_username = credentials.get("username")
    if not args.mqtt_password:
        args.mqtt_password = credentials.get("password")

    if args.mqtt_username and not args.mqtt_password:
        raise ValueError(f"MQTT username is configured but password is missing in {args.mqtt_credentials_file}")
    if args.mqtt_password and not args.mqtt_username:
        raise ValueError(f"MQTT password is configured but username is missing in {args.mqtt_credentials_file}")


def default_address(summary: dict[str, Any]) -> str:
    hub_address = normalize_optional(summary.get("hub", {}).get("ble_address"))
    if hub_address:
        return hub_address
    raise ValueError("BLE address is required")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    write_private_json_atomic(path, payload)


def parse_status_event(event: dict[str, Any]) -> dict[str, Any] | None:
    decoded = event.get("decoded") or {}
    envelope = decoded.get("envelope") or {}
    candidate = envelope.get("body_plain_candidate") or {}
    parsed = candidate.get("parsed_payload")
    if parsed and parsed.get("kind") == "cook_session_status":
        row = dict(parsed)
        row["transport_sequence"] = decoded.get("sequence")
        row["message_version"] = candidate.get("message_version")
        row["source"] = event.get("source")
        row["received_at"] = event.get("received_at")
        return row
    return None


def make_event(sender: Any, data: bytes | bytearray, source: str) -> dict[str, Any]:
    raw = bytes(data)
    hex_value = bytes_to_hex(raw)
    return {
        "received_at": utc_now(),
        "source": source,
        "sender": str(sender),
        "length": len(raw),
        "hex": hex_value,
        "decoded": decode_hex_frame(hex_value),
    }


def build_state(
    summary: dict[str, Any],
    latest_status: dict[str, Any],
    address: str,
    connected: bool,
    max_probes: int,
    source: str = "ble",
    probe_names: dict[int, str] | None = None,
    remote_controls_enabled: bool = False,
) -> dict[str, Any]:
    hub = summary.get("hub", {})
    aliases = probe_names or {}
    probes = []
    for raw_probe in latest_status.get("probes", []):
        probe = dict(raw_probe)
        number = probe.get("probe_number")
        if isinstance(number, int) and aliases.get(number):
            probe["nickname"] = aliases[number]
        probes.append(probe)
    state: dict[str, Any] = {
        "updated_at": utc_now(),
        "connected": connected,
        "source": source,
        "ble_address": address,
        "hub": {
            "display_name": hub.get("display_name"),
            "serial_number": hub.get("serial_number"),
            "model": hub.get("model"),
            "software_revision": hub.get("software_revision"),
            "wifi_mac": hub.get("wifi_mac"),
            "ble_address": hub.get("ble_address"),
        },
        "status": latest_status,
        "probes": probes,
        "probe_count": latest_status.get("probe_count", 0),
        "cook_controls_enabled": remote_controls_enabled,
    }

    active_cook = latest_status.get("active_cook")
    if not isinstance(active_cook, dict):
        active_cook = {}
    current_step = active_cook.get("current_step")
    if not isinstance(current_step, dict):
        current_step = {}
    current_prompt = active_cook.get("current_prompt")
    if not isinstance(current_prompt, dict):
        current_prompt = {}
    program_prompts = active_cook.get("prompts")
    if not isinstance(program_prompts, list):
        program_prompts = []
    instructions = [
        {
            "step_id": prompt.get("step_id"),
            "prompt_id": prompt.get("id"),
            "title": prompt.get("short_title"),
            "instruction": prompt.get("instruction"),
        }
        for prompt in program_prompts
        if isinstance(prompt, dict)
        and (prompt.get("short_title") or prompt.get("instruction"))
    ]
    target_c = current_step.get("target_temperature_c")
    target_f = (
        round(float(target_c) * 9.0 / 5.0 + 32.0, 1)
        if isinstance(target_c, (int, float)) and not isinstance(target_c, bool)
        else None
    )
    state.update(
        {
            "active_recipe": active_cook.get("title"),
            "active_recipe_state": active_cook.get("state"),
            "active_step": active_cook.get("step_id"),
            "active_prompt": active_cook.get("prompt_id"),
            "current_instruction": active_cook.get("current_instruction"),
            "current_instruction_short": current_prompt.get("short_title"),
            "cook_time_remaining_s": active_cook.get("time_remaining_s"),
            "cook_time_elapsed_s": active_cook.get("time_elapsed_s"),
            "cook_target_temperature_c": target_c,
            "cook_target_temperature_f": target_f,
            "cook_mode": current_step.get("cook_mode") or latest_status.get("cook_mode"),
            "cook_control_available": bool(
                connected and remote_controls_enabled and active_cook.get("active")
            ),
            "active_cook": (
                {
                    "title": active_cook.get("title"),
                    "program_id": active_cook.get("program_id"),
                    "plan_id": active_cook.get("plan_id"),
                    "probe_number": active_cook.get("probe_number"),
                    "state": active_cook.get("state"),
                    "step_id": active_cook.get("step_id"),
                    "prompt_id": active_cook.get("prompt_id"),
                    "instruction": active_cook.get("current_instruction"),
                    "short_instruction": current_prompt.get("short_title"),
                    "target_temperature_c": target_c,
                    "target_temperature_f": target_f,
                    "time_remaining_s": active_cook.get("time_remaining_s"),
                    "time_elapsed_s": active_cook.get("time_elapsed_s"),
                    "cook_mode": current_step.get("cook_mode"),
                    "instructions": instructions,
                }
                if active_cook
                else {}
            ),
        }
    )

    cavities = latest_status.get("cavities")
    if not isinstance(cavities, list):
        cavities = []
    for number in range(1, 3):
        row = next(
            (
                item
                for item in cavities
                if isinstance(item, dict) and item.get("cavity_number") == number
            ),
            {},
        )
        state[f"cavity_{number}_temperature_f"] = row.get("temperature_f")
        state[f"cavity_{number}_temperature_c"] = row.get("temperature_c")
    if latest_status.get("actual_cavity_temp_f") is not None:
        state["cavity_1_temperature_f"] = latest_status.get("actual_cavity_temp_f")
        state["cavity_1_temperature_c"] = latest_status.get("actual_cavity_temp_c")

    timers = latest_status.get("timers")
    if not isinstance(timers, list):
        timers = []
    for number in range(1, 5):
        row = next(
            (
                item
                for item in timers
                if isinstance(item, dict) and item.get("timer_number") == number
            ),
            {},
        )
        state[f"timer_{number}_remaining_s"] = row.get("remaining_s")

    rendered_cook = state.get("active_cook")
    if isinstance(rendered_cook, dict) and rendered_cook:
        rendered_cook["cavities"] = [
            {
                "number": number,
                "temperature_f": state.get(f"cavity_{number}_temperature_f"),
                "temperature_c": state.get(f"cavity_{number}_temperature_c"),
            }
            for number in range(1, 3)
            if state.get(f"cavity_{number}_temperature_f") is not None
            or state.get(f"cavity_{number}_temperature_c") is not None
        ]
        rendered_cook["timers"] = [
            {
                "number": number,
                "remaining_s": state.get(f"timer_{number}_remaining_s"),
            }
            for number in range(1, 5)
            if state.get(f"timer_{number}_remaining_s") is not None
        ]

    for number in range(1, max_probes + 1):
        prefix = f"probe_{number}"
        state[f"{prefix}_temperature_f"] = None
        state[f"{prefix}_temperature_c"] = None
        state[f"{prefix}_state"] = "No probe"
        state[f"{prefix}_battery"] = None
        state[f"{prefix}_type"] = None
        state[f"{prefix}_nickname"] = aliases.get(number)

    for probe in state["probes"]:
        number = probe.get("probe_number")
        if not number:
            continue
        prefix = f"probe_{number}"
        state[f"{prefix}_temperature_f"] = probe.get("probe_temp_f")
        state[f"{prefix}_temperature_c"] = probe.get("probe_temp_c")
        state[f"{prefix}_state"] = probe.get("state")
        state[f"{prefix}_battery"] = probe.get("battery_level")
        state[f"{prefix}_type"] = probe.get("probe_type")
    return state


def render_topic_prefix(template: str, *, device_id: str, object_slug: str, serial: str) -> str:
    values = {
        "device_id": device_id,
        "object_slug": object_slug,
        "serial": serial,
    }
    rendered = (template or DEFAULT_TOPIC_ROOT).strip("/")

    if any(f"{{{key}}}" in rendered for key in values):
        for key, value in values.items():
            rendered = rendered.replace(f"{{{key}}}", value)
        return rendered.replace("{", "").replace("}", "").strip("/")

    if "{" in rendered or "}" in rendered:
        rendered = rendered.replace("{", "").replace("}", "").strip("/")

    if not rendered:
        rendered = DEFAULT_TOPIC_ROOT

    if rendered.split("/")[-1] in values.values():
        return rendered
    return f"{rendered}/{device_id}".strip("/")


def build_mqtt_publish_plan(
    args: argparse.Namespace,
    state: dict[str, Any],
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    hub = summary.get("hub", {})
    device_id = device_id_from(summary, state.get("ble_address") or "")
    serial = hub.get("serial_number") or device_id
    device_name = hub.get("display_name") or "Weber Connect Hub"
    object_slug = slugify(device_name) or device_id
    topic_prefix = render_topic_prefix(
        args.topic_prefix,
        device_id=device_id,
        object_slug=object_slug,
        serial=slugify(serial),
    )
    state_topic = f"{topic_prefix}/{STATE_TOPIC_SUFFIX}"
    with_availability = getattr(args, "availability", False)
    expire_after = max(60, int(args.poll_seconds * 4))
    publish_plan: list[dict[str, Any]] = []

    if args.discovery:
        device = {
            "identifiers": [serial],
            "name": device_name,
            "manufacturer": "Weber",
            "model": hub.get("model") or "Connect Hub",
        }
        if hub.get("software_revision"):
            device["sw_version"] = hub["software_revision"]
        origin = {
            "name": "Weber Connect for Home Assistant (Unofficial)",
            "sw": VERSION,
            "url": "https://github.com/ProspectOre/weber-connect-home-assistant-addon",
        }
        for number in range(1, args.max_probes + 1):
            base_id = f"{device_id}_probe_{number}"
            nickname = str(state.get(f"probe_{number}_nickname") or "").strip()
            probe_name = f"{nickname} · Probe {number}" if nickname else f"Probe {number}"
            # Each probe carries its own availability topic so a single
            # wireless probe can drop out without taking the others offline.
            probe_availability = (
                {"availability_topic": f"{topic_prefix}/probe_{number}/availability"}
                if with_availability
                else {}
            )
            configs = [
                (
                    "sensor",
                    f"{base_id}_temperature",
                    {
                        "name": f"{probe_name} Temperature",
                        "unique_id": f"{serial}_probe_{number}_temperature",
                        "state_topic": state_topic,
                        **probe_availability,
                        "value_template": f"{{{{ value_json.probe_{number}_temperature_f }}}}",
                        "unit_of_measurement": "\u00b0F",
                        "device_class": "temperature",
                        "state_class": "measurement",
                        "device": device,
                        "origin": origin,
                        "expire_after": expire_after,
                    },
                ),
                (
                    "sensor",
                    f"{base_id}_state",
                    {
                        "name": f"{probe_name} State",
                        "unique_id": f"{serial}_probe_{number}_state",
                        "state_topic": state_topic,
                        **probe_availability,
                        "value_template": f"{{{{ value_json.probe_{number}_state }}}}",
                        "device": device,
                        "origin": origin,
                        "expire_after": expire_after,
                    },
                ),
            ]
            # Wired probes never report a battery level, so only advertise a
            # battery sensor once the hub has reported one (wireless probe).
            # We never emit an entity-deleting empty payload on disconnect;
            # availability and expire_after carry staleness instead, so probe
            # entities survive an offline/online cycle without being recreated.
            if state.get(f"probe_{number}_battery") is not None:
                configs.append(
                    (
                        "sensor",
                        f"{base_id}_battery",
                        {
                            "name": f"{probe_name} Battery",
                            "unique_id": f"{serial}_probe_{number}_battery",
                            "state_topic": state_topic,
                            **probe_availability,
                            "value_template": f"{{{{ value_json.probe_{number}_battery }}}}",
                            "unit_of_measurement": "%",
                            "device_class": "battery",
                            "state_class": "measurement",
                            "device": device,
                            "origin": origin,
                            "expire_after": expire_after,
                        },
                    )
                )
            for domain, object_id, config in configs:
                config_topic = f"{args.discovery_prefix}/{domain}/{object_id}/config"
                publish_plan.append(
                    {
                        "topic": config_topic,
                        "payload": json.dumps(config),
                        "qos": 0,
                        "retain": True,
                    }
                )

        hub_availability = (
            {"availability_topic": f"{topic_prefix}/availability"}
            if with_availability
            else {}
        )
        cook_configs: list[tuple[str, str, dict[str, Any]]] = [
            (
                "sensor",
                f"{device_id}_active_recipe",
                {
                    "name": "Active Recipe",
                    "unique_id": f"{serial}_active_recipe",
                    "state_topic": state_topic,
                    **hub_availability,
                    "value_template": "{{ value_json.active_recipe | default('No active cook', true) }}",
                    "json_attributes_topic": state_topic,
                    "json_attributes_template": "{{ value_json.active_cook | tojson }}",
                    "icon": "mdi:chef-hat",
                    "device": device,
                    "origin": origin,
                    "expire_after": expire_after,
                },
            ),
            (
                "sensor",
                f"{device_id}_current_instruction",
                {
                    "name": "Current Instruction",
                    "unique_id": f"{serial}_current_instruction",
                    "state_topic": state_topic,
                    **hub_availability,
                    "value_template": "{{ value_json.current_instruction | default('No active instruction', true) }}",
                    "icon": "mdi:format-list-checks",
                    "device": device,
                    "origin": origin,
                    "expire_after": expire_after,
                },
            ),
            (
                "sensor",
                f"{device_id}_cook_target_temperature",
                {
                    "name": "Cook Target Temperature",
                    "unique_id": f"{serial}_cook_target_temperature",
                    "state_topic": state_topic,
                    **hub_availability,
                    "value_template": "{{ value_json.cook_target_temperature_f }}",
                    "unit_of_measurement": "°F",
                    "device_class": "temperature",
                    "state_class": "measurement",
                    "device": device,
                    "origin": origin,
                    "expire_after": expire_after,
                },
            ),
            (
                "sensor",
                f"{device_id}_cook_time_remaining",
                {
                    "name": "Cook Time Remaining",
                    "unique_id": f"{serial}_cook_time_remaining",
                    "state_topic": state_topic,
                    **hub_availability,
                    "value_template": "{{ value_json.cook_time_remaining_s }}",
                    "unit_of_measurement": "s",
                    "device_class": "duration",
                    "device": device,
                    "origin": origin,
                    "expire_after": expire_after,
                },
            ),
        ]
        for number in range(1, 3):
            cook_configs.append(
                (
                    "sensor",
                    f"{device_id}_cavity_{number}_temperature",
                    {
                        "name": f"Cavity {number} Temperature",
                        "unique_id": f"{serial}_cavity_{number}_temperature",
                        "state_topic": state_topic,
                        **hub_availability,
                        "value_template": f"{{{{ value_json.cavity_{number}_temperature_f }}}}",
                        "unit_of_measurement": "°F",
                        "device_class": "temperature",
                        "state_class": "measurement",
                        "device": device,
                        "origin": origin,
                        "expire_after": expire_after,
                    },
                )
            )
        for number in range(1, 5):
            cook_configs.append(
                (
                    "sensor",
                    f"{device_id}_timer_{number}",
                    {
                        "name": f"Timer {number}",
                        "unique_id": f"{serial}_timer_{number}",
                        "state_topic": state_topic,
                        **hub_availability,
                        "value_template": f"{{{{ value_json.timer_{number}_remaining_s }}}}",
                        "unit_of_measurement": "s",
                        "device_class": "duration",
                        "icon": "mdi:timer-outline",
                        "device": device,
                        "origin": origin,
                        "expire_after": expire_after,
                    },
                )
            )
        if state.get("cook_controls_enabled"):
            control_availability = {
                "availability_topic": state_topic,
                "availability_template": (
                    "{{ 'online' if value_json.cook_control_available else 'offline' }}"
                ),
            }
            for action, label, icon_name in (
                ("confirm", "Confirm Current Step", "mdi:check-bold"),
                ("stop", "Stop Active Cook", "mdi:stop-circle-outline"),
            ):
                cook_configs.append(
                    (
                        "button",
                        f"{device_id}_{action}_cook",
                        {
                            "name": label,
                            "unique_id": f"{serial}_{action}_cook",
                            "command_topic": f"{topic_prefix}/command/cook/{action}",
                            "payload_press": action,
                            **control_availability,
                            "icon": icon_name,
                            "device": device,
                            "origin": origin,
                        },
                    )
                )
            for number in range(1, 5):
                cook_configs.extend(
                    [
                        (
                            "number",
                            f"{device_id}_timer_{number}_start",
                            {
                                "name": f"Start Timer {number}",
                                "unique_id": f"{serial}_timer_{number}_start",
                                "command_topic": f"{topic_prefix}/command/timer/{number}/start",
                                "min": 1,
                                "max": 86400,
                                "step": 1,
                                "unit_of_measurement": "s",
                                "mode": "box",
                                **hub_availability,
                                "icon": "mdi:timer-plus-outline",
                                "device": device,
                                "origin": origin,
                            },
                        ),
                        (
                            "button",
                            f"{device_id}_timer_{number}_reset",
                            {
                                "name": f"Reset Timer {number}",
                                "unique_id": f"{serial}_timer_{number}_reset",
                                "command_topic": f"{topic_prefix}/command/timer/{number}/reset",
                                "payload_press": "reset",
                                **hub_availability,
                                "icon": "mdi:timer-remove-outline",
                                "device": device,
                                "origin": origin,
                            },
                        ),
                    ]
                )
        for domain, object_id, config in cook_configs:
            publish_plan.append(
                {
                    "topic": f"{args.discovery_prefix}/{domain}/{object_id}/config",
                    "payload": json.dumps(config),
                    "qos": 0,
                    "retain": True,
                }
            )

        # Bridge-health entities: connectivity and the last successful publish.
        # These describe the hub link itself, so they stay available (no
        # expire_after / probe availability) and report last known state.
        health_configs = [
            (
                "binary_sensor",
                f"{device_id}_connectivity",
                {
                    "name": "Hub Connectivity",
                    "unique_id": f"{serial}_connectivity",
                    "state_topic": state_topic,
                    "value_template": "{{ 'ON' if value_json.connected else 'OFF' }}",
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    "device_class": "connectivity",
                    "entity_category": "diagnostic",
                    "device": device,
                    "origin": origin,
                },
            ),
            (
                "sensor",
                f"{device_id}_last_publish",
                {
                    "name": "Last Publish",
                    "unique_id": f"{serial}_last_publish",
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.updated_at }}",
                    "device_class": "timestamp",
                    "entity_category": "diagnostic",
                    "device": device,
                    "origin": origin,
                },
            ),
        ]
        for domain, object_id, health_config in health_configs:
            publish_plan.append(
                {
                    "topic": f"{args.discovery_prefix}/{domain}/{object_id}/config",
                    "payload": json.dumps(health_config),
                    "qos": 0,
                    "retain": True,
                }
            )

    publish_plan.append(
        {
            "topic": state_topic,
            "payload": json.dumps(state),
            "qos": 0,
            "retain": args.retain,
        }
    )
    return publish_plan


def mqtt_publish(args: argparse.Namespace, state: dict[str, Any], summary: dict[str, Any]) -> None:
    try:
        import paho.mqtt.client as mqtt
        from paho.mqtt.enums import CallbackAPIVersion
    except ImportError as exc:
        raise RuntimeError("paho-mqtt is not installed; run pip install -r requirements.txt") from exc

    device_name = summary.get("hub", {}).get("display_name") or "Weber Connect Hub"
    object_slug = slugify(device_name) or device_id_from(summary, state.get("ble_address") or "")
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"{object_slug}_bridge")
    if args.mqtt_username:
        client.username_pw_set(args.mqtt_username, args.mqtt_password)
    connect_rc = client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    if connect_rc != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError(f"MQTT connect failed: {mqtt.error_string(connect_rc)}")
    client.loop_start()
    try:
        publish_results = []
        for publish in build_mqtt_publish_plan(args, state, summary):
            publish_results.append(
                client.publish(
                    publish["topic"],
                    publish["payload"],
                    qos=publish["qos"],
                    retain=publish["retain"],
                )
            )
        for result in publish_results:
            result.wait_for_publish(timeout=10.0)
            if not result.is_published():
                raise TimeoutError("MQTT publish acknowledgement timed out")
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT publish failed: {mqtt.error_string(result.rc)}")
    finally:
        client.loop_stop()
        client.disconnect()


async def read_status_once(
    address: str,
    companion_id: str,
    version: int,
    listen_seconds: float,
    timeout: float,
    write_without_response: bool,
) -> dict[str, Any]:
    try:
        from bleak import BleakClient
    except ImportError as exc:
        raise RuntimeError("bleak is not installed; run pip install -r requirements.txt") from exc

    events: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    status_received = asyncio.Event()

    def handler(source: str) -> Callable[[Any, bytearray], None]:
        def on_notify(sender: Any, data: bytearray) -> None:
            event = make_event(sender, data, source)
            events.append(event)
            parsed = parse_status_event(event)
            if parsed is not None:
                statuses.append(parsed)
                status_received.set()
                probe_summary = ", ".join(
                    f"{probe.get('label')}: {probe.get('probe_temp_f')} F {probe.get('state')}"
                    for probe in parsed.get("probes", [])
                )
                LOGGER.info("status %s: %s", parsed.get("transport_sequence"), probe_summary)

        return on_notify

    nonce = secrets.token_bytes(32)
    frame = build_command_frame(
        1,
        version,
        0x70,
        build_handshake_body(companion_id, nonce),
    )

    async with BleakClient(address, timeout=timeout) as client:
        subscribed: list[str] = []
        for source, uuid in (
            ("status", STATUS_UUID),
            ("notification", NOTIFICATION_UUID),
            ("response", RESPONSE_UUID),
        ):
            try:
                await client.start_notify(uuid, handler(source))
                subscribed.append(uuid)
            except Exception as exc:
                LOGGER.warning("Could not subscribe %s: %r", source, exc)

        LOGGER.debug("Writing read-only session handshake to %s", SESSION_UUID)
        await client.write_gatt_char(SESSION_UUID, frame, response=not write_without_response)

        try:
            session_value = bytes(await client.read_gatt_char(SESSION_UUID))
            events.append(make_event(SESSION_UUID, session_value, "session-read"))
        except Exception as exc:
            LOGGER.warning("Could not read session characteristic: %r", exc)

        try:
            await asyncio.wait_for(status_received.wait(), timeout=listen_seconds)
        except asyncio.TimeoutError:
            pass

        for uuid in subscribed:
            try:
                await client.stop_notify(uuid)
            except Exception:
                pass

        return {
            "read_at": utc_now(),
            "address": address,
            "connected": client.is_connected,
            "message_version": version,
            "nonce_sha256_note": "nonce omitted; generated per run",
            "latest_status": statuses[-1] if statuses else None,
            "statuses": statuses,
            "events": events,
        }


def release_ble_connection(address: str) -> bool:
    """Ask BlueZ to drop any connection to the hub left behind by an unclean stop.

    The hub does not advertise while connected, so a stale connection blocks the
    Weber phone app from finding it.
    """
    tool = shutil.which("bluetoothctl")
    if not tool:
        LOGGER.debug("bluetoothctl is not available; skipping BLE release")
        return False
    try:
        result = subprocess.run(
            [tool, "disconnect", address],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.warning("Could not release BLE connection to %s: %r", address, exc)
        return False
    if result.returncode == 0:
        LOGGER.info("Released BLE connection to %s", address)
        return True
    LOGGER.debug(
        "No BLE connection to release for %s: %s",
        address,
        (result.stderr or result.stdout).strip(),
    )
    return False


async def run_bridge(args: argparse.Namespace) -> int:
    summary = load_bridge_summary(args, allow_unpaired=args.pause_ble)
    load_mqtt_credentials(args)
    address = args.address or default_address(summary)
    LOGGER.info("Using hub address %s", address)

    if args.pause_ble:
        release_ble_connection(address)
        state = build_state(summary, {}, address, connected=False, max_probes=args.max_probes)
        write_json_atomic(args.json_out, state)
        LOGGER.info("BLE pause is enabled; wrote disconnected state to %s", args.json_out)
        if args.mqtt_host:
            try:
                mqtt_publish(args, state, summary)
                LOGGER.info("Published MQTT disconnected state")
            except Exception as exc:
                LOGGER.error("MQTT publish failed: %r", exc)
                return 3
        if not args.continuous:
            return 0
        while True:
            await asyncio.sleep(3600)

    while True:
        try:
            result = await read_status_once(
                address=address,
                companion_id=summary["companion_id"],
                version=args.version,
                listen_seconds=args.listen_seconds,
                timeout=args.timeout,
                write_without_response=args.write_without_response,
            )
        except Exception as exc:
            LOGGER.error("Read failed: %r", exc)
            if not args.continuous:
                return 1
            await asyncio.sleep(args.poll_seconds)
            continue

        latest = result.get("latest_status")
        if latest:
            state = build_state(summary, latest, address, bool(result.get("connected")), args.max_probes)
            write_json_atomic(args.json_out, state)
            LOGGER.info("Wrote %s", args.json_out)
            if args.mqtt_host:
                try:
                    mqtt_publish(args, state, summary)
                    LOGGER.info("Published MQTT state")
                except Exception as exc:
                    LOGGER.error("MQTT publish failed: %r", exc)
                    if not args.continuous:
                        return 3
        else:
            result_path = args.json_out.with_suffix(".raw.json")
            write_json_atomic(result_path, result)
            LOGGER.warning("No decoded status received; wrote raw capture to %s", result_path)
            if not args.continuous:
                return 2

        if not args.continuous:
            return 0
        await asyncio.sleep(args.poll_seconds)


async def run_bridge_until_stopped(args: argparse.Namespace) -> int:
    """Run the bridge and disconnect from the hub cleanly on SIGTERM/SIGINT.

    Without this, an add-on stop kills the process mid-connection and BlueZ
    keeps the hub link open, so the hub never resumes advertising.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    stop_requested = False

    def request_stop(signum: int) -> None:
        nonlocal stop_requested
        stop_requested = True
        LOGGER.info(
            "Received %s; disconnecting from hub and shutting down",
            signal.Signals(signum).name,
        )
        task.cancel()

    registered: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop, sig)
        except (NotImplementedError, RuntimeError):
            continue
        registered.append(sig)

    try:
        return await run_bridge(args)
    except asyncio.CancelledError:
        if stop_requested:
            return 0
        raise
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)
        try:
            summary = load_bridge_summary(args, allow_unpaired=True)
            address = args.address or normalize_optional(summary.get("hub", {}).get("ble_address"))
            if address:
                await asyncio.to_thread(release_ble_connection, address)
        except Exception:
            LOGGER.warning("Could not release BLE connection during shutdown", exc_info=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Weber Connect local BLE status bridge.")
    parser.add_argument("--address", default=None, help="BLE address. Mac uses CoreBluetooth UUID.")
    parser.add_argument("--pairing-summary", type=Path, default=DEFAULT_PAIRING_SUMMARY)
    parser.add_argument("--companion-id", default=None, help="Trusted companion id, as 32 hex characters.")
    parser.add_argument("--hub-name", default="Weber Connect Hub")
    parser.add_argument("--hub-serial", default=None)
    parser.add_argument("--hub-model", default="Connect Hub")
    parser.add_argument("--hub-software-revision", default=None)
    parser.add_argument("--hub-wifi-mac", default=None)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--version", type=int, default=10)
    parser.add_argument("--listen-seconds", type=float, default=8.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--write-without-response", action="store_true")
    parser.add_argument("--pause-ble", action="store_true", help="Do not open BLE connections; publish disconnected state.")
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--mqtt-host", default=None)
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-username", default=None)
    parser.add_argument("--mqtt-password", default=None)
    parser.add_argument("--mqtt-credentials-file", type=Path, default=None)
    parser.add_argument("--topic-prefix", default="weber_connect/{device_id}")
    parser.add_argument("--discovery-prefix", default="homeassistant")
    parser.add_argument("--no-discovery", dest="discovery", action="store_false")
    parser.set_defaults(discovery=True)
    parser.add_argument("--retain", action="store_true")
    parser.add_argument("--max-probes", type=int, default=4)
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(run_bridge_until_stopped(args))


if __name__ == "__main__":
    raise SystemExit(main())
