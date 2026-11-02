# Production acceptance: physical matrix

[Production readiness](../../PRODUCTION_READINESS.md) retains the release gates
and navigation. This record preserves its version-bound evidence and limits;
see the [product matrix](../product-compatibility.md) for current candidate status.

## Physical release matrix

### Extended compatibility: direct host adapter

This is not a release claim for 3.0. The native transport uses Home Assistant's
standard Bluetooth manager and supports its selected local adapter, but the
final clean-install and endurance evidence used an ESPHome proxy. Complete this
row before describing host-adapter-only operation as physically verified.

1. Discover the hub through the Home Assistant host adapter.
2. Fully close the Weber app and temporarily turn off Bluetooth on every phone
   or tablet that uses it.
3. Complete physical-confirmation pairing, then turn Bluetooth back on.
4. Verify stable entities for every probe slot reported by the device and
   accurate probe temperatures.
5. Restart Home Assistant and confirm that no re-pairing is needed.
6. Reopen the Weber app and verify that one persistent companion WebSocket
   provides simultaneous phone plus Home Assistant telemetry for at least one
   hour at the 10-second cadence.
7. Start a recipe in the app and compare the active probe temperature for the
   full cook without the Home Assistant entity expiring to `Unknown`.
8. Delete the config entry and verify local private data is removed.

### Historical 3.0 local telemetry: one active ESPHome proxy

These steps describe the retired local telemetry path. They are not executable
3.2 release gates; current proxy acceptance covers discovery and physically
confirmed pairing, followed by authenticated cloud telemetry.

Run with the host Bluetooth adapter disabled:

1. Discover and pair through the active proxy.
2. Establish one persistent local GATT session and read direct probe status.
3. Sustain the 10-second cadence for one hour while the same proxy connection
   slot remains owned by the integration.
4. Restart the proxy and verify automatic recovery.
5. Restart Home Assistant and verify automatic recovery.
6. Return to Phone + Home Assistant mode and verify the proxy slot is released
   before the cloud socket starts.

### Historical 3.0 local telemetry: two active ESPHome proxies

This is a non-blocking resilience scenario. No second proxy is available in the
current test environment, so 3.0 does not claim that live connections fail over
between proxies.

Run with the host adapter disabled:

1. Start a local read through proxy A.
2. Make proxy A unavailable while proxy B remains in range.
3. Verify retry re-resolves proxy B without changing entity unique IDs.
4. Restore proxy A and verify Home Assistant may choose either best path.
5. Confirm the selected proxy releases its active connection slot after an
   actual link loss, mode change, or config-entry unload.

### Failure behavior

- Weber Cloud unavailable: registered probe entities remain present and become
  `Unknown` after repeated failures; the integration retries quietly and does
  not silently take Bluetooth from the phone or raise a repair for a routinely
  powered-off hub.
- Proxy out of slots during pairing: setup reports the failure and offers retry;
  an existing entry is preserved during credential replacement.
- Hub out of range: the integration releases resources and recovers on a later
  update.
- Sleeping or powered-off hub: registered probe entities remain visible as
  `Unknown`; this expected idle state does not create a repair.
- Pairing rejected or timed out: no config entry remains. Because the private
  companion must be registered before physical approval, Weber may retain an
  unused server-side companion record; Home Assistant retains no credential
  after the flow is discarded.
- Generated companion credential rejected: Home Assistant creates a distinct
  repair that starts same-hub reauthentication. Successful physical pairing
  replaces credentials in the existing entry; cancellation and failure leave
  its data and references intact. Ordinary connection loss does not request
  replacement credentials.
- Home Assistant unload/reload: the cloud socket closes and no background task
  survives the config entry. Setup GATT connections close on completion,
  cancellation, or failure.
