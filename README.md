# AVPro Edge for Home Assistant

A Home Assistant custom integration for **AVPro Edge AUHD-series HDMI matrix switchers**, such as
the AC-MX44-AUHD. It exposes each matrix output as a `media_player` whose source list is the
matrix's inputs, so routing a source to a display is a normal Home Assistant action — usable from
dashboards, scripts, scenes and voice assistants.

Installable and updatable through [HACS](https://hacs.xyz/).

> **Status: early.** Developed against firmware **V1.41**. Other firmware revisions expose
> different endpoints; the integration detects what is present rather than assuming.

## Telnet first, HTTP as the fallback

These matrices offer a telnet command interface on port 23 and a CGI web interface on port 80.
This integration prefers **telnet**, and falls back to HTTP when it cannot have that socket.

Telnet pushes changes within ~300–400 ms from any source — the front panel, the unit's own web
page, anything else on the network — reads the whole device in one command, and is the only wire
that can see output stream state, input power, key lock and the LCD backlight timeout. Under HTTP
those controls are not created at all rather than shown reading unknown.

The one thing telnet **cannot** do is report signal detection. That was established against a live
unit rather than assumed: thirty-two command spellings across two probe rounds every one answered
`CMD ERR`, and `GET STA` carries no signal line. Signal is therefore read over HTTP even while
telnet is connected — the single documented exception, alongside reading the port names once at
setup, since neither is something telnet supports.

The telnet server accepts **exactly one client at a time**: four simultaneous connection attempts
produced one success and three timeouts. If your installation has a control system that needs that
socket, set the transport option to `http` and nothing will ever open port 23. Otherwise an
unavailable control socket is treated as a fault — the integration still falls back so the matrix
stays controllable, but it raises a repair issue and re-checks every minute.

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
| **Transport** | Auto | `auto` prefers telnet and falls back to HTTP. `telnet` refuses to start without it. `http` never opens port 23 — the escape hatch for an installation whose control system needs that socket. |
| **Polling profile** | Balanced (5 s) | How often the matrix is polled. Responsive is 3 s, Gentle is 15 s. On telnet this sets only how often signal is re-read, because everything else is pushed. |
| **Allow writes** | On | When off, the integration is read-only and cannot change routing. Useful while you observe it alongside an existing control system. |

## Actions

Two, for the things that do not fit an entity. Both take the matrix's config entry.

### `ha_avpro_edge.route_all`

Sends every output to one input in a single command rather than one per output — a real difference
on a transport that serialises every request.

```yaml
action: ha_avpro_edge.route_all
data:
  config_entry_id: 01JABCDEF0123456789ABCDEF
  source: 2
```

### `ha_avpro_edge.send_command`

The escape hatch, for anything this integration has not modelled. It returns the device's own
reply, which matters because unsupported commands are answered with `NO SUPPORT` and HTTP 200 —
without the body you cannot tell "it worked" from "it was politely ignored".

```yaml
action: ha_avpro_edge.send_command
data:
  config_entry_id: 01JABCDEF0123456789ABCDEF
  endpoint: video
  command: O1I2
response_variable: result
```

`endpoint` is one of `video`, `audio`, `system`, `edid`, `tmds`. The endpoints that reconfigure the
matrix's network settings or factory-reset it are **absent from that list by construction**, not
merely rejected — a wrong address on a matrix in a wiring closet is a site visit, and that is not
something an automation should be able to cause. Commands are letters and digits only.

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

All eight supported filenames are shipped: `icon.png`, `logo.png`, their `@2x` variants, and a
`dark_` prefixed version of each. AVPro publish the wordmark twice, black-on-white and
white-on-black, so each theme gets the artwork drawn for it rather than one image compromising
across both. `scripts/make_brand_icons.py` generates all eight from the two sources in `assets/`.

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
