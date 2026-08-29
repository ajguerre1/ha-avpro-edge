# AVPro Edge for Home Assistant

A Home Assistant custom integration for **AVPro Edge AUHD-series HDMI matrix switchers**, such as
the AC-MX44-AUHD. It exposes each matrix output as a `media_player` whose source list is the
matrix's inputs, so routing a source to a display is a normal Home Assistant action — usable from
dashboards, scripts, scenes and voice assistants.

Installable and updatable through [HACS](https://hacs.xyz/).

> **Status: early.** Developed against firmware **V1.41**. Other firmware revisions expose
> different endpoints; the integration detects what is present rather than assuming.

## Why HTTP and not telnet

These matrices offer both a telnet command interface on port 23 and a CGI web interface on port
80. This integration deliberately uses **HTTP only**.

The telnet server on the tested unit accepts **exactly one client at a time** — four simultaneous
connection attempts produced one success and three timeouts. In a typical installation that single
slot is already held by a control system (Control4, Crestron, RTI), which holds it open
persistently. An integration that took the telnet socket would take it *from* that controller.

The HTTP interface has no such limit, is stateless, and is what the unit's own web UI uses, so
Home Assistant coexists with whatever else already controls the matrix. The cost is that Home
Assistant polls rather than being pushed to, and that a handful of telnet-only commands are not
reachable.

## Installation

### HACS (recommended)

1. In Home Assistant, open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/ajguerre1/ha-avpro-edge` with category **Integration**.
3. Find **AVPro Edge** in HACS and install it.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → + Add integration** and search for **AVPro Edge**.

### Manual

Copy `custom_components/ha_avpro_edge/` into your Home Assistant `config/custom_components/`
directory and restart Home Assistant.

## Configuration

Configuration is entirely through the UI. You are asked for one thing:

| Field | Meaning |
|---|---|
| **Host** | The matrix's IP address or hostname. Scheme and path are stripped, so `http://192.0.2.10/` works. |

The integration then reads the unit's identity, confirms it is an AVPro matrix, and registers it.
The number of inputs and outputs is read from the device rather than assumed, so non-4x4 models in
the same family should work.

### Options

Reachable from the integration's **Configure** button. Changes apply immediately without a reload.

| Option | Default | Meaning |
|---|---|---|
| **Polling profile** | Balanced (5 s) | How often the matrix is polled. Responsive is 3 s, Gentle is 15 s. |
| **Allow writes** | On | When off, the integration is read-only and cannot change routing. Useful while you observe it alongside an existing control system. |

## Removal

**Settings → Devices & services → AVPro Edge → ⋮ → Delete**. That removes the config entry, its
device and all of its entities. To remove the code as well, uninstall it from HACS (or delete
`custom_components/ha_avpro_edge/`) and restart Home Assistant. The integration writes nothing to
the matrix on removal — the matrix keeps whatever routing it had.

## Development

```bash
pip install -r requirements-test.txt
pytest tests/ -v          # offline suite, runs on Windows
ruff check . && ruff format --check .
```

Install the pre-push hook, which runs all three before letting a push through:

```bash
cp scripts/hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

`ruff check` and `ruff format --check` are separate checks and the second is easy to forget,
because it usually passes — until ruff reformats a Python block inside a Markdown file. The hook
exists so that is caught before the push rather than after it.

`tests/ha/` needs `pytest-homeassistant-custom-component`, which pulls in Home Assistant and
therefore cannot be imported on Windows (`homeassistant.runner` imports POSIX-only `fcntl`). Those
tests run in CI. Everything under `custom_components/ha_avpro_edge/avpro/` has no Home Assistant
imports and runs anywhere.

`tools/fake_avpro.py` is a fake matrix that speaks the real CGI protocol with fault injection, so
the client can be developed and tested without touching hardware.

## Brand images

The icon and logo live in `custom_components/ha_avpro_edge/brand/` and that is all that is needed.

**No submission to `home-assistant/brands` is required, and one would be refused.** Since Home
Assistant 2026.3, custom integrations ship their own brand images: the frontend fetches them
through `/api/brands/integration/{domain}/{image}`, and a local `brand/` directory takes priority
over the brands CDN. The brands repository's own pull request template now says outright that
"pull requests for adding new custom components will no longer be accepted".

Verified rather than assumed: on a 2026.8 instance, `GET /api/brands/integration/{domain}/icon.png`
returns the integration's committed `brand/icon.png` byte for byte, confirmed by SHA-256 against
two other installed custom integrations.

Supported filenames are `icon.png`, `logo.png`, their `@2x` variants, and a `dark_` prefixed
version of each for dark themes. This repository ships the four non-`dark_` files; the artwork is
white-on-black, which reads on either theme, so a separate dark variant would be the same image.

## Trademarks

**AVPro Edge** and the AVPro Edge logo are trademarks of their owner. This project is an
independent integration and is **not affiliated with, endorsed by, or supported by AVPro Edge**.

The mark appears under `custom_components/ha_avpro_edge/brand/` for one reason: to identify which
device the integration controls, which is what an integration icon in Home Assistant is for. It is
generated from the manufacturer's wordmark by `scripts/make_brand_icons.py`, and the artwork
remains the property of its owner.

## License

MIT — covering the code in this repository. The licence does not extend to the AVPro Edge marks
under `brand/` and `assets/`; see **Trademarks** above.
