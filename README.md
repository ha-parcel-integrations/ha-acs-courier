# ACS Courier Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-acs-courier.svg)](https://github.com/ha-parcel-integrations/ha-acs-courier/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-acs-courier/total.svg)](https://github.com/ha-parcel-integrations/ha-acs-courier/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

> [!WARNING]
> **Pre-release.** Only the returned-to-sender status has been confirmed on a
> real parcel so far. Other statuses may show as `in_transit` or `unknown` until
> more parcels have been seen. If you see a warning line in the log, please
> report it through the issue link it contains.

A custom Home Assistant integration that tracks your [ACS Courier](https://www.acscourier.net) parcels in Greece. No account is needed: enter a tracking code you are authorised to follow.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of ACS Courier parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text and a link to the ACS tracking page
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- `acs_courier.track_parcel` / `acs_courier.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- An ACS Courier parcel and its tracking code (from the shipping
  confirmation or the sender) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-acs-courier` as an **Integration**.
3. Install **ACS Courier** and restart Home Assistant.

### Manual

Copy `custom_components/acs_courier` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → ACS Courier**. There is nothing to fill in: the hub is created immediately (ACS Courier tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`acs_courier.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every ACS Courier
  hub out there polls at exactly the same second.

ACS Courier does not yet provide a delivery window this integration can use, so an out-for-delivery parcel is always checked every 15 minutes.

## Removal

Standard HA removal applies: **Settings → Devices & Services → ACS Courier → ⋮ → Delete**. Nothing is stored on ACS Courier's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.acs_courier_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.acs_courier_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.acs_courier_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.acs_courier_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.acs_courier_last_successful_update` | Diagnostic: when ACS Courier was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `in_transit` | Picked up, at a sorting centre or at the delivery station |
| `returning` | Returned to the sender |
| `unknown` | A status we have not mapped yet |

Only states seen on real parcels are mapped so far. Other statuses (out for
delivery, delivered and so on) show as `in_transit` or `unknown` until they are
reported; the log then asks you to open an issue.

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the ACS Courier device):

| Event | When |
|---|---|
| `acs_courier_parcel_registered` | A new parcel appears in the active list |
| `acs_courier_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `acs_courier_parcel_delivered` | A parcel is delivered |
| `acs_courier_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `acs_courier.track_parcel` | `tracking_code` | Start tracking a parcel |
| `acs_courier.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.acs_courier: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — ACS Courier has no record of the code yet, or the code is wrong. It will pick up automatically once scanned.
- **A status logs "Unrecognised ACS Courier status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-acs-courier/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by ACS Courier, Home Assistant, or any other third party referenced in this project. Please don't contact ACS Courier for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to ACS Courier's terms. Data is sent only to ACS Courier's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the same public tracking endpoint as the ACS Courier consumer website.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
