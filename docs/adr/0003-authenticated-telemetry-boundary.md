# ADR 0003: Authenticated telemetry boundary

## Status

Accepted and implemented.

## Context

The observed Weber Bluetooth null-session envelope contains length checks, a
CRC, and a terminal marker. Those fields detect accidental corruption but do
not authenticate the sender. The pairing response includes public-key bytes,
but the available protocol evidence does not establish a compatible signature,
MAC, key agreement, or verified sequence contract for subsequent status frames.

Accepting a structurally valid local status frame would therefore let a nearby
Bluetooth peer publish forged temperatures and appliance state. A speculative
cryptographic construction would risk breaking real hubs without establishing
an actual trust boundary.

## Decision

- Live telemetry uses the authenticated Weber Cloud companion only.
- The former `home_assistant_only` option is removed. Existing stored values
  migrate fail closed to `phone_and_home_assistant` when the entry reloads.
- Bluetooth remains available only for physically confirmed companion setup.
- The pairing decoder allowlists setup response types and rejects cook-status
  and appliance-status frames.
- Cloud association with the exact paired appliance remains required before a
  config entry is created; local approval alone is not sufficient.

## Consequences

The official Weber app can keep the hub's Bluetooth connection while Home
Assistant receives authenticated cloud telemetry. Installations require Weber
Cloud for runtime updates. The former proxy-only telemetry feature is removed
until a protocol-compatible peer-authentication mechanism can be documented,
implemented, and validated on physical hardware.

Historical proxy-only availability evidence remains useful as a record, but it
is not evidence that the retired null-session path was authenticated.
