"""Config-flow contracts for SiLabs appliance and portable transports."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import FlowResultType

from custom_components.weber_connect import pairing_storage
from custom_components.weber_connect.bluetooth import WeberBluetoothError
from custom_components.weber_connect.config_flow import WeberConnectConfigFlow
from custom_components.weber_connect.const import (
    CONF_APPLIANCE_ID,
    CONF_CAPABILITIES,
    CONF_COMPANION_ID,
    CONF_PAIRING_KEY_REF,
    CONF_TRANSPORT_FAMILY,
    DOMAIN,
)
from custom_components.weber_connect.models import CompanionIdentity, PairingResult
from custom_components.weber_connect.pairing_storage import PairingKeyStore, SiLabsCredentials
from custom_components.weber_connect.silabs_pairing import SiLabsPairingResult
from custom_components.weber_connect.silabs_secure import SiLabsSigningKeys
from custom_components.weber_connect.transport_family import (
    Qualification,
    TransportEvidence,
    TransportFamily,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def mock_bluetooth_dependencies() -> Generator[None]:
    with patch("homeassistant.setup._async_process_dependencies", new=AsyncMock(return_value=[])):
        yield


ADDRESS = "AA:BB:CC:DD:EE:FF"
COMPANION_ID = "11" * 16
APPLIANCE_ID = "22" * 16
OLD_KEY_REF = "aa" * 16
SILABS_COMPANY_ID = 0x0DF2


class MemoryStore:
    """Private store double used by config-flow tests without disk access."""

    records: ClassVar[dict[str, dict[str, Any]]] = {}
    constructions: ClassVar[list[tuple[tuple[Any, ...], dict[str, Any]]]] = []

    def __class_getitem__(cls, _item: object) -> type[MemoryStore]:
        return cls

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.key = str(args[2])
        self.constructions.append((args, kwargs))

    async def async_save(self, value: dict[str, Any]) -> None:
        self.records[self.key] = value

    async def async_load(self) -> dict[str, Any] | None:
        return self.records.get(self.key)

    async def async_remove(self) -> None:
        self.records.pop(self.key, None)


@pytest.fixture(autouse=True)
def isolate_pairing_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    MemoryStore.records = {}
    MemoryStore.constructions = []
    monkeypatch.setattr(pairing_storage, "Store", MemoryStore)


class FakeCloudClient:
    """Fake Weber Cloud with deterministic association and call accounting."""

    calls: ClassVar[list[str]] = []
    appliance_id: ClassVar[str] = APPLIANCE_ID

    def __init__(self, _config: object, *, timeout: float) -> None:
        self.calls.append(f"init:{timeout}")

    def authenticate(self) -> str:
        self.calls.append("authenticate")
        return "fake-token"

    def associated_appliances(self) -> list[dict[str, str]]:
        self.calls.append("associated_appliances")
        return [{"appliance_id": self.appliance_id}]

    def close(self) -> None:
        self.calls.append("close")


def silabs_evidence() -> TransportEvidence:
    return TransportEvidence(
        TransportFamily.SILABS_APPLIANCE,
        Qualification.NEEDS_SECURE_SESSION,
        company_id=SILABS_COMPANY_ID,
        schema=1,
    )


def portable_evidence(model_id: int | None = None) -> TransportEvidence:
    return TransportEvidence(
        TransportFamily.PORTABLE,
        Qualification.READING_READY,
        company_id=SILABS_COMPANY_ID,
        schema=1,
        model_id=model_id,
    )


def silabs_discovery() -> SimpleNamespace:
    return SimpleNamespace(
        address=ADDRESS,
        name="Genesis",
        source="",
        connectable=True,
        manufacturer_data={SILABS_COMPANY_ID: b"\x01" + bytes(range(1, 13))},
    )


async def finish_progress(hass: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Drive completed progress steps until a user decision or entry result."""

    for _attempt in range(12):
        if result["type"] is not FlowResultType.SHOW_PROGRESS:
            return result
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    raise AssertionError("Config flow did not finish after 12 progress updates")


async def prepare_flow(hass: Any, route: str, discovery: SimpleNamespace) -> dict[str, Any]:
    """Start one discovery route and return its confirmation form."""

    if route == "automatic":
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=discovery,
        )
    else:
        with patch(
            "custom_components.weber_connect.config_flow.bluetooth.async_discovered_service_info",
            return_value=[discovery],
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
            )
            assert result["step_id"] == "user"
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_ADDRESS: ADDRESS}
            )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    return result


