---
phase: testing
title: Testing Strategy — AVPro Edge matrix integration
description: Define how the integration is proven, offline and against hardware
feature: avpro-matrix
status: in-progress
created: 2026-08-28
---

# Testing Strategy

## Test Coverage Goals

Three tiers, split by what they need rather than by what they cover.

| Tier | Where | Needs | Runs |
|---|---|---|---|
| Offline | `tests/` | Nothing but Python | Everywhere, including Windows |
| HA-layer | `tests/ha/` | `pytest-homeassistant-custom-component` | CI only — Home Assistant cannot be imported on Windows |
| Live | `scripts/` + a human | The real matrix | On request, with someone present |

Target: every requirement `R1`–`R21` and every constraint `C1`–`C16` either has a test or a stated
reason it cannot have one.

## Unit Tests

### T-R — `DeviceReport` (M-B)

- [x] T-R1 A partial report merges without clearing fields it does not mention
- [x] T-R2 A complete report is distinguishable from a partial one, and **even a complete one
      does not clear a field it omits**
- [x] T-R3 Merging is associative — the order two pushes arrive in cannot change the result

> T-R2 was originally written as "only a complete one may clear a field". That was wrong, and the
> design moved without the doc following. No report may clear anything: telnet's `GET STA` knows
> nothing of the port names and the HTTP census knows nothing of the output stream state, so a
> census that cleared what it omitted would have each transport erase the other's contribution on
> every cycle. `complete` means only "enough has been read to create entities from".

### T-S — `MatrixState.apply` (M-B)

- [x] T-S1 Applying the same report twice yields an equal state (the `always_update=False` premise)
- [x] T-S2 A changed value makes the state unequal
- [x] T-S3 An unknown state key is ignored rather than raising
- [x] T-S4 A value of `None` means "not reported", never "off" or "input 0"

### T-N — Telnet grammar and client (M-C)

- [x] T-N1 Every line form in a real `GET STA` dump parses: `OUT1 VS IN1`, `OUT1 VIDEO 1`,
      `OUT1 EXADL PH0`, `OUT1 EXA DIS`, `EXAMX MODE2`, `OUT1 AS IN2`, `OUT1 IMAGE ENH 0`,
      `OUT1 STREAM ON`, `OUT1 SGM DIS`, `IN1 TMDS ON`, `IN1 EDID 30`, `KEY LOCK OFF`, `LCD ON T2`,
      `ADDR 00`, `MAC ...`
- [x] T-N2 An unrecognised line is dropped, not guessed at
- [x] T-N3 EDID index `30` normalises to the same option key as HTTP's `EDIDU1` — the two
      vocabularies must agree
- [x] T-N4 `EXA DIS`/`EXA EN` and `SGM DIS`/`SGM EN` map to the same booleans as HTTP's `ON`/`OFF`
- [x] T-N5 A partial dump produces a partial report, never a complete one
- [x] T-N6 A garbled or truncated line cannot corrupt neighbouring values
- [x] T-N7 The client sends a trailing return; the device requires it
- [x] T-N8 `GET STA` yields a complete report covering every state key the transport claims
- [x] T-N9 An unsolicited push updates only the keys it names (C3)
- [x] T-N10 A push arriving *during* a command response is not mistaken for the response (C6)
- [x] T-N11 Disconnect drops the pending overlay — a replayed optimistic value is a stale claim
- [x] T-N12 Reconnect backoff uses a per-client RNG, never `random.seed()`
- [x] T-N13 The fake telnet device reproduces push-on-change and the one-client limit

### T-T — Transport interface (M-B)

- [x] T-T1 **Every** transport satisfies `Transport`, discovered rather than listed; none leaks a
      transport-specific type upward

## Integration Tests

### T-D — Selection and fallback (M-D)

- [x] T-D1 The transport option round-trips through the options flow
- [x] T-D2 Under `auto` with telnet reachable, telnet is selected and `pushes` is True
- [x] T-D3 Under `auto` with the telnet socket busy, HTTP is used and the fallback is logged **once**
- [x] T-D4 Under `telnet` with telnet unavailable, setup raises `ConfigEntryNotReady` rather than
      silently degrading
- [x] T-D5 Under `http`, telnet-only entities are not created
- [x] T-D6 A pushing transport does not poll on the 5 s tick, but does run the 60 s safety net

### T-X — Transport discipline

**Telnet is primary. Always speak telnet unless you don't need to.**

- [x] T-X1 Under the `http` setting, **nothing ever connects to port 23** — asserted by the fake
      device's connection counter. The escape hatch (S8) still holds absolutely
