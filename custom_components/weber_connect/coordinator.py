"""Transport coordinator and single-session lifecycle for Weber Connect."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Protocol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .bluetooth import WeberBluetoothSession
from .const import (
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_MESSAGE_VERSION,
    DOMAIN,
)
from .options import ConnectionMode, WeberOptions
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
    """Own one transport and publish its decoded probe status."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.address = str(entry.data["address"])
        self.companion_id = str(entry.data[CONF_COMPANION_ID])
        self.message_version = int(entry.data.get(CONF_MESSAGE_VERSION, 11))
        self.options = WeberOptions.from_mapping(entry.options)
        self.source = (
            "cloud"
            if self.options.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
            else "bluetooth"
        )
        self.cloud_client: WeberCloudClient | None = None
        self.cloud_session: WeberCloudSession | None = None
        self.bluetooth_session: WeberBluetoothSession | None = None
        self._transport: _TransportSession
        self._transport_task: asyncio.Task[None] | None = None
        self._cancel_bluetooth_callback: Callable[[], None] | None = None
        self.last_error: str | None = None
        self.last_successful_update: str | None = None
        self.consecutive_failures = 0
        self.successful_updates = 0
        self.failed_updates = 0

        if self.source == "cloud":
            appliance_id = str(entry.data[CONF_APPLIANCE_ID])
            config = CloudConfig.from_mapping(
                {
                    "device_id": self.companion_id,
                    "device_password": entry.data[CONF_CLOUD_PASSWORD],
                    "appliance_id": appliance_id,
                }
            )
            self.cloud_client = WeberCloudClient(config)
            self.cloud_session = WeberCloudSession(hass, self.cloud_client, appliance_id)
            self._transport = self.cloud_session
        else:
            self.bluetooth_session = WeberBluetoothSession(
                hass,
                self.address,
                self.companion_id,
                self.message_version,
            )
            self._transport = self.bluetooth_session

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            always_update=False,
        )

    def initial_state(self) -> dict[str, Any]:
        """Return the complete idle entity shape before the transport starts."""

        return normalize_state(None, source=self.source, connected=False)

    def async_start(self) -> None:
        """Start the one entry-owned transport task."""

        if self._transport_task is not None:
            return
        # Versions before 3.0.1 raised a repair after routine cloud outages.
        # A powered-off hub is normal, so clear that retired issue at startup.
        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            f"connection_lost_{self.entry.entry_id}",
        )
        if self.bluetooth_session is not None:
            self._cancel_bluetooth_callback = bluetooth.async_register_callback(
                self.hass,
                self._async_bluetooth_advertisement,
                {"address": self.address, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        self._transport_task = self.entry.async_create_background_task(
            self.hass,
            self._transport.async_run(self._async_status, self._async_error),
            name=f"{DOMAIN} {self.source} session",
        )

    @callback
    def _async_bluetooth_advertisement(
        self,
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Retry promptly when an idle local hub wakes and advertises."""

        self._transport.async_wake()

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
        self.async_set_updated_data(normalize_state(status, source=self.source, connected=True))

    @callback
    def _async_error(self, message: str) -> None:
        """Record a bounded transport failure without hiding probe entities."""

        self.last_error = message
        self.failed_updates += 1
        self.consecutive_failures += 1
        if self.consecutive_failures >= OFFLINE_FAILURE_THRESHOLD:
            self.async_set_updated_data(normalize_state(None, source=self.source, connected=False))
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

        if self._cancel_bluetooth_callback is not None:
            self._cancel_bluetooth_callback()
            self._cancel_bluetooth_callback = None
        if self._transport_task is not None:
            task = self._transport_task
            self._transport_task = None
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._transport.async_close()
        await self.async_shutdown()
        if self.cloud_client is not None:
            await self.hass.async_add_executor_job(self.cloud_client.close)
