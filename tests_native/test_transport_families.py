"""Contract fixtures for distinct Weber advertisement and transport families."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.weber_connect import bluetooth as ble_transport
from custom_components.weber_connect.bluetooth import WeberBluetoothError
from custom_components.weber_connect.config_flow import WeberConnectConfigFlow
from custom_components.weber_connect.models import CompanionIdentity
from custom_components.weber_connect.protocol_adapters import (
    ProtocolQualificationRequired,
    SaberAdapter,
    SiLabsApplianceAdapter,
    adapter_for,
)
from custom_components.weber_connect.saber_frames import (
    APPLIANCE_SERVICE_UUID,
    COMMAND_UUID,
    RESPONSE_UUID,
)
from custom_components.weber_connect.silabs_frames import (
    SiLabsEncryptedFrame,
    SiLabsFrameError,
    build_outgoing_frame,
    parse_incoming_frame,
)
from custom_components.weber_connect.transport_family import (
    JUNE_COMPANY_ID,
    PORTABLE_SERVICE_UUID,
    PORTABLE_TEMPERATURE_UUID,
    SILABS_COMPANY_ID,
    Qualification,
    TransportFamily,
    classify_advertisement,
    classify_discovery,
    classify_service_shapes,
)
from custom_components.weber_connect.weber_cloud import CloudConfig


@pytest.mark.parametrize(
    ("model_id", "model"),
    [
        (1, "saber"),
        (2, "smokefire"),
        (5, "pulse1000"),
        (6, "pulse2000"),
        (7, "spirit"),
        (8, "genesis"),
    ],
)
def test_legacy_advertisement_models(model_id: int, model: str) -> None:
    one_byte = classify_advertisement({JUNE_COMPANY_ID: bytes([model_id])})
    extended = classify_advertisement(
        {JUNE_COMPANY_ID: bytes([2]) + model_id.to_bytes(2, "little") + b"SKU\x00"}
    )
    assert one_byte.family is extended.family is TransportFamily.SABER
    assert one_byte.qualification is extended.qualification is Qualification.PAIRING_READY
    assert one_byte.model == extended.model == model
    assert extended.schema == 2
    assert extended.sku == "SKU"


def test_name_only_smokefire_and_hub_remain_provisional_saber() -> None:
    for name in ("SmokeFire 0638", "Connect Hub", "Weber Connect Hub", "Weber Hub"):
        evidence = classify_discovery(SimpleNamespace(name=name, manufacturer_data={}))
        assert evidence.family is TransportFamily.SABER
        assert evidence.qualification is Qualification.NAME_ONLY
        adapter_for(evidence).require_pairing_support(evidence)
    assert classify_advertisement({}, "SmokeFireplace").family is TransportFamily.UNKNOWN
    with pytest.raises(ProtocolQualificationRequired):
        SaberAdapter().require_pairing_support(
            classify_advertisement({SILABS_COMPANY_ID: _silabs_payload()})
        )


@pytest.mark.parametrize("payload", [b"", b"\x01\x01", b"\x01\x01\x00\xff"])
def test_legacy_bad_payload_never_selects_transport(payload: bytes) -> None:
    assert classify_advertisement({JUNE_COMPANY_ID: payload}).family is TransportFamily.UNKNOWN


def test_legacy_unknown_id_and_conflicting_company_ids_are_unqualified() -> None:
    unknown = classify_advertisement({JUNE_COMPANY_ID: b"\x09"})
    assert unknown.qualification is Qualification.UNKNOWN_CONTROLLER
    conflict = classify_advertisement({JUNE_COMPANY_ID: b"\x01", SILABS_COMPANY_ID: b"\x00"})
    assert conflict.qualification is Qualification.INVALID_ADVERTISEMENT
    with pytest.raises(ProtocolQualificationRequired):
        adapter_for(unknown).require_pairing_support(unknown)
    assert classify_advertisement({JUNE_COMPANY_ID: b"\x01"}, "SmokeFire EX4").model == (
        "smokefire"
    )
    assert (
        classify_service_shapes(unknown, {APPLIANCE_SERVICE_UUID: set()}).family
        is TransportFamily.UNKNOWN
    )


def _silabs_payload(schema: int = 1) -> bytes:
    return bytes([schema]) + bytes(range(1, 9)) + bytes.fromhex("00001234")


def test_silabs_schema_and_sku_need_gatt_not_a_name_guess() -> None:
    evidence = classify_advertisement({SILABS_COMPANY_ID: _silabs_payload()}, "Genesis")
    assert evidence.family is TransportFamily.SILABS_UNRESOLVED
    assert evidence.qualification is Qualification.NEEDS_GATT
    assert evidence.schema == 1
    assert evidence.sku == "1234"
    assert not hasattr(evidence, "serial")
    for payload in (_silabs_payload(4), _silabs_payload()[:-1], b"x"):
        invalid = classify_advertisement({SILABS_COMPANY_ID: payload})
        assert invalid.qualification is Qualification.INVALID_ADVERTISEMENT


def test_gatt_selects_silabs_appliance_or_portable_and_rejects_ambiguity() -> None:
    evidence = classify_advertisement({SILABS_COMPANY_ID: _silabs_payload()})
    appliance_services = {
        APPLIANCE_SERVICE_UUID: set(),
        COMMAND_UUID: {"write"},
        RESPONSE_UUID: {"read", "notify"},
    }
    portable_services = {
        PORTABLE_SERVICE_UUID: set(),
        PORTABLE_TEMPERATURE_UUID: {"read", "notify"},
    }
    appliance = classify_service_shapes(evidence, appliance_services)
    portable = classify_service_shapes(evidence, portable_services)
    assert appliance.family is TransportFamily.SILABS_APPLIANCE
    assert portable.family is TransportFamily.PORTABLE
    assert appliance.qualification is Qualification.NEEDS_SECURE_SESSION
    assert portable.qualification is Qualification.READING_READY
    for family in (appliance, portable):
        adapter_for(family).require_pairing_support(family)
    assert (
        classify_service_shapes(evidence, appliance_services | portable_services).family
        is TransportFamily.UNKNOWN
    )
    assert (
        classify_service_shapes(evidence, {COMMAND_UUID: {"write"}}).family
        is TransportFamily.UNKNOWN
    )


@pytest.mark.parametrize("model_id", [16, 17])
def test_legacy_portable_ids_require_portable_gatt(model_id: int) -> None:
    evidence = classify_advertisement({JUNE_COMPANY_ID: bytes((model_id,))}, "SmokeFire")
    assert evidence.family is TransportFamily.PORTABLE
    assert evidence.qualification is Qualification.NEEDS_GATT
    portable = classify_service_shapes(
        evidence, {PORTABLE_SERVICE_UUID: set(), PORTABLE_TEMPERATURE_UUID: {"read"}}
    )
    assert portable.family is TransportFamily.PORTABLE
    assert portable.qualification is Qualification.READING_READY
    for services in (
        {},
        {APPLIANCE_SERVICE_UUID: set(), COMMAND_UUID: {"write"}, RESPONSE_UUID: {"notify"}},
    ):
        assert classify_service_shapes(evidence, services).family is TransportFamily.UNKNOWN


def test_silabs_incoming_frame_validates_plaintext_without_guessing_encryption() -> None:
    packed = (5 << 28) | 0x12345
    plain = packed.to_bytes(4, "little") + b"\x02\x00\x00\x0b\xf1"
    frame = parse_incoming_frame(plain)
    assert (frame.sequence, frame.boot_count, frame.length, frame.plaintext_body()) == (
        0x12345,
        5,
        2,
        b"\x0b\xf1",
    )
    assert build_outgoing_frame(0x12345, b"\x0b\xf1") == (b"\x45\x23\x01\x00\x02\x00\x00\x0b\xf1")
    for malformed in (
        plain[:6],
        plain + b"extra",
        plain[:4] + b"\x03\x00" + plain[6:],
        plain[:6] + b"\x02" + plain[7:],
    ):
        with pytest.raises(SiLabsFrameError):
            parse_incoming_frame(malformed)
    encrypted = parse_incoming_frame(plain[:4] + b"\x0c\x00\x01" + plain[7:] + b"opaque-tag")
    with pytest.raises(SiLabsEncryptedFrame):
        encrypted.plaintext_body()
    with pytest.raises(SiLabsFrameError):
        build_outgoing_frame(-1, b"")


def test_adapter_does_not_reinterpret_silabs_as_saber() -> None:
    evidence = classify_service_shapes(
        classify_advertisement({SILABS_COMPANY_ID: _silabs_payload()}),
        {APPLIANCE_SERVICE_UUID: set(), COMMAND_UUID: {"write"}, RESPONSE_UUID: {"notify"}},
    )
    adapter = adapter_for(evidence)
    assert isinstance(adapter, SiLabsApplianceAdapter)
    assert (
        adapter.parse_frame(b"\x01\x00\x00\x00\x02\x00\x00\x0b\xf1").plaintext_body() == b"\x0b\xf1"
    )


async def test_read_only_gatt_qualifier_selects_portable_and_disconnects() -> None:
    evidence = classify_advertisement({SILABS_COMPANY_ID: _silabs_payload()})
    temperature = SimpleNamespace(uuid=PORTABLE_TEMPERATURE_UUID.upper(), properties=["read"])
    service = SimpleNamespace(uuid=PORTABLE_SERVICE_UUID.upper(), characteristics=[temperature])
    client = SimpleNamespace(services=SimpleNamespace(services={1: service}))
    with (
        patch.object(ble_transport, "_connect", new=AsyncMock(return_value=client)) as connect,
        patch.object(ble_transport, "_safe_disconnect", new=AsyncMock()) as disconnect,
    ):
        result = await ble_transport.async_qualify_gatt(
            object(),
            "AA:BB:CC:DD:EE:FF",
            evidence,  # type: ignore[arg-type]
        )
    assert result.family is TransportFamily.PORTABLE
    connect.assert_awaited_once()
    disconnect.assert_awaited_once_with(client)


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("family", [TransportFamily.SILABS_APPLIANCE, TransportFamily.PORTABLE])
async def test_simulated_setup_routes_by_qualified_protocol(
    hass: object, automatic: bool, family: TransportFamily
) -> None:
    """Both setup paths classify controller evidence before side effects."""

    discovery = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        name="Genesis",
        source="",
        connectable=True,
        manufacturer_data={SILABS_COMPANY_ID: _silabs_payload()},
    )
    flow = WeberConnectConfigFlow()
    flow.hass = hass  # type: ignore[assignment]
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()  # type: ignore[method-assign]
    flow._abort_if_unique_id_configured = Mock()  # type: ignore[method-assign]
    evidence = classify_service_shapes(
        classify_discovery(discovery),
        (
            {APPLIANCE_SERVICE_UUID: set(), COMMAND_UUID: {"write"}, RESPONSE_UUID: {"notify"}}
            if family is TransportFamily.SILABS_APPLIANCE
            else {PORTABLE_SERVICE_UUID: set(), PORTABLE_TEMPERATURE_UUID: {"read"}}
        ),
    )
    with (
        patch(
            "custom_components.weber_connect.config_flow.bluetooth.async_discovered_service_info",
            return_value=[discovery],
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_qualify_gatt",
            new=AsyncMock(return_value=evidence),
        ) as qualify,
        patch.object(
            flow,
            "async_step_preparing",
            new=AsyncMock(return_value={"type": FlowResultType.FORM, "step_id": "preparing"}),
        ) as cloud,
        patch("custom_components.weber_connect.config_flow.async_pair") as pair,
    ):
        if automatic:
            shown = await flow.async_step_bluetooth(discovery)  # type: ignore[arg-type]
        else:
            chosen = await flow.async_step_user()
            assert chosen["step_id"] == "user"
            shown = await flow.async_step_user({"address": discovery.address})
        assert shown["step_id"] == "confirm"
        stopped = await flow.async_step_confirm({})
    assert stopped["type"] is FlowResultType.FORM
    assert stopped["step_id"] == (
        "preparing" if family is TransportFamily.SILABS_APPLIANCE else "portable_model"
    )
    assert flow._identity is None
    qualify.assert_awaited_once()
    assert cloud.await_count == int(family is TransportFamily.SILABS_APPLIANCE)
    pair.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_reauth_refreshes_visible_family_and_rejects_invalid_persisted_family(
    hass: object,
) -> None:
    entry = SimpleNamespace(
        entry_id="entry",
        unique_id="AA:BB:CC:DD:EE:FF",
        title="Patio",
        data={"address": "AA:BB:CC:DD:EE:FF", "transport_family": "future_family"},
    )
    flow = WeberConnectConfigFlow()
    flow.hass = hass  # type: ignore[assignment]
    flow.context = {"source": "reauth"}
    flow.async_set_unique_id = AsyncMock()  # type: ignore[method-assign]
    flow._get_repair_entry = Mock(return_value=entry)  # type: ignore[method-assign]
    flow._async_in_progress = Mock(return_value=False)  # type: ignore[method-assign]
    visible = [
        SimpleNamespace(address="another", name="Other", manufacturer_data={}),
        SimpleNamespace(
            address=entry.unique_id,
            name="Genesis",
            manufacturer_data={SILABS_COMPANY_ID: _silabs_payload()},
        ),
    ]
    with patch(
        "custom_components.weber_connect.config_flow.bluetooth.async_discovered_service_info",
        return_value=visible,
    ):
        result = await flow._async_begin_repair()
    assert result["step_id"] == "reauth_confirm"
    assert flow._transport_evidence is not None
    assert flow._transport_evidence.family is TransportFamily.SILABS_UNRESOLVED


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_qualification_failures_never_start_cloud_or_pairing(hass: object) -> None:
    flow = WeberConnectConfigFlow()
    flow.hass = hass  # type: ignore[assignment]
    flow.context = {"source": "user"}
    flow._address = "AA:BB:CC:DD:EE:FF"
    flow._transport_evidence = None
    with patch.object(flow, "_start_cloud_preparation") as cloud:
        result = await flow.async_step_confirm({})
    assert result["step_id"] == "protocol_qualification"
    cloud.assert_not_called()

    flow._transport_evidence = classify_advertisement({SILABS_COMPANY_ID: _silabs_payload()})
    with patch(
        "custom_components.weber_connect.config_flow.async_qualify_gatt",
        new=AsyncMock(side_effect=WeberBluetoothError("GATT unavailable")),
    ):
        result = await flow.async_step_confirm({})
    assert result["step_id"] == "protocol_qualification"
    assert flow._qualification_reason == "GATT unavailable"

    flow._identity = CompanionIdentity("11" * 16, "22" * 64)
    flow._cloud_config = CloudConfig.generate(flow._identity.companion_id)
    flow._transport_evidence = None
    with pytest.raises(WeberBluetoothError, match="evidence is missing"):
        flow._start_pairing()
    flow._transport_evidence = classify_advertisement({JUNE_COMPANY_ID: b"\x09"})
    with pytest.raises(WeberBluetoothError, match="unknown_controller"):
        flow._start_pairing()
