"""Transport coordinator and single-session lifecycle for Weber Connect."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CLOUD_OFFLINE_RETAINED_KEYS,
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    DOMAIN,
)
from .options import WeberOptions
from .state import normalize_state
from .weber_cloud import CloudConfig, WeberCloudClient
from .weber_cloud_socket import WeberCloudSession

_LOGGER = logging.getLogger(__name__)
OFFLINE_FAILURE_THRESHOLD = 3


class _TransportSession(Protocol):
    async def async_run(
        self,
        status_callback: Callable[[dict[str, Any]], None],
        error_callback: Callable[[str], None],
    ) -> None: ...

    def async_wake(self) -> None: ...

    async def async_close(self) -> None: ...


class WeberCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Own one transport and publish its decoded appliance status."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.options = WeberOptions.from_mapping(entry.options)
        self.source = "cloud"
        self._transport_task: asyncio.Task[None] | None = None
        self.last_error: str | None = None
        self.last_successful_update: str | None = None
        self.consecutive_failures = 0
        self.successful_updates = 0
        self.failed_updates = 0

        appliance_id = str(entry.data[CONF_APPLIANCE_ID])
        config = CloudConfig.from_mapping(
            {
                "device_id": entry.data[CONF_COMPANION_ID],
                "device_password": entry.data[CONF_CLOUD_PASSWORD],
                "appliance_id": appliance_id,
            }
        )
        self.cloud_client = WeberCloudClient(config)
        self.cloud_session = WeberCloudSession(hass, self.cloud_client, appliance_id)
        self._transport: _TransportSession = self.cloud_session

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            always_update=False,
        )

    def initial_state(self) -> dict[str, Any]:
        """Return the complete idle entity shape before the transport starts."""

        return normalize_state(
            None,
            source=self.source,
            connected=False,
            last_successful_update=self.last_successful_update,
        )

    def async_start(self) -> None:
        """Start the one entry-owned transport task."""

        if self._transport_task is not None:
            return
        # Versions before 3.0.1 raised a repair after routine cloud outages.
        # A powered-off hub is normal. Clear every retired issue at startup,
        # including one orphaned when an earlier config entry was removed.
        registry = ir.async_get(self.hass)
        for domain, issue_id in tuple(registry.issues):
            if domain == DOMAIN and issue_id.startswith("connection_lost_"):
                ir.async_delete_issue(self.hass, domain, issue_id)
        self._transport_task = self.entry.async_create_background_task(
            self.hass,
            self._transport.async_run(self._async_status, self._async_error),
            name=f"{DOMAIN} {self.source} session",
        )

    @callback
    def _async_status(self, status: dict[str, Any]) -> None:
        """Publish a decoded transport status into Home Assistant."""

        self.successful_updates += 1
        self.last_error = None
        self.last_successful_update = datetime.now(timezone.utc).isoformat()
        self.consecutive_failures = 0
        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            f"credentials_rejected_{self.entry.entry_id}",
        )
        normalized = normalize_state(
            status,
            source=self.source,
            connected=True,
            last_successful_update=self.last_successful_update,
        )
        self.async_set_updated_data(normalized)

        identity = self.entry.unique_id or self.entry.entry_id
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, identity)})
        software_version = normalized.get("software_version")
        hardware_version = normalized.get("hardware_version")
        if device is not None and software_version is not None and hardware_version is not None:
            device_registry.async_update_device(
                device.id,
                sw_version=software_version,
                hw_version=hardware_version,
            )
        elif device is not None and software_version is not None:
            device_registry.async_update_device(device.id, sw_version=software_version)
        elif device is not None and hardware_version is not None:
            device_registry.async_update_device(device.id, hw_version=hardware_version)

    @callback
    def _async_error(self, message: str) -> None:
        """Record a bounded transport failure without hiding temperature entities."""

        self.last_error = message
        self.failed_updates += 1
        self.consecutive_failures += 1
        if self.consecutive_failures < OFFLINE_FAILURE_THRESHOLD:
            pending = dict(self.data or self.initial_state())
            pending["reading_status"] = "reconnecting"
            for number in range(1, 5):
                pending[f"probe_{number}_reading_status"] = "reconnecting"
            self.async_set_updated_data(pending)
        if self.consecutive_failures >= OFFLINE_FAILURE_THRESHOLD:
            offline_state = normalize_state(
                None,
                source=self.source,
                connected=False,
                last_successful_update=self.last_successful_update,
            )
            if self.source == "cloud":
                # Connection state and the timestamp communicate that this data
                # is stale. Retain the last hub snapshot through a cloud outage
                # so a transport failure cannot turn every slow-changing hub
                # entity unknown; live cooking and probe values remain cleared.
                previous_state = self.data or {}
                offline_state.update(
                    {key: previous_state.get(key) for key in CLOUD_OFFLINE_RETAINED_KEYS}
                )
            self.async_set_updated_data(offline_state)
        if self.source == "cloud" and self.cloud_session is not None:
            if self.cloud_session.error_kind == "credentials":
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"credentials_rejected_{self.entry.entry_id}",
                    data={"entry_id": self.entry.entry_id},
                    is_fixable=True,
                    is_persistent=True,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="credentials_rejected",
                    translation_placeholders={"name": self.entry.title},
                )
                return

    async def async_close(self) -> None:
        """Cancel all entry work and release the selected transport."""

        if self._transport_task is not None:
            task = self._transport_task
            self._transport_task = None
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._transport.async_close()
        await self.async_shutdown()
        await self.hass.async_add_executor_job(self.cloud_client.close)
