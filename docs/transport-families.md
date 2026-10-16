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

The SiLabs adapter uses a seven-byte header and a source-backed authenticated
setup flow: P-256 pairing request, physical confirmation response, ECDH plus
SHA-256 to derive AES-256-GCM, plaintext companion greeting, authenticated
handshake success, then encrypted capability negotiation. Reads, writes and
response counts are bounded. Invalid tags, keys, framing and duplicate
nonces/sequences within one connection are rejected. The transport header is
not GCM authenticated by the app protocol, and session freshness across separate
connections has no demonstrated challenge/response guarantee.

After Bluetooth setup, the existing cloud association check must match the
BLE-confirmed appliance identity. Only then are keys saved in a separate Home
Assistant private (0600), atomic storage file; config data contains its reference
and a capability summary. This is private-file protection, not an encrypted-at-
rest or OS-keychain guarantee. Protect Home Assistant backups like other secrets.
A stored-key resume path repeats the authenticated greeting and capabilities
without a new pairing request. Normal grill telemetry and recovery continue
through the existing cloud session. Replacement pairing preserves the old entry
until the new cloud association and private save succeed; deletion removes the
private record. Capabilities select available probe slots without name rules.

Portable probes use their own local GATT read/notify transport with no appliance
pairing command or cloud registration. The app's packed sequence/boot count,
battery byte, sensor count and signed deci-C readings are parsed separately;
WASP uses ambient temperature and BATWOMAN uses the coldest non-ambient segment.
An unknown catalogue model requires the owner to select the probe type. This
application payload has no demonstrated cryptographic authentication. Link
bond/authorization requirements and the app's portable account association
coroutine remain unestablished; a GATT authorization failure is reported without
attempting a bond or changing security settings.

Tests use generated synthetic peers, mock storage and mock HA Bluetooth routes.
They do not establish any grill or probe's hardware acceptance. Unknown or
conflicting evidence still stops before credentials or commands are generated.

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
it does **not** claim physical SiLabs or portable acceptance. Before a hardware
compatibility claim, obtain consented sanitized evidence for each controller type:

1. Manufacturer company ID, payload length, schema, model/SKU fields (redact
   serial/address), and GATT service/characteristic properties.
2. Greeting and pairing **structural** results: frame lengths, sequence/boot
   count, encryption flag, message type, accepted/rejected/timeout outcomes.
   Do not include keys, nonces, ciphertext or account identifiers.
3. Validate the independent secure-session implementation against consented
   device evidence, including protected identity persistence and reconnect
   behavior. Local portable readings have no application authentication claim.
4. Capability and message-version outcomes, cloud association, restart/reconnect,
   and a live cook or probe reading for the exact product/firmware candidate.

Genesis [issue #65](https://github.com/ProspectOre/weber-connect-unofficial/issues/65)
is a priority candidate because its reported OTA service resembles the SiLabs
branch and its greeting fails Saber framing. This remains a hypothesis until
the structural fixture and physical outcome are captured. SmokeFire EX4 Gen2
pairing/cloud association has contributor evidence; cook acceptance remains
pending. These gates are independent of unit-test coverage and CI.
