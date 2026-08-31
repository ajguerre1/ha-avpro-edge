---
phase: deployment
title: Live verification checklist — complete through 0.3.1
description: Every assumption a single pass against the real matrix has to settle
feature: avpro-matrix
status: verified
created: 2026-08-29
---

# Live verification — every item run

**Complete as of 2026-08-31, across 0.2.1, 0.3.0 and 0.3.1.** Every check on this page has been
performed against the hardware, including the five that needed a person standing at the matrix.

This was written while nothing had ever run against the matrix, on the principle that a passing
test suite is a statement about a *model* of the device rather than about the device. The first
pass suggested the model was sound — the entity count, the transport choice and the signal
supplement were all right on first contact.

**The second pass found five defects, and the suite was green through every one of them.** The
model was right about what the integration does and wrong about what the device does, which is
exactly the gap a test suite cannot see:

| Defect | Why CI could not reach it |
|---|---|
| `bool("NO SIGNAL")` is `True` — two entities and the diagnostics dump reported a dark port as carrying a picture | The fake modelled darkness as `""`, which is falsy. The suite agreed with a device that does not exist |
| The coordinator caught only the HTTP wire's exception types | Telnet became primary and the handler was never revisited. The error path runs only when something has already gone wrong |
| The change gate omitted `available`, so 15 of 19 entities reported through an outage | Nothing had ever made the coordinator fail while entities were watched |
| The telnet client never reconnected — `BACKOFF` and `backoff_delay()` were never called by anything | A ladder nothing climbed looks identical to a ladder nobody needed |
| The signal binary sensor's change gate never fired at all | A gate that never fires is indistinguishable from a value that never changes |

Three of the five live on paths that run only after something else has failed — the same class as
`HttpTransport` having no `connected` attribute for seven commits.

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

**O2, 2026-08-31.** A front-panel route change reached Home Assistant at `16:28:36.083` against a
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

## Recovery (T-L4) — **run 2026-08-31, and it failed**

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

## The items that needed a person — all run

| # | What was settled | Result |
|---|---|---|
| **T-L2** | Stream toggle, on a display someone could see | Went black and came back. Held `off` for **12.5 s**, eight times the overlay window, so it was device truth |
| **T-L6** | Whether `T0` really is *Always ON* | `T1` 15 s, `T3` 60 s, `T0` still lit past 90 s. **Confirmed**, `T2` = 30 s by elimination |
| **T-L7** | `HOT_PLUG_RESET_HOLD`, the last unmeasured constant | **1.002 s** against a nominal 1.0. Display blacked and recovered, which is what settles it |
| **T-L8** | What the matrix reports for an unplugged input | The literal string **`NO SIGNAL`**. It blanks nothing |
| **T-L4 / P1–P3** | Power cycle | Failed, found three defects, fixed, re-run passes |

## Assumptions, and what became of them

Listed plainly, because this is what a pass against hardware is for. Four of the five moved.

1. **The signal supplement's cadence with both wires active.** ⚠️ **Still open, and now with
   evidence of a real hazard.** Port 1's signal field has twice been observed carrying telnet
   vocabulary — `3840X2160P@60IN1 VID F` for at least twelve hours, and `3840X2160P@60HZ YUVIN1`
   after a power cycle. Telnet text in a field read over HTTP, on the same port both times, which
   looks like the device sharing a buffer between its CGI and control subsystems. The clean test is
   the `http` escape hatch: run with port 23 untouched and see whether it recurs.
2. **The LCD label ordering.** ✅ Measured (T-L6). Was a count with an inferred meaning.
3. **The hot-plug hold time.** ✅ Measured at 1.002 s (T-L7).
4. **`route_all` on telnet.** ✅ The per-output fallback is what runs, as expected.
5. **Port renaming.** Still not implemented, still gated on probe P11. Unchanged, and deliberate.

## What the hardware changed that no test could

- **Probe P10 is settled**: `INFSta` reports **inputs**. Unplugging a source drove its field to
  `NO SIGNAL`; muting the corresponding output left it unchanged. Both results would have been
  reversed for outputs. The planning docs carried this as unanswerable from the CGI interface
  alone, which was true — reading the interface was never going to settle it.
- **The telnet port refuses connections for ~60 s after a disconnect** — `ECONNREFUSED`, not a
  timeout. The design anticipated a held slot presenting as a timeout. This is why every reload
  loses telnet for about a minute, and why the recovery watcher is load-bearing rather than
  belt-and-braces.
- **A `send_command` reply is an echo, not a read-back.** An EDID write returned `ok` with the new
  value visible, never applied it, and blanked a television on the way past — leaving no trace in
  the device's state, the signal readings, or Home Assistant. Documented in the README as a hazard.
- **The `never-apply` fake fault happened for real**, in that same EDID write. The overlay held the
  commanded value, took device truth when the read disagreed, and did not re-send it.

## Still open

- **The port 1 corruption** (assumption 1 above). Reproduced twice, cause unconfirmed.
- **`3840X2160P@62HZ` on port 3.** Not a standard rate and not an Apple TV output mode; survives an
  EDID change and repeated renegotiation, with all four inputs now on the same EDID. Owner's
  reading is a firmware reporting quirk, and the evidence supports it. The unit is **discontinued**
  with no entry at all on AVPro's firmware index, so there is no fix coming — the successor is
  different hardware and must not be cross-flashed.
- Anything that surprises us next becomes a fake fault, so it cannot surprise us twice.