def paired_result(identity: CompanionIdentity, keys: SiLabsSigningKeys) -> SiLabsPairingResult:
    """Build a synthetic confirmed result that matches the generated keypair."""

    appliance = bytes.fromhex(APPLIANCE_ID)
    peer = SiLabsSigningKeys.generate()
    credentials = SiLabsCredentials(
        bytes.fromhex(identity.companion_id), appliance, keys, peer.public_key, 11
    )
    return SiLabsPairingResult(11, APPLIANCE_ID, credentials, {"probe_count": 4})


@pytest.mark.parametrize("route", ["automatic", "manual"])
async def test_silabs_setup_routes_through_cloud_and_persists_private_keys(
    hass: Any, route: str
) -> None:
    discovery = silabs_discovery()
    identity = CompanionIdentity(COMPANION_ID, "00" * 64)
    observed: dict[str, Any] = {}

    async def fake_pair(
        _hass: object,
        address: str,
        companion: CompanionIdentity,
        keys: SiLabsSigningKeys,
        **_kwargs: object,
    ) -> SiLabsPairingResult:
        result = paired_result(companion, keys)
        observed.update(
            address=address,
            identity=companion,
            keys=keys,
            credentials=result.credentials,
        )
        return result

    FakeCloudClient.calls = []
    FakeCloudClient.appliance_id = APPLIANCE_ID
    with (
        patch(
            "custom_components.weber_connect.config_flow.async_qualify_gatt",
            new=AsyncMock(return_value=silabs_evidence()),
        ) as qualify,
        patch(
            "custom_components.weber_connect.config_flow.generate_identity",
            return_value=identity,
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_pair_silabs",
            side_effect=fake_pair,
        ) as pair_silabs,
        patch(
            "custom_components.weber_connect.config_flow.async_pair",
            new=AsyncMock(side_effect=AssertionError("Saber pairing was dispatched")),
        ),
        patch(
            "custom_components.weber_connect.config_flow.bluetooth_summary",
            return_value={"available": True},
        ),
        patch("custom_components.weber_connect.config_flow.WeberCloudClient", FakeCloudClient),
    ):
        result = await prepare_flow(hass, route, discovery)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await finish_progress(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRANSPORT_FAMILY] == TransportFamily.SILABS_APPLIANCE.value
    assert result["data"][CONF_COMPANION_ID] == COMPANION_ID
    assert result["data"][CONF_APPLIANCE_ID] == APPLIANCE_ID
    assert result["data"][CONF_CAPABILITIES] == {"probe_count": 4}
    assert result["data"][CONF_PAIRING_KEY_REF] == COMPANION_ID
    assert "signing_scalar" not in result["data"]
    assert observed["address"] == ADDRESS
    assert observed["identity"].public_key == observed["keys"].public_key.hex()
    qualify.assert_awaited_once()
    pair_silabs.assert_awaited_once()
    assert FakeCloudClient.calls.count("authenticate") == 2
    assert FakeCloudClient.calls.count("associated_appliances") == 1
    assert FakeCloudClient.calls.count("close") == 2

    reloaded = await PairingKeyStore(hass, COMPANION_ID).async_load(APPLIANCE_ID)
    assert reloaded.companion_id == bytes.fromhex(COMPANION_ID)
    assert reloaded.appliance_id == bytes.fromhex(APPLIANCE_ID)
    assert reloaded.keys.private_scalar == observed["keys"].private_scalar
    assert reloaded.peer_public_key == observed["credentials"].peer_public_key
    assert MemoryStore.records["weber_connect.pairing." + COMPANION_ID]["signing_scalar"]
    assert MemoryStore.constructions
    assert all(
        kwargs == {"private": True, "atomic_writes": True}
        for _args, kwargs in MemoryStore.constructions
    )


@pytest.mark.parametrize("route", ["automatic", "manual"])
async def test_silabs_pairing_error_stops_before_cloud_association(hass: Any, route: str) -> None:
    FakeCloudClient.calls = []
    discovery = silabs_discovery()
    with (
        patch(
            "custom_components.weber_connect.config_flow.async_qualify_gatt",
            new=AsyncMock(return_value=silabs_evidence()),
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_pair_silabs",
            new=AsyncMock(side_effect=WeberBluetoothError("synthetic peer rejected pairing")),
        ) as pair_silabs,
        patch(
            "custom_components.weber_connect.config_flow.async_pair",
            new=AsyncMock(side_effect=AssertionError("Saber pairing was dispatched")),
        ),
        patch(
            "custom_components.weber_connect.config_flow.bluetooth_summary",
            return_value={"available": True},
        ),
        patch("custom_components.weber_connect.config_flow.WeberCloudClient", FakeCloudClient),
    ):
        result = await prepare_flow(hass, route, discovery)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await finish_progress(hass, result)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "pairing_failed"
    pair_silabs.assert_awaited_once()
    assert FakeCloudClient.calls.count("authenticate") == 1
    assert "associated_appliances" not in FakeCloudClient.calls
    assert MemoryStore.records == {}


