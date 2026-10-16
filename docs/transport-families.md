# Bluetooth transport qualification

The official Weber Connect Android app `2.12.0.2665` distinguishes legacy
June/Saber advertisements (company `0x07c5`) from SiLabs advertisements
(`0x0df2`). SiLabs appliances and portable probes have separate interpreters.
Saber and SiLabs appliances can share the `3103` command and `3108` response
characteristics while using different transport headers. Product names and a
usable GATT table alone cannot choose a frame parser.

## Integration behavior

Automatic and manual discovery use the same advertisement classifier. Legacy
model IDs `1, 2, 5, 6, 7, 8` select the existing Saber pairing adapter. A
legacy model ID `16` or `17` marks a portable probe candidate and requires a
portable GATT table before its separate adapter is selected. A
recognized SmokeFire or Hub name without manufacturer data remains a provisional
Saber candidate for backward compatibility; the existing GATT and strict frame
checks still apply. Unknown or malformed company payloads are never sent a
pairing command.

SiLabs schema versions `0..3` and minimum payload length 13 qualify the
advertisement. A read-only GATT check then selects appliance service `0101…`
with usable `3103`/`3108`, or portable service `a9ad69c3…` with its temperature
characteristic. Ambiguous or incomplete services remain unqualified. This check
does not write to a device or register a cloud companion.

The SiLabs frame parser validates the seven-byte header, sequence, boot count,
flag, and wire-body length. The isolated `silabs_secure` contract builds a
P-256 public key from two 32-byte coordinates, derives an AES-256 key from
SHA-256(ECDH shared secret), and checks AES-GCM messages with a 12-byte nonce
and 16-byte tag. Synthetic peers exercise the plaintext version-11 pairing
request (`0x0a`), confirmation/rejection/timeout response (`0x85`), plaintext
greeting (`0x70`), encrypted handshake success (`0xf2`), tampering and replay.
This contract has no GATT writes, cloud calls or identity persistence yet;
consequently it does not enable live SiLabs setup. Portable probes use a separate
GATT contract and are never fed appliance frames. Their authenticated pairing
and reading mapping have not been independently verified. Both families show a
specific qualification screen before cloud registration or pairing, with a
privacy-safe support report. Existing Saber pairing and cloud telemetry remain
active; successful entries store their transport family and discovery schema.
Reauthentication checks a currently visible advertisement again while retaining
the old entry until replacement pairing and cloud association succeed.

The static source for this contract is the official Android
`com.weber.connect` `2.12.0.2665` base APK (SHA-256
`b1c2669dfdaca50832e373d2ddaa3ff7d380653f84d14c679949c58772fd3be6`):
`ManufacturerData` and `ScanForAppliancesUseCase` for advertisements;
`SiLabsApplianceInterpreter`, `OutgoingSiLabsTransportHeader` and
`IncomingSiLabsTransportHeader` for framing; `PairingRequestMessage`,
`IncomingPairingResponseMessage` and `ExchangeKeysForSiLabsAction` for pairing;
`KeyUtils`, `SiLabsEncryptor` and `SiLabsSecureSessionManager` for security.
`BleEncryptorFactory` selects the Java SiLabs encryptor; its JNI library is
used by the separate JOSL path. All tests here use synthetic peers and frames.

## Hardware gates

The code provides first-class classification and explicit adapter boundaries;
it does **not** claim physical SiLabs or portable pairing acceptance. To enable
those adapters, obtain consented sanitized evidence for each controller type:

1. Manufacturer company ID, payload length, schema, model/SKU fields (redact
   serial/address), and GATT service/characteristic properties.
2. Greeting and pairing **structural** results: frame lengths, sequence/boot
   count, encryption flag, message type, accepted/rejected/timeout outcomes.
   Do not include keys, nonces, ciphertext or account identifiers.
3. Validate the independent secure-session implementation against consented
   device evidence, then add protected identity persistence and reconnect
   handling before live pairing or claiming authenticated telemetry.
4. Capability and message-version outcomes, cloud association, restart/reconnect,
   and a live cook or probe reading for the exact product/firmware candidate.

Genesis [issue #65](https://github.com/ProspectOre/weber-connect-unofficial/issues/65)
is a priority candidate because its reported OTA service resembles the SiLabs
branch and its greeting fails Saber framing. This remains a hypothesis until
the structural fixture and physical outcome are captured. SmokeFire EX4 Gen2
pairing/cloud association has contributor evidence; cook acceptance remains
pending. These gates are independent of unit-test coverage and CI.
