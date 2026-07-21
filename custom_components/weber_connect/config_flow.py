"""Native setup and settings for Weber Connect Unofficial."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import voluptuous as vol
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .bluetooth import WeberBluetoothError, async_pair, generate_identity
from .const import (
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
    CONF_MESSAGE_VERSION,
    CONF_PROBE_NAME_PREFIX,
    CONF_PROBES,
    DOMAIN,
    WEBER_COMPANY_IDS,
)
from .models import CompanionIdentity, PairingResult
from .options import ConnectionMode, WeberOptions
from .weber_cloud import (
    CloudConfig,
    WeberCloudClient,
    WeberCloudError,
    resolve_associated_appliance_id,
)

_LOGGER = logging.getLogger(__name__)

_CLOUD_ASSOCIATION_MAX_WAIT = 300.0
_CLOUD_ASSOCIATION_POLL_INTERVAL = 10.0
_CLOUD_PROGRESS_REFRESH_INTERVAL = 1.0
_CLOUD_REQUEST_TIMEOUT = 5.0


class WeberCloudAssociationPending(WeberCloudError):
    """The generated companion is valid but the hub is not associated with it."""


def _monotonic_time() -> float:
    """Return event-loop time through one testable boundary."""

    return asyncio.get_running_loop().time()


def _is_weber(info: Any) -> bool:
    manufacturer_data = getattr(info, "manufacturer_data", {}) or {}
    if any(company_id in WEBER_COMPANY_IDS for company_id in manufacturer_data):
        return True
    name = str(getattr(info, "name", "") or "").lower()
    return any(token in name for token in ("weber", "connect", "june"))


class WeberConnectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Discover and pair one Weber Connect hub."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._address: str | None = None
        self._name = "Weber Connect Hub"
        self._connection_path = "Home Assistant Bluetooth"
        self._identity: CompanionIdentity | None = None
        self._cloud_config: CloudConfig | None = None
        self._pairing_result: PairingResult | None = None
        self._pairing_failure_reason = "The pairing connection ended before setup finished."
        self._cloud_prepare_task: asyncio.Task[None] | None = None
        self._pairing_task: asyncio.Task[PairingResult] | None = None
        self._cloud_task: asyncio.Task[dict[str, Any]] | None = None
        self._cloud_progress_task: asyncio.Task[None] | None = None
        self._cloud_deadline: float | None = None
        self._entry_data: dict[str, Any] | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle discovery from a local adapter or active ESPHome proxy."""

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name or self._name
        self._connection_path = self._discovery_path(discovery_info)
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_confirm()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """List Weber hubs currently visible to Home Assistant."""

        discovered_info = {
            info.address: info
            for info in bluetooth.async_discovered_service_info(self.hass, connectable=True)
            if _is_weber(info)
        }
        if user_input is not None:
            self._address = str(user_input[CONF_ADDRESS])
            selected = discovered_info.get(self._address)
            if selected is not None:
                self._name = selected.name or self._name
                self._connection_path = self._discovery_path(selected)
            self.context["title_placeholders"] = {"name": self._name}
            await self.async_set_unique_id(self._address)
            self._abort_if_unique_id_configured()
            return await self.async_step_confirm()
        if not discovered_info:
            return await self.async_step_no_devices()
        discovered = {
            address: self._discovery_label(info) for address, info in discovered_info.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(discovered)}),
        )

    def _discovery_label(self, info: Any) -> str:
        """Describe the hub and the Home Assistant path that can reach it."""

        name = str(getattr(info, "name", "") or f"Weber hub {info.address}")
        scanner_name = self._discovery_path(info)
        if scanner_name == "Home Assistant Bluetooth":
            return name
        if scanner_name and scanner_name.lower() not in name.lower():
            return f"{name} · via {scanner_name}"
        return name

    def _discovery_path(self, info: Any) -> str:
        """Return the adapter or proxy currently reporting the hub."""

        source = str(getattr(info, "source", "") or "")
        if source:
            scanner = bluetooth.async_scanner_by_source(self.hass, source)
            scanner_name = str(getattr(scanner, "name", "") or "").strip()
            if scanner_name:
                return scanner_name
        return "Home Assistant Bluetooth"

    async def async_step_no_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep setup recoverable when the hub is temporarily out of range."""

        return self.async_show_menu(
            step_id="no_devices",
            menu_options=["search_again"],
        )

    async def async_step_search_again(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run discovery again."""

        return await self.async_step_user()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain the one physical setup action."""

        if self._address is None:
            return await self.async_step_no_devices()
        if user_input is not None:
            return await self.async_step_preparing()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._name,
                "path": self._connection_path,
            },
        )

    def _start_cloud_preparation(self) -> None:
        """Register the private companion before presenting it to the hub."""

        if self._cloud_prepare_task is not None:
            return
        if self._identity is None:
            self._identity = generate_identity()
        if self._cloud_config is None:
            self._cloud_config = CloudConfig.generate(self._identity.companion_id)
        self._cloud_prepare_task = self.hass.async_create_task(
            self._async_prepare_cloud_companion()
        )

    async def _async_prepare_cloud_companion(self) -> None:
        """Create the cloud device before the hub publishes its association."""

        if self._cloud_config is None:
            raise WeberCloudError("The private cloud companion was not generated.")
        cloud_client = WeberCloudClient(
            self._cloud_config,
            timeout=_CLOUD_REQUEST_TIMEOUT,
        )
        try:
            await self.hass.async_add_executor_job(cloud_client.authenticate)
        finally:
            await self.hass.async_add_executor_job(cloud_client.close)

    async def async_step_preparing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Register the private companion before starting Bluetooth pairing."""

        self._start_cloud_preparation()
        task = self._cloud_prepare_task
        if task is None:
            return self.async_show_progress_done(next_step_id="setup_failed")
        if not task.done():
            return self.async_show_progress(
                step_id="preparing",
                progress_action="preparing_companion",
                progress_task=task,
            )
        try:
            await task
        except WeberCloudError as err:
            _LOGGER.warning("Could not prepare the Weber cloud companion: %s", err)
            self._cloud_prepare_task = None
            return self.async_show_progress_done(next_step_id="cloud_preparation_failed")
        except Exception:
            _LOGGER.exception("Unexpected Weber companion preparation failure")
            self._cloud_prepare_task = None
            return self.async_show_progress_done(next_step_id="setup_failed")
        self._cloud_prepare_task = None
        return self.async_show_progress_done(next_step_id="pairing")

    def _start_pairing(self) -> None:
        """Start one pairing attempt while preserving the generated identity."""

        if self._pairing_task is not None:
            return
        if self._address is None:
            raise WeberBluetoothError("The Weber hub is no longer visible.")
        if self._identity is None or self._cloud_config is None:
            raise WeberBluetoothError("The private Weber companion is not ready.")
        self._pairing_task = self.hass.async_create_task(
            async_pair(self.hass, self._address, self._identity)
        )

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait only for approval on the hub."""

        try:
            self._start_pairing()
        except WeberBluetoothError as err:
            self._pairing_failure_reason = str(err)
            return self.async_show_progress_done(next_step_id="pairing_failed")
        task = self._pairing_task
        if task is None:
            self._pairing_failure_reason = "Home Assistant could not start the pairing connection."
            return self.async_show_progress_done(next_step_id="pairing_failed")
        if not task.done():
            return self.async_show_progress(
                step_id="pairing",
                progress_action="approve_hub",
                description_placeholders={
                    "name": self._name,
                    "path": self._connection_path,
                },
                progress_task=task,
            )
        try:
            self._pairing_result = await task
        except WeberBluetoothError as err:
            _LOGGER.warning("Weber hub pairing was not completed: %s", err)
            self._pairing_failure_reason = str(err)
            self._pairing_task = None
            return self.async_show_progress_done(next_step_id="pairing_failed")
        except Exception:
            _LOGGER.exception("Unexpected Weber pairing failure")
            self._pairing_task = None
            return self.async_show_progress_done(next_step_id="setup_failed")
        self._pairing_task = None
        return self.async_show_progress_done(next_step_id="cloud")

    def _start_cloud_setup(self) -> None:
        """Start cloud association after physical pairing has completed."""

        if self._cloud_task is None:
            self._cloud_deadline = _monotonic_time() + _CLOUD_ASSOCIATION_MAX_WAIT
            self._cloud_task = self.hass.async_create_task(self._async_cloud_setup())

    def _cloud_time_remaining(self) -> str:
        """Format the cloud deadline for the progress screen."""

        deadline = self._cloud_deadline
        if deadline is None:
            seconds = int(_CLOUD_ASSOCIATION_MAX_WAIT)
        else:
            seconds = max(0, math.ceil(deadline - _monotonic_time()))
        minutes, remainder = divmod(seconds, 60)
        return f"{minutes}:{remainder:02d}"

    async def _async_wait_for_cloud_progress(self, delay: float) -> None:
        """Wait for either cloud completion or the next countdown redraw."""

        cloud_task = self._cloud_task
        if cloud_task is None:
            return
        await asyncio.wait({cloud_task}, timeout=delay)

    def _cloud_progress_tick(self) -> asyncio.Task[None]:
        """Return a short task that makes Home Assistant redraw the countdown."""

        task = self._cloud_progress_task
        if task is None or task.done():
            delay = _CLOUD_PROGRESS_REFRESH_INTERVAL
            if self._cloud_deadline is not None:
                delay = min(delay, max(0.0, self._cloud_deadline - _monotonic_time()))
            task = self.hass.async_create_task(self._async_wait_for_cloud_progress(delay))
            self._cloud_progress_task = task
        return task

    async def _async_cloud_setup(self) -> dict[str, Any]:
        """Register the generated companion and return durable entry data."""

        if self._address is None or self._identity is None or self._pairing_result is None:
            raise WeberCloudError("Physical pairing did not finish.")
        if self._cloud_config is None:
            raise WeberCloudError("The private cloud companion was not prepared.")
        deadline = self._cloud_deadline
        if deadline is None:
            deadline = _monotonic_time() + _CLOUD_ASSOCIATION_MAX_WAIT

        result = self._pairing_result
        cloud_client = WeberCloudClient(
            self._cloud_config,
            timeout=_CLOUD_REQUEST_TIMEOUT,
        )
        try:
            await self.hass.async_add_executor_job(cloud_client.authenticate)
            appliances = await self.hass.async_add_executor_job(cloud_client.associated_appliances)
            associated = resolve_associated_appliance_id(appliances, result.appliance_id)
            if associated is None:
                associated = await self._async_wait_for_cloud_association(
                    cloud_client,
                    result.appliance_id,
                    deadline=deadline,
                )
            if associated is None:
                raise WeberCloudAssociationPending(
                    "Weber's online service has not finished registering the hub."
                )
        finally:
            await self.hass.async_add_executor_job(cloud_client.close)

        return {
            CONF_ADDRESS: self._address,
            CONF_COMPANION_ID: self._identity.companion_id,
            CONF_MESSAGE_VERSION: result.message_version,
            CONF_APPLIANCE_ID: result.appliance_id,
            CONF_CLOUD_PASSWORD: self._cloud_config.device_password,
        }

    async def _async_wait_for_cloud_association(
        self,
        cloud_client: WeberCloudClient,
        appliance_id: str,
        *,
        deadline: float,
    ) -> str | None:
        """Poll Weber without exceeding the setup's wall-clock deadline."""

        while (remaining := deadline - _monotonic_time()) > _CLOUD_REQUEST_TIMEOUT:
            delay = min(
                _CLOUD_ASSOCIATION_POLL_INTERVAL,
                remaining - _CLOUD_REQUEST_TIMEOUT,
            )
            await asyncio.sleep(delay)
            appliances = await self.hass.async_add_executor_job(cloud_client.associated_appliances)
            associated = resolve_associated_appliance_id(appliances, appliance_id)
            if associated is not None:
                return associated
        return None

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Finish setup without showing Bluetooth approval instructions."""

        self._start_cloud_setup()
        task = self._cloud_task
        if task is None:
            return self.async_show_progress_done(next_step_id="setup_failed")
        if not task.done():
            return self.async_show_progress(
                step_id="cloud",
                progress_action="finishing_setup",
                description_placeholders={
                    "remaining": self._cloud_time_remaining(),
                },
                progress_task=self._cloud_progress_tick(),
            )
        self._cloud_progress_task = None
        try:
            self._entry_data = await task
        except WeberCloudAssociationPending as err:
            _LOGGER.warning("Weber setup is waiting for hub association: %s", err)
            self._cloud_task = None
            self._cloud_progress_task = None
            self._cloud_deadline = None
            return self.async_show_progress_done(next_step_id="cloud_not_linked")
        except WeberCloudError as err:
            _LOGGER.warning("Weber setup could not finish: %s", err)
            self._cloud_task = None
            self._cloud_progress_task = None
            self._cloud_deadline = None
            return self.async_show_progress_done(next_step_id="cloud_unavailable")
        except Exception:
            _LOGGER.exception("Unexpected Weber setup failure")
            self._cloud_task = None
            self._cloud_progress_task = None
            self._cloud_deadline = None
            return self.async_show_progress_done(next_step_id="setup_failed")
        self._cloud_task = None
        self._cloud_deadline = None
        return self.async_show_progress_done(next_step_id="complete")

    async def async_step_pairing_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer explicit recovery after physical approval times out."""

        return self.async_show_menu(
            step_id="pairing_failed",
            menu_options=["retry_pairing", "choose_hub"],
            description_placeholders={"reason": self._pairing_failure_reason},
        )

    async def async_step_cloud_preparation_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain that setup stopped before contacting the hub."""

        return self.async_show_menu(
            step_id="cloud_preparation_failed",
            menu_options=["retry_preparation", "start_over"],
        )

    async def async_step_retry_preparation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retry cloud registration without changing the private identity."""

        self._cloud_prepare_task = None
        return await self.async_step_preparing()

    async def async_step_retry_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retry physical pairing with the same generated identity."""

        self._pairing_task = None
        self._pairing_failure_reason = "The pairing connection ended before setup finished."
        return await self.async_step_pairing()

    async def async_step_cloud_not_linked(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain that Weber has not associated the new companion."""

        return self.async_show_menu(
            step_id="cloud_not_linked",
            menu_options=["retry_cloud", "start_over"],
        )

    async def async_step_cloud_unavailable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain a network or Weber Cloud failure separately."""

        return self.async_show_menu(
            step_id="cloud_unavailable",
            menu_options=["retry_cloud", "start_over"],
        )

    async def async_step_retry_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retry only the final online setup phase."""

        self._cloud_task = None
        self._cloud_progress_task = None
        self._cloud_deadline = None
        return await self.async_step_cloud()

    async def async_step_setup_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Recover cleanly from an unexpected setup failure."""

        return self.async_show_menu(
            step_id="setup_failed",
            menu_options=["start_over"],
        )

    async def async_step_choose_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Return to discovery without leaving a dead-end flow."""

        self._reset_setup()
        return await self.async_step_user()

    async def async_step_start_over(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Restart discovery and generate a fresh identity on the next attempt."""

        self._reset_setup()
        return await self.async_step_user()

    def _reset_setup(self) -> None:
        """Reset transient flow state without touching configured entries."""

        self._cancel_pending_tasks()
        self._address = None
        self._name = "Weber Connect Hub"
        self._connection_path = "Home Assistant Bluetooth"
        self._identity = None
        self._cloud_config = None
        self._pairing_result = None
        self._pairing_task = None
        self._cloud_prepare_task = None
        self._cloud_task = None
        self._cloud_progress_task = None
        self._cloud_deadline = None
        self._entry_data = None

    def _cancel_pending_tasks(self) -> None:
        """Cancel every task privately owned by this config flow."""

        for task in (
            self._cloud_prepare_task,
            self._pairing_task,
            self._cloud_task,
            self._cloud_progress_task,
        ):
            if task is not None and not task.done():
                task.cancel()

    def async_remove(self) -> None:
        """Cancel private work when Home Assistant removes an unfinished flow."""

        self._cancel_pending_tasks()

    async def async_step_complete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry after both setup phases succeed."""

        if self._entry_data is None:
            return await self.async_step_setup_failed()
        return self.async_create_entry(title=self._name, data=self._entry_data)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlow:
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlowWithReload):
    """Present a small set of user-facing settings in native sections."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = WeberOptions.from_mapping(self.config_entry.options).as_dict()
        connection = current[CONF_CONNECTION]
        probes = current[CONF_PROBES]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONNECTION): section(
                        vol.Schema(
                            {
                                vol.Required(
                                    CONF_CONNECTION_MODE,
                                    default=connection[CONF_CONNECTION_MODE],
                                ): SelectSelector(
                                    SelectSelectorConfig(
                                        options=[mode.value for mode in ConnectionMode],
                                        mode=SelectSelectorMode.DROPDOWN,
                                        translation_key="connection_mode",
                                    )
                                ),
                            }
                        ),
                        {"collapsed": False},
                    ),
                    vol.Required(CONF_PROBES): section(
                        vol.Schema(
                            {
                                vol.Optional(
                                    f"{CONF_PROBE_NAME_PREFIX}{number}",
                                    default=probes[f"{CONF_PROBE_NAME_PREFIX}{number}"],
                                ): vol.All(str, vol.Length(max=40))
                                for number in range(1, 5)
                            }
                        ),
                        {"collapsed": True},
                    ),
                }
            ),
        )
