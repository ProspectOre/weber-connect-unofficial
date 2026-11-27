# Production readiness

The [README](README.md#choose-a-release) lists the stable and beta channels.
The [compatibility matrix](docs/product-compatibility.md) links results for
each tested model, firmware, and integration release.

## Beta

Betas let users try changes on their own equipment. A beta needs passing CI
and review plus automated and physical receipts tied to its runtime. A
physical receipt can show that qualification is pending. Record individual
hardware results with their exact setup and source as they arrive. See the
[hardware trial guide](docs/support/hardware-trials.md).

## Checks for both channels

CI runs the pinned Home Assistant suites at 100% statement and branch coverage,
the latest Home Assistant smoke lane, lint and type checks, security and
dependency checks, release and support validation, HACS, and Hassfest. The PR
also needs CodeQL and shared review. See the [contributor checks](CONTRIBUTING.md#development-setup).

## Stable

A new stable release needs passing CI and review and the required
[physical acceptance checks](scripts/validate_release.py)
on the release candidate. Verify upgrade and rollback, desktop and mobile
setup, credential replacement and cancellation, stable entity IDs, Activity
filtering, loss and automatic recovery, and live readings alongside the Weber app.

The Connect Hub endurance test must run for at least one hour on the unchanged
candidate. Sample at least every ten seconds. The maximum update gap and
sampled update age are 30 seconds. The run must finish connected with zero
disconnected samples, failed updates, manual recoveries, unexpected reloads,
or capture errors. See the [release validator](scripts/validate_release.py).

Publish compatibility claims only for the models, firmware, and connection
paths exercised. Optional models and connection paths can remain experimental
while they await their own results.

## Previous release evidence

The [historical physical matrix](docs/validation/production-physical-matrix.md)
records earlier transport and hardware trials.

- [3.2.2 stable release and read-only cloud validation](https://github.com/ProspectOre/weber-connect-unofficial/releases/tag/v3.2.2)
- [3.2.1 acceptance and evidence-only amendment](docs/validation/3.2.1-readiness.md)
- [3.2.0 physical acceptance](docs/validation/3.2.0-readiness.md)
- [3.1 automated gates and deployment](docs/validation/production-3.1-evidence.md)
- [3.0 transport and endurance](docs/validation/production-3.0-transports.md), [clean install](docs/validation/production-3.0-clean-install.md), and [community follow-up](docs/validation/production-3.0-follow-up.md)
