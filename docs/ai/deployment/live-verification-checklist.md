---
phase: deployment
title: Live verification checklist — 0.2.1
description: Every assumption a single pass against the real matrix has to settle
feature: avpro-matrix
status: partly-verified
created: 2026-08-29
---

# Live verification — 0.2.1

**Run 2026-08-29. Everything that can be driven from software passes; what remains needs hands or
eyes on the hardware.**

This was written while nothing had ever run against the matrix, on the principle that a passing
test suite is a statement about a *model* of the device rather than about the device. That gap is
now largely closed, and the model held up: the entity count, the transport choice and the signal
supplement were all right on first contact.

Two figures are worth carrying forward, because they are the ones no test could have produced:

| Measurement | Result | Budget |
|---|---|---|
| Out-of-band route change → visible in Home Assistant | **0.538 s** (0.549 s on the restore) | 2 s (S2) |
| State writes per commanded change | **exactly 1** — no flicker | 1 (S4) |

The latency decomposes the way the mechanism predicts — roughly 300–400 ms for the device to
announce a change, plus the client's 250 ms quiet-gather window before it parses a block — so it
agrees with the design rather than merely being fast. Polling could not have produced it: on the
5 s profile the same change surfaces somewhere between 0 and 5 seconds later, if the routing
endpoint happens to be due.

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

## Reads — **all passed 2026-08-29**

| # | Check | Expected | Settles |
|---|---|---|---|
| R1 | Entity count | **55**: 4 media_player, 4 sensor, 4 binary_sensor, 17 switch, 22 select, 4 button | Whether capability gating behaves on real data |
| R2 | Enabled by default | 12 — 4 media_player, 4 sensor, 4 stream switches | — |
| R3 | Source picker on `media_player.*_output_1` | **Your real input names**, not `Input 1`–`Input 4` | The HTTP identity read at setup |
| R4 | Routing matches the matrix's own web page | Same four routes | The whole point |
| R5 | Diagnostics → transport | `telnet`, `pushes: true` | Telnet actually won on real hardware |
| R6 | No repair issue raised | Nothing under Settings → Repairs | The fallback path did *not* trigger |
| R7 | Signal sensors | Populated, not `unknown` and not all `off` | **The HTTP signal supplement working alongside telnet** — the newest and least-exercised code |
| R8 | `switch.*_output_N_stream` | `on` for live outputs, no "assumed" marker | T-E1: real state rather than a memory |

## Out-of-band change (T-L3) — **passed, 0.538 s**

| # | Check | Result |
|---|---|---|
| O1 | Change a route from the matrix's **web page** | ✅ **0.538 s** (0.549 s on the restore) |
| O2 | Change a route from the matrix's **front panel** | ✅ **Pushes.** Reflected within ~1 s of the press |
| O3 | Watch the log during a quiet minute | ✅ 3 minutes, ~144 signal polls, **zero** state writes |

**O2, 2026-08-29.** A front-panel route change reached Home Assistant at `16:28:36.083` against a
press reported at `16:28:37` — nominally *before* it, which is clock skew between the operator's
watch and the instance, not a measurable latency. No figure is claimed from it.

What it does settle is the mechanism, and that was the open question: a 60 s safety-net poll lands
uniformly 0–60 s *after* a change, expected value ~30 s. Landing within a second of the press, on
either side, is only consistent with the device announcing it over the control port. **The front
panel is not silent**, so a route changed at the rack appears immediately rather than up to a
minute later.

> **The first attempt measured nothing, and the tool was the reason.** `tools/ha_watch.py` compared
> `state`, and a route change does not move a `media_player`'s state — it stays `on`. The route is
> an *attribute*. The same trap caught the history query, which keyed on `last_changed`; Home
> Assistant moves that only for the state string, and an attribute-only update moves
> `last_updated`. So the change was invisible in both, and the entity looked untouched while its
> route had moved. Both now compare attributes and use `last_updated`.