- [x] T-X2 While telnet is connected, **no HTTP request is issued for anything telnet supports**.
      Routing, audio, scaler, EDID, stream, key lock and the periodic safety-net read all go over
      telnet. Asserted against a named allowlist on the fake — `WEBDivSta`, `NETDivSta` (identity,
      once) and `INFDivSta` (signal) — so anything else appearing is a failure
- [x] T-X3 A port rename — the one thing telnet cannot do — *does* reach HTTP, even while telnet
      is connected, and is the only thing that does
- [x] T-X4 Static scan: no module opens an outbound socket outside the telnet client

> T-X2 is the assertion that encodes the rule. The old guard forbade telnet outright, which was
> only ever a proxy for "do not take a socket someone else needs". Now that telnet is the primary
> channel, the failure worth guarding against is the opposite one: hedging by running HTTP
> alongside a healthy telnet session, which doubles device load and creates two sources of truth.

### T-E — Features telnet unlocks (M-E)

- [x] T-E1 `switch.output_N_stream` reads back after a restart — it is real state, not assumed
- [x] T-E2 `switch.input_N_power` reflects `IN1 TMDS ON`
- [x] T-E3 Key lock and LCD timeout read and write
- [x] T-E4 `media_player` advertises `TURN_ON`/`TURN_OFF` on telnet and **not** on HTTP
- [x] T-E5 Signal is supplemented over HTTP when telnet is active — telnet cannot read it at all
- [x] T-E6 `manifest.json` declares `local_push`

### T-G — Control-system parity (M-G)

The functions the vendor's own control-system driver has that this did not. Scoped against what
that driver *does*, which is the only definition of "parity" that matters — the matrix loses its
dependency when nothing is left that only the driver can do.

- [x] T-G1 Input hot plug reset drops the input's TMDS and restores it, through the same overlay
      every other write uses
- [x] T-G2 `route_all` is registered as an action at setup, and routes every output
- [x] T-G3 `send_command` returns the device's own reply, so `NO SUPPORT` is visible rather than
      indistinguishable from success
- [x] T-G4 `send_command` **cannot** reach the network or factory-reset endpoints — they are
      absent from the mapping, not merely rejected by validation

### T-W — Write semantics (already passing; must stay passing)

- [x] T-W1 One command produces exactly one state write
- [x] T-W2 The confirming poll or push produces none
- [x] T-W3 A stale reading inside the settle window does not revert the entity
- [x] T-W4 An ignored write expires to device truth and is **never** re-sent
- [x] T-W5 A quiet cycle writes no state at all (S4)

## End-to-End Tests

### T-L — Live, needs the hardware

- [x] T-L1 Route a real output over telnet; observe the change and restore it
      *2026-08-29: output 1 to input 3 and back from Home Assistant. The matrix agreed both
      times, exactly one state change per write, routing restored bit for bit.*
- [x] T-L2 Toggle an output's `STREAM` and confirm it reads back — the capability HTTP could not offer
      *2026-08-29: the display went black and came back. The switch held `off` for **12.5 s**,
      eight times the 1.5 s overlay settle window, so what it reported was the device rather than
      a remembered write — which is the entire argument for telnet being primary, since the CGI
      interface can write this and cannot read it. `media_player` reached `OFF`, the one branch
      only stream can produce. The input's signal was unaffected throughout.*
- [x] T-L3 Change a route from the matrix's web page; confirm Home Assistant reflects it in < 2 s (S2)
      *2026-08-29: driven over the CGI interface so Home Assistant learned of it only by push.
      **0.538 s**, and 0.549 s on the restore. The two agreeing to 11 ms says the path is
      consistent rather than lucky, and the figure decomposes as the mechanism predicts:
      ~300-400 ms for the device to announce, plus the client's 250 ms quiet-gather window.*
- [x] T-L4 Pull power to the matrix; confirm entities go unavailable and recover with no restart
      *2026-08-29: **the first run failed and was the most productive minute of the pass.** Three
      defects, all on paths that only run once something has already gone wrong: the coordinator
      caught only the HTTP wire's exception types so a dead device logged a traceback instead of
      the once-per-outage warning; the change gate omitted `available`, so 15 of 19 entities went
      on reporting their last reading through the whole outage; and the telnet client never
      reconnected, because `BACKOFF` and `backoff_delay()` shipped with it and nothing ever called
      them.*
      *Re-run on 0.3.0: unavailable at **13.7 s**, recovered at **10.6 s** and held — the previous
      run had died again at +80 s. One warning, zero tracebacks. A seventh entity still lied and
      is fixed in 0.3.1.*
      *P3 could not be reached by cutting power at all — the repair issue is raised on fallback,
      and with the reconnect working the transport stays on telnet throughout. Driven at the
      mechanism instead, by holding the single control socket from another machine: issue raised
      on the fallback, cleared itself 70 s after release, no video touched.*
