---
phase: deployment
title: Live verification checklist — 0.2.0
description: Every assumption a single pass against the real matrix has to settle
feature: avpro-matrix
status: ready
created: 2026-08-29
---

# Live verification — 0.2.0

Everything below has been proven against `tools/fake_avpro.py` and nothing below has ever run
against the matrix. **495 tests are 495 statements about a model of the device, not about the
device.** The fidelity pass showed the model matches the real protocol structurally — same line
shapes, same field counts, same absent endpoints — but that says nothing about whether 49 entities
come up correctly, whether pushes reach the UI, or whether it loads at all.

The plan is one pass rather than several, so this exists to make the pass count. Every item is
something a green CI run cannot tell us.

## Install

1. HACS → three-dot menu → **Custom repositories** → add `https://github.com/ajguerre1/ha-avpro-edge`, category **Integration**
2. Install **AVPro Edge**, then restart Home Assistant
3. **Settings → Devices & Services → Add Integration → AVPro Edge**, and enter the matrix's
   address on your LAN

| # | Check | Expected | Why CI can't say |
|---|---|---|---|
| I1 | HACS accepts the repository | Appears and installs | `hacs.json`, brand assets and repo layout are only validated as metadata |
| I2 | The integration loads after restart | No error in the log | Nothing has ever imported this under a real HA |
| I3 | Config flow accepts the host | Title reads `AC-MX44-AUHD` | Real identity body, real firmware string |
| I4 | Device page shows model and firmware | `AC-MX44-AUHD`, `V1.41` | — |
| I5 | The AVPro Edge icon appears in HACS and on the integration card | The AV mark, not a placeholder | Served from `brand/` via the local proxy; the CDN has no entry for this domain and does not need one |

## Reads — do these before writing anything

| # | Check | Expected | Settles |
|---|---|---|---|
| R1 | Entity count | **49**: 4 media_player, 4 sensor, 4 binary_sensor, 17 switch, 22 select, 4 button | Whether capability gating behaves on real data |
| R2 | Enabled by default | 12 — 4 media_player, 4 sensor, 4 stream switches | — |
| R3 | Source picker on `media_player.*_output_1` | **Your real input names**, not `Input 1`–`Input 4` | The HTTP identity read at setup |
| R4 | Routing matches the matrix's own web page | Same four routes | The whole point |
| R5 | Diagnostics → transport | `telnet`, `pushes: true` | Telnet actually won on real hardware |
| R6 | No repair issue raised | Nothing under Settings → Repairs | The fallback path did *not* trigger |
| R7 | Signal sensors | Populated, not `unknown` and not all `off` | **The HTTP signal supplement working alongside telnet** — the newest and least-exercised code |
| R8 | `switch.*_output_N_stream` | `on` for live outputs, no "assumed" marker | T-E1: real state rather than a memory |

## Out-of-band change (T-L3)

| # | Check | Expected |
|---|---|---|
| O1 | Change a route from the matrix's **web page** | HA reflects it in **under 2 s** |
| O2 | Change a route from the matrix's **front panel** | Same |
| O3 | Watch the log during a quiet minute | No repeated warnings, no poll spam |

O1 is the strongest single test in this list: it exercises telnet push, the parser, the report
seam, the coordinator and the entity in one motion, and it is the thing HTTP polling could never do.

## Writes (T-L1)

| # | Check | Expected |
|---|---|---|
| W1 | Change a source from the HA card | Display follows; matrix's web page agrees |
| W2 | Restore it | Back as found |
| W3 | `ha_avpro_edge.route_all` with source 1 | All four outputs move in one command |
| W4 | Then restore the original four routes | — |
| W5 | Watch for flicker on any change | **None.** The entity must not show old → new → old |

W5 is the settle-window measurement (25–404 ms, window 1.0 s) meeting reality.

## Recovery (T-L4)

| # | Check | Expected |
|---|---|---|
| P1 | Power the matrix off | Entities go `unavailable`, one warning in the log — not one per 5 s |
| P2 | Power it back on | Recovers with **no Home Assistant restart** |
| P3 | Check Repairs during the outage | A telnet repair issue may appear; it must clear itself on recovery |

## Owner present — these are visible or need eyes

| # | Check | Why it needs you |
|---|---|---|
| **T-L2** | `switch.*_output_1_stream` off, then on | **Blanks a real display.** Do it on a screen you can see |
| **T-L6** | `select.*_front_panel_backlight` → each of the four | **The labels are inferred.** The count is measured (T0–T3 accepted, T4/T5 refused) but *Always ON / 15s / 30s / 60s* comes from AVPro's own driver listing them in that order. Only someone watching the front panel can confirm `T0` really is always-on |
| **T-L7** | `button.*_input_N_hot_plug_reset` on an input with a fussy source | **`HOT_PLUG_RESET_HOLD` is the one unmeasured constant here.** 1.0 s is conventional, not observed. If the source does not renegotiate, it is too short |

## Known-unvalidated assumptions

Listed plainly, because these are what a single pass is for:

1. **The signal supplement's cadence.** Signal polls over HTTP on the 5 s profile while telnet
   carries everything else. Load on the device has never been observed with both wires active.
2. **The LCD label ordering** — T-L6 above. Measured count, inferred meaning.
3. **The hot-plug hold time** — T-L7 above. A property of the source, unobservable from here.
4. **`route_all` on telnet.** The `x=0 means ALL` form is documented in the help but the matching
   `GET` returned nothing on this firmware, so the coordinator falls back to one command per
   output. If W3 shows four telnet commands rather than one, that is expected and correct.
5. **Port renaming.** Not implemented; still gated on probe P11 (percent-encoding is the riskiest
   write class, and the endpoint writes all eight names at once).

## After the pass

- Tick the `T-L` boxes in `docs/ai/testing/` with what was observed, and drop them from `DEFERRED`
  in `tests/test_traceability.py` — the build will complain until those two agree
- Add the `AV-01` row to the private repo's `docs/ai/planning/backlog.md`
- Anything that surprised us becomes a fake fault, so it can never surprise us twice