O1 is the strongest single test in this list: it exercises telnet push, the parser, the report
seam, the coordinator and the entity in one motion, and it is the thing HTTP polling could never do.

## Writes (T-L1) — **all passed, matrix restored bit for bit**

| # | Check | Expected |
|---|---|---|
| W1 | Change a source from the HA card | Display follows; matrix's web page agrees |
| W2 | Restore it | Back as found |
| W3 | `ha_avpro_edge.route_all` with source 1 | All four outputs move in one command |
| W4 | Then restore the original four routes | — |
| W5 | Watch for flicker on any change | **None.** The entity must not show old → new → old |

W5 is the settle-window measurement (25–404 ms, window 1.0 s) meeting reality.

## Recovery (T-L4) — **run 2026-08-29, and it failed**

Power cut at 16:32:30, restored at 16:33:30. The most productive sixty seconds of the pass: it
found three defects, two of them serious, and none of them reachable from CI.

| # | Check | Result |
|---|---|---|
| P1 | Power the matrix off | ❌ **Failed.** Entities went `unavailable` in 15.4 s — but only four of nineteen, and the log carried a traceback, not the warning |
| P2 | Power it back on | ❌ **Failed.** Recovered at +6.3 s, then died again 80 s later and stayed dead until a manual reload |
| P3 | Repairs during the outage | ✅ **Raised and cleared itself**, tested directly rather than via a power cut |

### What broke, and why nothing caught it

**The coordinator did not catch telnet's errors at all.** It named `AvProConnectionError` and
`UnsupportedCommand`, both from the HTTP path, written when HTTP was the only wire. Telnet became
primary and nobody revisited the handler. So on the transport almost every installation uses, a
device being switched off raised straight through: no `UpdateFailed`, no once-per-outage warning,
and `Unexpected error fetching data` with a traceback — which describes a bug in this integration
rather than a matrix somebody unplugged. `log-when-unavailable` was marked **done** and its code
had never executed. Fixed by a `TransportError` base every wire's failures derive from, so the
coordinator catches the contract instead of naming wires.

**Fifteen of nineteen entities lied about being available.** `_state_snapshot` omitted
`available`, so the change gate suppressed the write that says the device is gone. Only
`media_player` reported the outage, because it happened to include `available` in its own
override. Everything else went on displaying its last reading, indefinitely, looking healthy.

**The telnet client never reconnected.** `BACKOFF` and `backoff_delay()` shipped with it and
nothing ever called them — a ladder nothing climbed. The read loop returned on EOF, silence or
error and no path restarted it, so losing a session was permanent. Worse, `connected` described
only our end of the socket: a device losing power sends no FIN, so the writer stayed open and the
transport reported a healthy connection to an unplugged matrix. It never fell back to HTTP either,
so `async_watch_for_telnet` — which starts only after a fallback — was not running. Nothing was
watching, because everything believed it was still connected.

> **This is the shape that keeps recurring.** Every one of the three lives on a path that only
> runs once something has already gone wrong, which is the class nothing exercises until the day
> it matters — the same class as `HttpTransport` having no `connected` attribute for seven
> commits. A green suite says the model agrees with itself.

### Re-run on 0.3.0 — P1 and P2 pass

Power cut 16:57:00, restored 16:57:30. Unavailable at **16:57:13.7** (13.7 s), available again at
**16:57:40.6** (10.6 s), and it **held** — the previous run had died again at +80 s. One
`WARNING … is unreachable` and **zero** tracebacks, against a traceback and 4-of-19 before.

Six of seven watched entities went unavailable together. The seventh was the signal binary sensor,
still reporting a live picture on a port belonging to an unplugged matrix — because the override
added that morning to fix its *frozen* gate returned `is_on` alone, and overriding the gate opts
out of the base class where `available` lives. Fixing one defect preserved the other in the one
entity that had an override. Fixed in 0.3.1 and guarded behaviourally: one entity per platform,
driven through a real outage, all must go unavailable.

