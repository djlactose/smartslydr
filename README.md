# Lychee Things (SmartSlydr) — Home Assistant Integration

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz)
[![GitHub Release](https://img.shields.io/github/v/release/djlactose/smartslydr?display_name=tag&sort=semver)](https://github.com/djlactose/smartslydr/releases/latest)
[![Pre-release](https://img.shields.io/github/v/release/djlactose/smartslydr?include_prereleases&label=pre-release)](https://github.com/djlactose/smartslydr/releases)
[![Downloads](https://img.shields.io/github/downloads/djlactose/smartslydr/total.svg)](https://github.com/djlactose/smartslydr/releases)
[![Validate](https://github.com/djlactose/smartslydr/actions/workflows/validate.yml/badge.svg)](https://github.com/djlactose/smartslydr/actions/workflows/validate.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/djlactose/smartslydr.svg)](https://github.com/djlactose/smartslydr/stargazers)

Custom integration that brings **SmartSlydr** devices (sold under the
**LycheeThings** brand) into Home Assistant via the official
[SmartSlydr REST API v0.4](SmartSlydr_REST_API_0_4.pdf).

The integration name in Home Assistant and HACS is **Lychee Things**;
internally the domain is `smartslydr`.

## Features

- **Cover** entity per device — open / close / stop / set position (0–100).
- **Sensors** per device:
  - Temperature (°C native; HA can convert to °F via the entity's UI settings).
  - Humidity (%).
  - WLAN signal (dBm).
  - Sound level (dB).
  - WLAN MAC.
  - Device status (string, e.g. `device is online`).
- **Petpass switch** per device — toggle the door open/closed; lists allowed pet
  names as an attribute.
- All entities for one physical SmartSlydr unit are grouped under a single
  device in the Home Assistant device registry.
- Single **DataUpdateCoordinator** drives polling: one batched `/devices` +
  `/operation/get` cycle per scan interval, regardless of entity count.
- Authentication, token refresh (with safety margin), and re-auth on refresh
  failure are handled automatically.

## Requirements

- Home Assistant **2025.5.0** or later (declared in `hacs.json`).
- A SmartSlydr account with at least one device already onboarded and
  calibrated through the LycheeThings mobile app.

## Installation

### A) HACS (recommended)

This integration is available in the **default HACS catalog** — no custom
repository setup required.

1. In Home Assistant: **HACS → Integrations**.
2. Search **Lychee Things** and click the result.
3. Click **Download** and confirm.
4. Restart Home Assistant.

If for some reason it isn't visible (e.g. HACS catalog hasn't refreshed
yet, or you're on an older HACS version), fall back to adding it as a
custom repository:

1. **HACS → Integrations → ⋮ (top-right) → Custom repositories**.
2. URL `https://github.com/djlactose/smartslydr`, category **Integration**.
3. Search and install as above.

#### Beta channel

Pre-release builds are published from the `develop` branch as
`vX.Y.Z-beta.N` GitHub releases. To opt in:

- HACS → the Lychee Things integration → ⋮ → **Redownload**.
- In the version selector, enable **Show beta versions** and pick the latest
  `-beta.N` build.

Stable builds come from the `main` branch as `vX.Y.Z` releases. HACS hides
betas from users who haven't opted in.

### B) Manual

1. Download or clone this repo.
2. Copy `custom_components/smartslydr/` into your Home Assistant
   `config/custom_components/` directory (folder name must stay lowercase).
3. Restart Home Assistant.

(No `aiohttp` or other extra Python packages need to be installed —
`aiohttp` ships with Home Assistant core.)

## Configuration

[![Add to your Home Assistant.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smartslydr)

The button above starts the config flow on your instance. Or do it manually:

1. **Settings → Devices & Services → Add Integration**.
2. Search **Lychee Things** and enter your SmartSlydr **username** (email)
   and **password**.
3. (Optional) Adjust the **Scan Interval** under
   **Settings → Devices & Services → Lychee Things → Configure**. Default is
   300 seconds. Lower values poll the cloud API more often; the SmartSlydr
   API has no published rate limit but a sensible minimum is ~30 seconds.

A given email can only be added once — the config flow rejects duplicate
entries.

## Entities & Devices

For each SmartSlydr device the integration creates one entry in the device
registry, plus the entities below (only if the corresponding field appears
in the `/devices` response):

| Entity                       | Source field    | Notes                                   |
|------------------------------|-----------------|-----------------------------------------|
| Cover (`cover.*`)            | `position`      | 0 = closed, 100 = open, 200 = stop cmd. |
| Temperature (`sensor.*`)     | `temperature`   | °C; HA converts to °F per user pref.    |
| Humidity (`sensor.*`)        | `humidity`      | %                                       |
| WLAN signal (`sensor.*`)     | `wlansignal`    | dBm                                     |
| Sound (`sensor.*`)           | `sound`         | dB                                      |
| WLAN MAC (`sensor.*`)        | `wlanmac`       | string                                  |
| Status (`sensor.*`)          | `status`        | e.g. `device is online`                 |
| Petpass switch (`switch.*`)  | `petpass`       | see note below                          |

The Petpass switch's on/off state is fetched from `/operation/get` (command
`petpass`) once per coordinator refresh, and toggled via `/operation`. Its
`allowed_pets` attribute lists the pet names from the device's petpass slot
list.

### Stop command

`cover.stop_cover` sends the documented stop value (`position = 200`) per the
API spec.

## Debug logging

The API client emits per-request debug logs when the helper boolean
`input_boolean.smartslydr_debug_mode` is `on`. To use it:

1. Create the helper: **Settings → Devices & Services → Helpers →
   Create helper → Toggle**, name it `SmartSlydr Debug Mode`. The entity ID
   must be exactly `input_boolean.smartslydr_debug_mode`.
2. Make sure debug-level logs are captured for this integration. Add to
   `configuration.yaml`:

   ```yaml
   logger:
     default: warning
     logs:
       custom_components.smartslydr: debug
   ```

3. Toggle the helper on; per-request request/response bodies appear in the
   HA log. Toggle it off when done — the integration won't log payloads
   while the helper is off, even if the logger level is debug.

## Troubleshooting

### `Unexpected /devices response: { 'errorType': 'TypeError', ... }`

This is the **upstream Lambda backend** crashing — not the integration. The
integration logs the raw error JSON for diagnosis. Check whether the
LycheeThings mobile app can still see your devices; if it can, the public
REST API has regressed and you'll need to report it to LycheeThings (a
ready-to-send draft is in
[`BUG_REPORT_LYCHEETHINGS.md`](BUG_REPORT_LYCHEETHINGS.md)).

### `auth_failed` when adding the integration

Your username (account email) or password is wrong. Reset via the
LycheeThings mobile app and try again.

### `cannot_connect` when adding the integration

The integration couldn't reach
`https://34yl6ald82.execute-api.us-east-2.amazonaws.com/prod` — typically a
network or DNS issue on the HA host, or AWS service disruption. Retry; HA
will not auto-retry the config flow itself, but it will auto-retry an
already-set-up entry on the next scan interval.

### Entities go stale

Polling errors after first setup are surfaced via the standard HA "device
unavailable" UI. The coordinator keeps retrying with backoff — there's no
need to reload manually unless the issue persists for several intervals.

### A new device added to the account doesn't appear

Entities are created at integration setup. After adding a device in the
LycheeThings mobile app, **reload the integration**:
**Settings → Devices & Services → Lychee Things → ⋮ → Reload**.

## Known limitations

### Door-button accessory devices are not supported

The LycheeThings mobile app exposes a "door button" accessory (a separate
button device that opens the door, behaving like a global petpass toggle).
The integration does **not** surface this device because it is not returned
by the public REST API v0.4 — `GET /devices` only returns the primary
SmartSlydr units. Speculative endpoint and command probing on a real
account confirmed the door button is served by an internal API the public
v0.4 spec doesn't document. Support will require either a v0.5+ public
API extension from LycheeThings, or reverse-engineering the mobile app's
internal endpoint via packet capture. If you're affected, please file an
upstream feature request — the integration will pick it up automatically
once the data is in `/devices`.

## Reporting bugs

Open an issue at
[github.com/djlactose/smartslydr/issues](https://github.com/djlactose/smartslydr/issues).
Please include:

- The integration version (HACS shows it; or read `manifest.json`).
- HA version (**Settings → About**).
- Relevant lines from `home-assistant.log`, ideally with debug logging
  enabled (see above).

## Development

A `Dockerfile` and `.devcontainer/devcontainer.json` are included to spin
up a local HA instance with this integration mounted at
`/config/custom_components/smartslydr/`. From the repo root:

```bash
docker build -t smartslydr-ha .
docker run --rm -p 8123:8123 -v $PWD/custom_components/smartslydr:/config/custom_components/smartslydr smartslydr-ha
```

Then visit <http://localhost:8123>.

CI (`.github/workflows/validate.yml`) runs HACS validation and `hassfest`
on every push and PR, plus weekly. Releases are automated by
`.github/workflows/release.yml`:

- Push to `main` → tag `vX.Y.Z` (using `manifest.json` version) → stable
  GitHub release.
- Push to `develop` → tag `vX.Y.Z-beta.N` (auto-incrementing) → pre-release.

To cut a stable release, bump `version` in
`custom_components/smartslydr/manifest.json` and merge to `main`.

## Support the project

If this integration is useful to you and you'd like to say thanks, you can
buy me a coffee:

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-djlactose-yellow?logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/djlactose)

Sponsorship is appreciated but never required — bug reports, fixes, and
documentation improvements are equally welcome via the
[issue tracker](https://github.com/djlactose/smartslydr/issues).

## License

MIT — see [`LICENSE`](LICENSE).
