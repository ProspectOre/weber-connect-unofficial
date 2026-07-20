"""Home Assistant update coordinator for Weber Connect."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .bluetooth import WeberBluetoothError, async_read_status
from .const import (
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_MESSAGE_VERSION,
    DOMAIN,
)
from .options import WeberOptions
from .state import normalize_state
from .weber_cloud import (
    CloudConfig,
    WeberCloudClient,
    WeberCloudError,
    resolve_associated_appliance_id,
)

_LOGGER = logging.getLogger(__name__)
OFFLINE_FAILURE_THRESHOLD = 3
REPAIR_FAILURE_THRESHOLD = 6


class WeberCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate online and optional direct Bluetooth reads."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.address = str(entry.data["address"])
        self.companion_id = str(entry.data[CONF_COMPANION_ID])
        self.message_version = int(entry.data.get(CONF_MESSAGE_VERSION, 11))
        self.options = WeberOptions.from_mapping(entry.options)
        self.cloud_enabled = self.options.cloud_enabled
        self.local_fallback = self.options.local_fallback
        self.remote_controls = self.options.remote_controls
        self.poll_seconds = self.options.poll_seconds
        self._poll_task: asyncio.Task[None] | None = None
        self._advertisement_refresh_task: asyncio.Task[None] | None = None
        self._cancel_bluetooth_callback: Callable[[], None] | None = None
        self.cloud_client: WeberCloudClient | None = None
        self.appliance_id = str(entry.data.get(CONF_APPLIANCE_ID) or "") or None
        self.cloud_ready = False
        self.last_error: str | None = None
        self.last_successful_update: str | None = None
        self.consecutive_failures = 0
        if self.cloud_enabled:
            config = CloudConfig.from_mapping(
                {
                    "device_id": self.companion_id,
                    "device_password": entry.data[CONF_CLOUD_PASSWORD],
                    "enabled": True,
                    "temperature_unit": "deci_celsius",
                    "identity_source": "home_assistant",
                    "appliance_id": self.appliance_id,
                }
            )
            self.cloud_client = WeberCloudClient(config)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
        )

    def async_start(self) -> None:
        """Start polling on a wall-clock cadence tied to the config entry."""

        if self._poll_task is not None:
            return
        if not self.cloud_enabled or self.local_fallback:
            self._cancel_bluetooth_callback = bluetooth.async_register_callback(
                self.hass,
                self._async_bluetooth_advertisement,
                {"address": self.address, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        self._poll_task = self.entry.async_create_background_task(
            self.hass,
            self._async_poll_loop(),
            name=f"{DOMAIN} live updates",
        )

    @callback
    def _async_bluetooth_advertisement(
        self,
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Read immediately while a briefly awake hub is still connectable."""

        if (
            self._advertisement_refresh_task is not None
            and not self._advertisement_refresh_task.done()
        ):
            return
        task = self.entry.async_create_background_task(
            self.hass,
            self.async_refresh(),
            name=f"{DOMAIN} Bluetooth wake update",
        )
        self._advertisement_refresh_task = task
        task.add_done_callback(self._async_bluetooth_advertisement_refresh_done)

    @callback
    def _async_bluetooth_advertisement_refresh_done(self, task: asyncio.Task[None]) -> None:
        """Allow the next wake advertisement to trigger another immediate read."""

        if self._advertisement_refresh_task is task:
            self._advertisement_refresh_task = None

    async def _async_poll_loop(self) -> None:
        """Refresh start-to-start so request time is not added to the interval."""

        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            await self.async_refresh()
            delay = max(1.0, self.poll_seconds - (loop.time() - started))
            await asyncio.sleep(delay)

    async def _async_cloud_update(self) -> dict[str, Any]:
        client = self.cloud_client
        if client is None:
            raise WeberCloudError("Cloud access is disabled.")
        if not self.cloud_ready:
            appliances = await self.hass.async_add_executor_job(client.associated_appliances)
            appliance_id = resolve_associated_appliance_id(
                appliances,
                self.appliance_id,
            )
            if appliance_id is None:
                raise WeberCloudError("Online setup is still linking Home Assistant to this hub.")
            self.appliance_id = appliance_id
            self.cloud_ready = True
            if client.config.appliance_id != appliance_id:
                client.config = client.config.with_appliance_id(appliance_id)
        appliance_id = self.appliance_id
        if appliance_id is None:
            raise WeberCloudError("Weber Cloud did not return a hub identity.")
        result = await self.hass.async_add_executor_job(
            client.poll,
            appliance_id,
        )
        status = result.status if result is not None else None
        return normalize_state(
            status,
            source="cloud",
            connected=True,
            cloud_ready=True,
        )

    async def _async_bluetooth_update(self) -> dict[str, Any]:
        # bleak-retry-connector owns the connection deadline. An additional
        # coordinator timeout can cancel Home Assistant while a proxy is still
        # allocating its GATT slot, leaving that proxy with a stale connection.
        status = await async_read_status(
            self.hass,
            self.address,
            self.companion_id,
            self.message_version,
        )
        return normalize_state(
            status,
            source="bluetooth",
            connected=True,
            cloud_ready=self.cloud_ready,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        cloud_error: Exception | None = None
        if self.cloud_enabled:
            try:
                state = await self._async_cloud_update()
                self._record_success()
                return state
            except Exception as exc:
                cloud_error = exc
                _LOGGER.debug("Weber cloud update is not ready", exc_info=True)

        if not self.cloud_enabled or self.local_fallback:
            try:
                state = await self._async_bluetooth_update()
                self._record_success()
                return state
            except WeberBluetoothError as exc:
                self.last_error = str(exc)
                self._record_failure()
                _LOGGER.debug("Weber Bluetooth update failed", exc_info=True)
                return self._offline_state("bluetooth")

        self.last_error = str(cloud_error) if cloud_error else "No transport is available."
        self._record_failure()
        return self._offline_state("cloud")

    def _record_success(self) -> None:
        """Record a healthy update and clear any stale repair."""

        self.last_error = None
        self.last_successful_update = datetime.now(timezone.utc).isoformat()
        self.consecutive_failures = 0
        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            f"connection_lost_{self.entry.entry_id}",
        )

    def _record_failure(self) -> None:
        """Raise one actionable repair only after a sustained outage."""

        self.consecutive_failures += 1
        if self.consecutive_failures < REPAIR_FAILURE_THRESHOLD:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"connection_lost_{self.entry.entry_id}",
            data={"entry_id": self.entry.entry_id},
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="connection_lost",
            translation_placeholders={"name": self.entry.title},
        )

    def _offline_state(self, source: str) -> dict[str, Any]:
        """Keep the last valid readings visible during a transient outage."""

        if self.last_successful_update is not None and self.data:
            state = dict(self.data)
            # A sleeping hub or brief proxy scheduling delay can miss one or
            # two polls between valid wake advertisements. Keep the last good
            # connection state during that short grace window so entities do
            # not flicker offline while fresh readings continue to arrive.
            if self.consecutive_failures >= OFFLINE_FAILURE_THRESHOLD:
                state.update(
                    connected=False,
                    cloud_ready=self.cloud_ready,
                    source=source,
                )
            return state
        return normalize_state(
            None,
            source=source,
            connected=False,
            cloud_ready=self.cloud_ready,
        )

    async def async_session_command(self, command: str) -> None:
        """Run an allowlisted command against an already-active cloud cook."""

        if not self.remote_controls:
            raise HomeAssistantError("Remote cook controls are disabled in integration options.")
        client = self.cloud_client
        active_cook = self.data.get("active_cook") if self.data else None
        if client is None or self.appliance_id is None or not isinstance(active_cook, dict):
            raise HomeAssistantError("No controllable active cook is available.")
        await self.hass.async_add_executor_job(
            client.session_command,
            self.appliance_id,
            active_cook,
            command,
        )
        await self.async_request_refresh()

    async def async_reset_timer(self, timer_index: int) -> None:
        """Reset one existing timer through the cloud companion."""

        if not self.remote_controls:
            raise HomeAssistantError("Remote cook controls are disabled in integration options.")
        if self.cloud_client is None or self.appliance_id is None:
            raise HomeAssistantError("Weber Cloud is not ready.")
        await self.hass.async_add_executor_job(
            self.cloud_client.timer_command,
            self.appliance_id,
            timer_index,
            "reset",
            0,
        )
        await self.async_request_refresh()

    async def async_close(self) -> None:
        """Close the persistent cloud socket during config-entry unload."""

        if self._cancel_bluetooth_callback is not None:
            self._cancel_bluetooth_callback()
            self._cancel_bluetooth_callback = None
        if self._advertisement_refresh_task is not None:
            task = self._advertisement_refresh_task
            self._advertisement_refresh_task = None
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._poll_task is not None:
            task = self._poll_task
            self._poll_task = None
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self.async_shutdown()
        if self.cloud_client is not None:
            await self.hass.async_add_executor_job(self.cloud_client.close)