> The per-platform sweep is what caught it. Checking a `media_player` alone passes on **both**
> broken versions, because that platform included `available` in its own override from the start.
> It was the only entity telling the truth and it looked exactly like proof that everything was.

The same trace carried the first hardware confirmation of the `NO SIGNAL` work: the sensor read
`NO SIGNAL` while the matrix was still booting and the binary sensor correctly went `off`, then
both recovered when the source re-synced.

### P3, tested directly — the mechanism, not the weather

A power cut cannot exercise this. The repair issue is raised on **fallback to HTTP**, and with the
reconnect working the transport stays on telnet throughout an outage, so there is nothing to
raise. Both runs left P3 untouched for that reason.

So it was driven at the mechanism instead. The matrix allows one telnet client, which makes the
socket a thing that can be taken: disable the entry so Home Assistant releases it, hold it from
another machine, re-enable. Home Assistant then wants telnet and cannot have it — which is exactly
the fault the repair issue describes, with **no video impact at all**, since routing continues over
HTTP and no display is touched.

| Time | Observed |
|---|---|
| 17:08:49 | Socket held elsewhere; entry re-enabled |
| 17:09:09 | Fell back to HTTP, **7 entities unavailable** — precisely the telnet-only ones |
| 17:09:09 | **Repair issue raised**, severity warning, not fixable, key `telnet_unavailable` |
| 17:09:59 | Socket released 50 s earlier → **issue cleared itself**, 19 entities back |

Recovery ~70 s from release: the 60 s re-check plus a reload. Every branch is now observed —
raised on an unwanted fallback, not raised when HTTP is chosen deliberately, and cleared without
anyone touching it.

Worth recording separately: a second telnet connection while Home Assistant holds the socket is
**refused** (`ECONNREFUSED`), not timed out. The design anticipated a held slot presenting as a
timeout, and on this unit it presents as a refusal — which is also why every reload loses telnet
for ~60 s, and why the recovery watcher earns its place.

## Still outstanding — these need hands or eyes on the hardware

| # | Check | Why it needs you |
|---|---|---|
| **T-L2** | `switch.*_output_1_stream` off, then on | **Blanks a real display.** Do it on a screen you can see |
| **T-L6** | `select.*_front_panel_backlight` → each of the four | **The labels are inferred.** The count is measured (T0–T3 accepted, T4/T5 refused) but *Always ON / 15s / 30s / 60s* comes from AVPro's own driver listing them in that order. Only someone watching the front panel can confirm `T0` really is always-on |
| **T-L7** | `button.*_input_N_hot_plug_reset` on an input with a fussy source | **`HOT_PLUG_RESET_HOLD` is the one unmeasured constant here.** 1.0 s is conventional, not observed. If the source does not renegotiate, it is too short |
| **T-L8** | **Unplug one source**, then read the signal sensors and the matrix's own web page | **Decides whether the signal binary sensor can ever say *Disconnected*.** A blank field currently decodes to the same `None` as a port never read, so `is_on` returns `True` or `None` and never `False`. Cheapest test on this list — pull one HDMI cable and look |

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

## What the run changed

- **T-L1, T-L3 and T-L5 are ticked**, each with its observation recorded in `VERIFIED_LIVE` in
  `tests/test_traceability.py`. The live tier used to sit outside that mechanism, so running a
  scenario by hand left it looking undone for ever; a ticked box must now be backed by a test *or*
  by a dated observation, and the build fails on a claim with neither.
- **O2, P1–P3, T-L2, T-L6 and T-L7 remain.** Each needs someone at the matrix: pressing a front
  panel button, cutting power, watching a screen go dark, or seeing a backlight time out.
- **Nothing surprised us**, so no new fake fault was needed — the first run of this checklist that
  could have said otherwise.

## Still to do after those

- Update the deployment tracking with the remaining observations
- Anything that surprises us becomes a fake fault, so it cannot surprise us twice