@pytest.mark.asyncio
async def test_silabs_flow_cancellation_cancels_pair_task_without_saving(hass: Any) -> None:
    discovery = silabs_discovery()
    FakeCloudClient.calls = []
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def waiting_pair(*_args: object, **_kwargs: object) -> PairingResult:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with (
        patch(
            "custom_components.weber_connect.config_flow.async_qualify_gatt",
            new=AsyncMock(return_value=silabs_evidence()),
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_pair_silabs",
            side_effect=waiting_pair,
        ),
        patch(
            "custom_components.weber_connect.config_flow.PairingKeyStore.async_save",
            new=AsyncMock(),
        ) as save,
        patch(
            "custom_components.weber_connect.config_flow.bluetooth_summary",
            return_value={"available": True},
        ),
        patch("custom_components.weber_connect.config_flow.WeberCloudClient", FakeCloudClient),
    ):
        result = await prepare_flow(hass, "manual", discovery)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()

    assert cancelled.is_set()
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_portable_model_selection_progress_retry_and_no_cloud(hass: Any) -> None:
    discovery = silabs_discovery()
    reading = {"sensor_count": 2, "probes": [{"probe_number": 1, "probe_temp_c": 22.5}]}
    read_portable = AsyncMock(
        side_effect=[WeberBluetoothError("first synthetic read failed"), reading]
    )
    cloud_factory = Mock(side_effect=AssertionError("portable setup contacted Weber Cloud"))
    with (
        patch(
            "custom_components.weber_connect.config_flow.async_qualify_gatt",
            new=AsyncMock(return_value=portable_evidence()),
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_read_portable",
            new=read_portable,
        ),
        patch("custom_components.weber_connect.config_flow.WeberCloudClient", cloud_factory),
        patch(
            "custom_components.weber_connect.config_flow.async_pair",
            new=AsyncMock(side_effect=AssertionError("portable setup used Saber pairing")),
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_pair_silabs",
            new=AsyncMock(side_effect=AssertionError("portable setup used appliance pairing")),
        ),
        patch(
            "custom_components.weber_connect.config_flow.generate_identity",
            side_effect=AssertionError("portable setup created a cloud identity"),
        ),
    ):
        result = await prepare_flow(hass, "manual", discovery)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "portable_model"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"model": "17"})
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == "portable"
        result = await finish_progress(hass, result)
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "portable_failed"
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        result = await finish_progress(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRANSPORT_FAMILY] == TransportFamily.PORTABLE.value
    assert result["data"]["model_id"] == 17
    assert result["data"][CONF_CAPABILITIES] == {"sensor_count": 2}
    assert read_portable.await_count == 2
    cloud_factory.assert_not_called()


def _complete_flow(hass: Any, *, source: str, entry_data: dict[str, Any]) -> WeberConnectConfigFlow:
    flow = WeberConnectConfigFlow()
    flow.hass = hass
    flow.context = {"source": source}
    flow._address = ADDRESS
    flow._pairing_result = paired_result(
        CompanionIdentity(COMPANION_ID, "00" * 64), SiLabsSigningKeys.generate()
    )
    flow._entry_data = entry_data
    return flow


