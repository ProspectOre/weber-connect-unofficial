"""Constants for the unofficial Weber Connect integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "weber_connect"
NAME: Final = "Weber Connect Unofficial"
MANUFACTURER: Final = "Weber"

CONF_COMPANION_ID: Final = "companion_id"
CONF_MESSAGE_VERSION: Final = "message_version"
CONF_CLOUD_PASSWORD: Final = "cloud_password"
CONF_APPLIANCE_ID: Final = "appliance_id"

CONF_CONNECTION: Final = "connection"
CONF_CONNECTION_MODE: Final = "connection_mode"
CONF_PROBES: Final = "probes"
CONF_PROBE_NAME_PREFIX: Final = "probe_name_"

WEBER_COMPANY_IDS: Final = frozenset({0x0DF2, 0x07C5})
PLATFORMS: Final = ("sensor",)