- [x] T-L5 Install from HACS as a custom repository, end to end (S3)
      *2026-08-29: config entry loaded, 55 entities registered (4 media_player, 4 sensor,
      4 binary_sensor, 17 switch, 22 select, 4 button), 12 enabled, device identified as
      AC-MX44-AUHD V1.41. No repair issue raised, so the fallback path did not trigger.*
- [x] T-L7 Press a hot plug reset and confirm the source renegotiates
      *2026-08-29: TMDS off at +36 ms, on again **1002 ms** later — the first measurement of
      `HOT_PLUG_RESET_HOLD`, which was 1.0 s by convention and is now 1.0 s by observation. The
      display went black and recovered, and that is the part that settles it: the state trace on
      its own could not distinguish the matrix acting from the pending overlay publishing, since
      both produce an identical `off`/`on` pair with the same final value. Nothing on either wire
      reports whether the source noticed — a person watching the screen does.*
- [x] T-L6 Someone watches the front panel and confirms `T0` really is *Always ON*
      *2026-08-29: `T1` went dark at ~15 s and `T3` at ~60 s, which pins both ends of the range;
      `T0` was still lit well past 90 s, longer than the longest timeout that exists, so it is
      not a timeout at all. **Always ON confirmed**, and `T2` = 30 s follows by elimination — four
      values, four labels, both ends fixed, and the middle has nowhere else to go. The list order
      came from AVPro's own driver and is now measured rather than trusted.*
- [x] T-L8 Unplug a source and see what the matrix reports for that input
      *2026-08-29: **`NO SIGNAL`**, as a literal string — at 14:25:12 ET on the unplug, and the
      format string back at 14:26:34 on the replug. The question was framed as whether a blank
      field meant "nothing connected" or "not measured"; **both were wrong**, because the device
      blanks nothing. It says so in words. Not guessing was the whole value of filing it: either
      answer would have changed `_decode_info` and fixed nothing.*
      *The real defect was one line further on. `bool("NO SIGNAL")` is `True`, so every consumer
      of a signal field — the binary sensor, `media_player.state`, and the diagnostics dump —
      reported a port with the cable out as **carrying a picture**. An output held `on` for 82
      seconds while its source sat unplugged. The suite passed throughout because the fake
      modelled a dark port as `""`, which is falsy and is not what this hardware sends. Same
      failure as the TMDS tab: agreeing with a convenient model rather than the device.*

## Test Data

Fixtures are **invented**, never captured. A real `GET STA` dump contains the site's port names
and the unit's address and MAC; a real status body is site data. Fixtures use `OutA`/`SrcA`,
`10.0.0.1`, `AA:BB:CC:DD:EE:FF`.

The fake device serves both transports from one in-memory model, so a telnet write is visible to
an HTTP read and vice versa — which is what makes the fallback path testable at all.

## Manual Testing

The live tier is the only place several constraints can be confirmed, because they are properties
of the hardware rather than of the code: C1 (one client), C2 (socket free), C3 (push latency),
C12 (apply latency). Each is re-checkable by the probes already used to establish it.

## Triaging a red run

A red CI run is a **set** of failures, not one problem. Fixing the loudest can silence the rest
without addressing them, and a failure that stops appearing looks identical to one that was fixed.

That is not hypothetical here. Run `33229748919` reported several telnet-selection failures *and*
`AttributeError: 'HttpTransport' object has no attribute 'connected'`. The telnet failures had a
single cause — the control port was not plumbed through — and fixing it made the `AttributeError`
disappear too, because the affected test then received a telnet transport instead. It was a
different bug wearing the same run, and it survived for another seven commits: `connected` was on
neither the `Transport` protocol nor `HttpTransport`, so the HTTP fallback path would have raised
at runtime. The path that only runs when something has already gone wrong.

So:

1. **Enumerate every distinct failure before fixing any.** `gh run view --log-failed`, and write
   the list down. The count is the thing that matters.
2. **Close each one with a change that explains it.** "It stopped appearing" is not a diagnosis.
3. **After the fix, account for the whole list.** If eight failures were observed and one change
   resolved all eight, say which mechanism made that true. If it cannot be stated, it is not known.

The same asymmetry that governs polling governs this: a failure that vanishes on its own has not
been understood, and an unexplained recovery is a finding, not a relief.

## Bug Tracking

Findings that change behaviour go in the planning doc's risk table and, if they outlive the
feature, in the deployment plan.

---

# Execution Results

Recorded per run once M-B begins. The offline and HA-layer suites currently stand at **298 tests
passing in CI**, covering the HTTP transport delivered in M-A; the scenarios above are additions
to that baseline, not a replacement for it.