@pytest.mark.asyncio
async def test_reauth_wrong_hub_does_not_save_and_replacement_removes_old_key_ref(
    hass: Any,
) -> None:
    wrong = _complete_flow(
        hass,
        source=config_entries.SOURCE_REAUTH,
        entry_data={CONF_ADDRESS: ADDRESS, CONF_APPLIANCE_ID: "33" * 16},
    )
    old_entry = SimpleNamespace(
        entry_id="entry-id",
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_APPLIANCE_ID: APPLIANCE_ID,
            CONF_PAIRING_KEY_REF: OLD_KEY_REF,
        },
    )
    wrong._get_repair_entry = Mock(return_value=old_entry)  # type: ignore[method-assign]
    with (
        patch(
            "custom_components.weber_connect.config_flow.PairingKeyStore.async_save",
            new=AsyncMock(),
        ) as save,
        patch(
            "custom_components.weber_connect.config_flow.bluetooth_summary",
            return_value={"available": False},
        ),
    ):
        result = await wrong.async_step_complete()
    assert result["reason"] == "wrong_hub"
    save.assert_not_awaited()

    replacement = _complete_flow(
        hass,
        source=config_entries.SOURCE_REAUTH,
        entry_data={CONF_ADDRESS: ADDRESS, CONF_APPLIANCE_ID: APPLIANCE_ID},
    )
    replacement._get_repair_entry = Mock(return_value=old_entry)  # type: ignore[method-assign]
    replacement.async_update_reload_and_abort = Mock(  # type: ignore[method-assign]
        return_value={"type": "updated"}
    )
    old_store_key = "weber_connect.pairing." + OLD_KEY_REF
    MemoryStore.records[old_store_key] = {"old": "credentials"}
    result = await replacement.async_step_complete()

    assert result == {"type": "updated"}
    replacement.async_update_reload_and_abort.assert_called_once()
    assert replacement._entry_data[CONF_PAIRING_KEY_REF] == COMPANION_ID
    assert old_store_key not in MemoryStore.records
    assert "weber_connect.pairing." + COMPANION_ID in MemoryStore.records


@pytest.mark.asyncio
async def test_pairing_key_save_failure_does_not_create_entry(hass: Any) -> None:
    flow = _complete_flow(
        hass,
        source=config_entries.SOURCE_USER,
        entry_data={CONF_ADDRESS: ADDRESS, CONF_APPLIANCE_ID: APPLIANCE_ID},
    )
    flow.async_create_entry = Mock(return_value={"type": FlowResultType.CREATE_ENTRY})  # type: ignore[method-assign]
    with (
        patch(
            "custom_components.weber_connect.config_flow.PairingKeyStore.async_save",
            new=AsyncMock(side_effect=OSError("synthetic storage failure")),
        ),
        patch(
            "custom_components.weber_connect.config_flow.bluetooth_summary",
            return_value={"available": False},
        ),
    ):
        result = await flow.async_step_complete()
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "setup_failed"
    flow.async_create_entry.assert_not_called()


@pytest.mark.asyncio
async def test_saber_entry_completion_does_not_write_silabs_key_storage(hass: Any) -> None:
    flow = _complete_flow(
        hass,
        source=config_entries.SOURCE_USER,
        entry_data={CONF_ADDRESS: ADDRESS, CONF_APPLIANCE_ID: APPLIANCE_ID},
    )
    flow._pairing_result = PairingResult(11, APPLIANCE_ID)
    flow.async_create_entry = Mock(return_value={"type": FlowResultType.CREATE_ENTRY})  # type: ignore[method-assign]
    with patch(
        "custom_components.weber_connect.config_flow.PairingKeyStore.async_save",
        new=AsyncMock(),
    ) as save:
        result = await flow.async_step_complete()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    save.assert_not_awaited()


async def test_setup_uses_actual_crypto_gatt_driver_fake_cloud_and_private_store(hass: Any) -> None:
    """Compose the production setup layers around an offline synthetic GATT peer."""

    from tests_native.test_silabs_pairing import APPLIANCE_ID as peer_id
    from tests_native.test_silabs_pairing import COMPANION_ID as companion_id
    from tests_native.test_silabs_pairing import GattClient, Peer

    peer = Peer()
    client = GattClient(peer)
    FakeCloudClient.appliance_id = peer_id.hex()
    with (
        patch(
            "custom_components.weber_connect.bluetooth._connect", new=AsyncMock(return_value=client)
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_qualify_gatt",
            new=AsyncMock(return_value=silabs_evidence()),
        ),
        patch(
            "custom_components.weber_connect.config_flow.generate_identity",
            return_value=CompanionIdentity(companion_id.hex(), "00" * 64),
        ),
        patch(
            "custom_components.weber_connect.config_flow.bluetooth_summary",
            return_value={"available": True},
        ),
        patch("custom_components.weber_connect.config_flow.WeberCloudClient", FakeCloudClient),
    ):
        result = await prepare_flow(hass, "automatic", silabs_discovery())
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await finish_progress(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_APPLIANCE_ID] == peer_id.hex()
    assert result["data"][CONF_CAPABILITIES]["probe_count"] == 4
    record = await PairingKeyStore(hass, companion_id.hex()).async_load(peer_id.hex())
    assert record.peer_public_key == peer.keys.public_key
    assert len(peer.writes) == 3
    client.disconnect.assert_awaited_once()
