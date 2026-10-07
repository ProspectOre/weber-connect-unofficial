# Genesis pairing investigation: issue 65

Source: [user report and fresh GATT discovery](https://github.com/ProspectOre/weber-connect-unofficial/issues/65), inspected October 6, 2026.

The reported built-in Genesis controller exposes COMMAND, RESPONSE and STATUS,
but lacks NOTIFICATION and SESSION. The integration assumed the standalone hub's
entire characteristic set was mandatory. It treated either absent characteristic
as an incomplete cache rather than a device capability difference.

The resulting retry cleared Home Assistant's advertisement history. That history
is how `async_ble_device_from_address` resolves a connectable route; clearing it
does not clear GATT services. Without a subsequent advertisement, the next attempt
failed before connecting. The same clearing in final cleanup also impaired manual
retries and removed discovery evidence.

Moreover, `use_services_cache=False` alone does not guarantee fresh services on
ESPHome: the backend can use its cache when remote caching is available. See
[ESPHome backend service discovery and cache clearing](https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/src/bleak_esphome/backend/client.py)
and [Home Assistant Bluetooth history API](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/bluetooth/api.py).

## Correction

- Require COMMAND and RESPONSE before sending a greeting or approval request.
- Subscribe only to characteristics present with notify/indicate capability.
  Use the existing RESPONSE polling fallback when notifications are unavailable.
- Initialize SESSION only when the connected controller exposes it.
- If required channels are missing or disappear during setup, clear the client's
  GATT service cache while connected, release the link, and retry with a new
  client. Keep Home Assistant's advertisement history intact.
- Give each connection its own notification queue, so a failed attempt cannot
  supply responses to its successor.

The greeting, pairing request, physical confirmation requirement, cloud association,
and authenticated telemetry boundary remain enforced.

## Unresolved response framing

After locally skipping the absent characteristics, the reporter reached an
`invalid transport frame` failure and reverted their experiment. No response
capture accompanies the report. This does not establish a particular replacement
wire format: a bare envelope, fragmentation, unrelated channel traffic, or an
invalid response are still possible.

The candidate retains strict transport length, extra-byte, CRC and terminator
checks. Rejected frames now add a bounded structural summary to the existing
support report: received byte count, recognized transport/envelope presence,
transport length/extra-byte checks, envelope CRC and terminator checks. It retains
no packet bytes, payloads, device identities or exception text in that summary.
For example, a valid bare envelope can now be distinguished from an unrecognized
frame without posting raw Bluetooth data.

The next hardware test should retry on this candidate, verify whether physical
approval is requested, and use **Get help / report a problem** if pairing still
fails. Any new framing support needs observed evidence and a regression fixture.
Do not claim full Genesis compatibility or relax validation based on the GATT
table alone.

## Validation scope

Regression tests exercise minimal and partial characteristic sets, read-only
notification channels, missing COMMAND/RESPONSE, retries without a new
advertisement, GATT cache-clear failure/cancellation, stale notification isolation,
and redaction of rejected-frame diagnostics. All physical and endurance receipts
for 3.2.1 remain historical; this transport change requires fresh hardware evidence
before a stable release.

Local validation on October 6, 2026: 371 native tests passed with 100% statement
and branch coverage under Python 3.14.8 / Home Assistant 2026.7.2. Ruff lint and
format checks, strict mypy, Bandit, and `git diff --check` passed. The six capability
and slow-advertisement regressions failed against the unchanged `1148e917` runtime,
including the reported `unknown (never seen by any scanner)` reconnect failure,
and pass on this candidate. `scripts/validate_release.py` rejects the changed
runtime because its automated receipt does not match. No release receipts or
physical compatibility claims were updated.
