# Contributing

Notes for working on the integration itself. If you just want to use it, the
[README](README.md) is the place to start.

## Getting set up

```bash
pip install -r requirements-test.txt
```

Install the pre-push hook, which runs the same checks CI does:

```bash
cp scripts/hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

`ruff check` and `ruff format --check` are two separate checks, and the second is easy to forget
because it usually passes — right up until ruff reformats a Python block inside a Markdown file.
The hook exists so that is caught before the push rather than after it.

## Running the tests

```bash
pytest tests/                    # everything
pytest tests/ --ignore=tests/ha  # the part that runs on any platform
```

The suite is in two halves:

- **`tests/`** needs nothing but Python and runs anywhere, including Windows. Everything under
  `custom_components/ha_avpro_edge/avpro/` is plain Python with no Home Assistant imports, which is
  what keeps this half portable.
- **`tests/ha/`** needs `pytest-homeassistant-custom-component`, which pulls in Home Assistant
  itself. Home Assistant cannot be imported on Windows at all — it needs a POSIX-only module — so
  this half runs in CI.

## The fake matrix

`tools/fake_avpro.py` is a stand-in for the hardware that speaks the real protocol on both of its
connections, with fault injection. It means the client can be developed and tested without a matrix
on the desk.

```bash
python tools/fake_avpro.py --list-faults
```

Each fault exists to prove one specific defence. A fault nothing asserts on is dead weight, and a
defence with no fault behind it is a claim rather than a test — there is a test that enforces both
directions.

**A fake is a model, and a model can be wrong.** `scripts/probe_fidelity.py` compares the fake's
responses against a real unit and reports anything only one of them produces. It is worth running
against new hardware or new firmware; the two directions are not symmetric, and the one that
matters is the device producing something the fake cannot, because no test can then reach it.

## Probing real hardware

Three scripts talk to a real matrix. All of them refuse to send anything outside their remit — the
guard is in the send path, not a convention about which strings appear in a list.

| Script | What it does |
|---|---|
| `scripts/probe_fidelity.py` | Compares the fake against a real unit. Read-only. |
| `scripts/probe_telnet_signal.py` | Explores what the control connection can and cannot report. Read-only. |
| `scripts/probe_lcd_keylock.py` | Confirms which front-panel values the hardware accepts. Writes, then restores what it found. |

Output is scrubbed before it is printed or saved: names, addresses and hardware identifiers are
replaced by position, not by pattern, because a pattern has to anticipate what something might be
called and a position does not.

## Brand images

The icon and logo live in `custom_components/ha_avpro_edge/brand/` and that is all that is needed.

Since Home Assistant 2026.3, a local `brand/` directory takes priority over the brands CDN, and the
brands repository no longer accepts submissions for custom integrations. Regenerate the images with:

```bash
python scripts/make_brand_icons.py
```

## Quality scale

`custom_components/ha_avpro_edge/quality_scale.yaml` declares where the integration sits against
Home Assistant's integration quality scale. Every rule that is not `done` carries a reason, and a
test enforces that — an exemption without a reason is an unfinished item wearing a different label.

It is deliberately not mirrored into `manifest.json`: that key is for core integrations, and
hassfest objects to it on a custom one.

## Conventions worth knowing

- **Nothing under `avpro/` may import Home Assistant.** A test enforces it. That boundary is what
  lets the protocol code be developed and tested on any machine.
- **Constants carry their measurement.** A timing value with no recorded observation behind it is a
  guess wearing a constant's clothes. Where something genuinely could not be measured, it says so.
- **Absence is not "off".** A value the hardware never reported reads as unknown, never as a
  plausible default — a wrong value that looks right is worse than no value.
- **No site data, ever.** Port names are room names and source names on a real installation. Tests
  use invented values, entities are named by index, and diagnostics are built up from shapes and
  counts rather than dumped and filtered. There are tests for all three.
