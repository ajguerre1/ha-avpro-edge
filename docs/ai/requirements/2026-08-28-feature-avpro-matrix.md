---
phase: requirements
title: Requirements & Problem Understanding — AVPro Edge matrix integration
description: Clarify the problem space, gather requirements, and define success criteria
feature: avpro-matrix
status: approved
created: 2026-08-28
---

# Requirements & Problem Understanding

## Problem Statement

**What problem are we solving?**

An AVPro Edge AC-MX44-AUHD 4x4 HDMI matrix routes four sources to four displays in a home that
also runs Home Assistant. Home Assistant cannot see it at all: it cannot show which source is on
which display, cannot route, and cannot react when routing changes. Anything that depends on
"what is the gym display showing" has to be done by hand at the matrix's own web page or from a
separate control system.

There is a further constraint that shapes the whole design. The matrix offers **two control
interfaces**, and one of them is exclusive:

| Interface | Port | Concurrency |
|---|---|---|
| Telnet ASCII command set | 23 | **One client at a time** |
| CGI web interface | 80 | Unlimited |

Homes with this class of hardware usually already have a control system (Control4, Crestron, RTI)
that may want the telnet socket. Taking it is not reversible from the other system's point of
view: it simply cannot connect.

### Amended 2026-08-29 — Home Assistant is the sole controller

**This integration exists so the Control4 system can be decommissioned.** That was not stated when
these requirements were written, and it revises the premise above rather than adding to it.

The design was built to *coexist* with a control system that owned the matrix. It should instead
assume nothing else drives the device. Confirmed by the owner, and independently consistent with
the evidence: 20 connection probes found the control socket free, and both signal-probe rounds
connected immediately.

What this changes:

- **A busy or unreachable control socket is a fault, not a neighbour.** Silent fallback to HTTP
  made sense when another system had a legitimate claim; now it hides the one situation that
  should raise an alarm. Fallback stays — the matrix must remain controllable — but it raises a
  repair issue and retries quickly rather than every five minutes.
- **Live write testing is unblocked.** The owner-gate on the `T-L` scenarios existed because a
  route change could collide with a Control4 scene. It reduces to the ordinary rule that toggling
  an output's stream blanks a real display.
- **Scope grows to Control4 parity.** For the matrix to lose its dependency, this integration must
  do what that driver does. Its whole command surface is covered or planned except **Input Hot
  Plug Reset**, which is added.

What this deliberately does **not** change, because none of it was ever about Control4:

- **C1**, the one-client telnet limit, is a property of the hardware.
- The write overlay, the KEEP rule and `WRITE_SETTLE_WINDOW` bridge the matrix's own apply latency
  (**C12**, 25–404 ms), measured with no other controller involved.
- "Never re-assert a disputed write" still holds, and **G3** still matters. The matrix has a
  **front panel** — which is what key lock exists for — and its **own web page**, and both change
  routing without Home Assistant. "Another controller" is re-attributed, not removed.

## Goals & Objectives

**What do we want to achieve?**

**Primary goals**

| # | Goal |
|---|---|
| G1 | Every matrix output appears in Home Assistant as a first-class entity whose source can be read and set |
| G2 | The integration installs and updates through HACS, with no manual file copying |
| G3 | Changes made *outside* Home Assistant — front panel, web page, another control system — are reflected promptly |
| G4 | Every feature the hardware exposes is available, not only routing |
| G5 | The integration can coexist with an existing control system, or be told to get out of its way |
| G6 | *(2026-08-29)* The matrix has no remaining dependency on Control4 — everything that driver does, this does |

**Secondary goals**

- Work across the AUHD family (MX42, MX88, MX1616), not only the 4x4, by deriving port counts.
- Reach Bronze on the Home Assistant integration quality scale, and Silver where it is cheap.

**Non-goals (explicitly out of scope)**

- Changing the matrix's IP configuration. Nothing in the integration may reach `SET RIP`/`SET HIP`
  /`NetSendCmd.CGI`, including any escape hatch. A misconfigured address is a site visit.
- Factory reset (`SET RST`) for the same reason.
- Writing EDID binary data to user buffers (`SET INx EDID Uy DATAz`). Reading and selecting an
  EDID is in scope; authoring one is not.
- Talking to Control4, or reading its configuration. The goal is to make it **removable**, not to
  integrate with it: the two systems never exchange anything. Matching the *function set* of its
  AVPro driver is in scope as of 2026-08-29; matching its internals is not.

## User Stories & Use Cases

**How will users interact with the solution?**

- As **someone using a dashboard**, I want to pick which source plays on a display from the same
  card I use for everything else, so routing is not a separate app.
- As **someone using voice**, I want "put the Apple TV on the gym display" to work, so I do not
  have to be at a panel.
- As **an automation author**, I want `media_player.select_source` and a "route everything to one
  input" action, so a Movie Night scene is one call rather than four.
- As **someone with an existing control system**, I want Home Assistant to either share nicely or
  tell me plainly that it has taken the control socket, so a failure is diagnosable.
- As **an installer**, I want per-input EDID, scaler mode, audio delay and extracted-audio routing
  reachable from Home Assistant, so commissioning does not need a laptop on the matrix's web page.

**Edge cases**

- The matrix is powered off, or on a switch that reboots. Entities must go unavailable and recover
  without a Home Assistant restart, and without filling the log.
- Another controller changes a route half a second after Home Assistant does.
- A port is renamed to something containing `&`, which the matrix's own CGI cannot encode.
- Firmware that lacks an endpoint this integration expects.
- A source is asleep, so an input reports no signal.

## Success Criteria

**How will we know when we're done?**

| # | Criterion |
|---|---|
| S1 | Four outputs appear as `media_player` entities; `select_source` routes, verified against the real unit |
| S2 | A change made from the matrix's own web page appears in Home Assistant within 2 seconds |
| S3 | Installing from HACS as a custom repository works end to end, with no manual file copying |
| S4 | With nothing changing, the integration writes **zero** entity state — measured, not assumed |
| S5 | Every feature in the "Functional requirements" table below is reachable |
| S6 | hassfest, HACS validation, ruff and the test suite all pass in CI |
| S7 | No site data — address, MAC, room or source names — is present in the public repository |
| S8 | Home Assistant never holds the telnet socket when the user has told it not to |

## Functional requirements

Verified against the live unit unless marked otherwise.

| # | Requirement | Telnet | HTTP |
|---|---|---|---|
| R1 | Read and set video routing per output | yes | yes |
| R2 | Route all outputs to one input in a single command | yes | yes |
| R3 | Read and set extracted-audio routing per output | yes | yes |
| R4 | Read and set extracted-audio enable per output | yes | yes |
| R5 | Read and set extracted-audio delay (bypass, 90–630 ms) | yes | yes |
| R6 | Read and set the extracted-audio matrix bind mode | yes | yes |
| R7 | Read and set the video scaler mode per output | yes | yes |
| R8 | Read and set image enhancement per output | yes | yes |
| R9 | Read and set the test-pattern generator per output | yes | yes |
| R10 | Read and set per-input EDID | yes (index 0–32) | yes (token) |
| R11 | Read per-input detected signal | boolean | resolution string |
| R12 | **Read and set output stream on/off** | **yes** | **no — no status endpoint** |
| R13 | **Read and set input port power (TMDS)** | **yes** | **no** |
| R14 | **Read and set front-panel key lock** | **yes** | **no** |
| R15 | **Read and set LCD backlight timeout** | **yes** | **no** |
| R16 | Read model, firmware and MAC | yes | yes |
| R17 | Receive unsolicited notification of changes made elsewhere | **yes, ~300–400 ms** | no — polling only |
| R18 | Read complete device state in one request | **yes (`GET STA`)** | no — 6+ requests |
| R19 | Rename ports | no | yes (`NameSendCmd.CGI`) |
| R20 | Choose the transport, including forcing HTTP-only | — | — |
| R21 | Fall back automatically when the preferred transport is unavailable | — | — |

R12–R15, R17 and R18 are only reachable over telnet, which is why telnet is required rather than
optional. R19 is only reachable over HTTP.

## Constraints & Assumptions

**Technical constraints (all measured against the live unit unless noted)**

| # | Constraint | Evidence |
|---|---|---|
| C1 | The telnet server accepts **one client at a time** | 4 simultaneous connections: 1 succeeded, 3 timed out. Reproduced twice |
| C2 | The telnet socket is **ours**, not merely currently free | 20 connection probes over 60 s all succeeded; both 2026-08-29 signal probes connected immediately; owner confirms Control4 is effectively decommissioned. Originally recorded as "currently unoccupied" — an incidental observation. It is now a design premise |
| C3 | Telnet pushes changes from any source within ~300–400 ms | Route changed over HTTP at t=8.1 s, telnet reported it at t=8.4 s |
| C4 | Telnet also re-sends full routing every ~8–16 s | Observed during a 40 s idle hold |
| C5 | The telnet socket survives at least 40 s idle | Held open, no drop, no keepalive sent |
| C6 | Telnet responses and unsolicited pushes share one stream | `GET ADDR` returned its answer with a routing dump appended |
| C7 | The `0 = ALL` form of telnet `GET` commands does not work; per-index does | `GET OUT0 STREAM` empty, `GET OUT1 STREAM` → `OUT1 STREAM ON` |
| C8 | An HTTP request for a missing endpoint returns **200** with an HTML body, not 404 | `TMDSDivSta.CGI`, `/do?cmd=status`, `/ws/uart` |
| C9 | The HTTP server closes the connection after every response despite HTTP/1.1 | Second request on the same socket got no reply |
| C10 | HTTP `Content-Type` is `text/html;` with no charset | Raw response inspection |
| C11 | HTTP fields are `&`-separated and the separator is not escaped, so a port name containing `&` shifts every later field | Protocol analysis |
| C12 | Route changes apply in 25–46 ms typically, 404 ms worst of 20 samples | Timed across all four outputs, restored afterwards |
| C13 | Two independent firmware version fields exist: telnet reports 1.72, the web UI reports V1.41 | `H` header vs `WebSta` |
| C14 | The MAC is formatted `18.8a.6a...` on telnet and `18:8A:6A...` on HTTP | Both read |
| C15 | Home Assistant cannot be imported on Windows; HA-dependent tests are CI-only | `homeassistant.runner` imports POSIX `fcntl` |
| C16 | This Home Assistant instance drives ~50 wall panels that receive every state change | Existing installation |

**Assumptions**

| # | Assumption | If wrong |
|---|---|---|
| A1 | Nothing else needs the telnet socket *today* | Evidence C2 supports this, but it is a snapshot. R20/R21 exist precisely because it may change |
| A2 | `INFDivSta.CGI`'s four fields are per **input** | Telnet has input signal status and no output equivalent; the reported resolutions match the sources. Entities are labelled "Port N" so a wrong guess is not baked into a name |
| A3 | HTTP percent-decoding behaviour is unknown | Port rename (R19) stays gated until probed; every other command uses only unreserved characters |
| A4 | 20 latency samples do not establish a true p99 | The settle window carries ~2.5x margin over the worst observation |

## Questions & Open Items

**What do we still need to clarify?**

| Question | Resolution |
|---|---|
| Does telnet conflict with the existing control system? | **No, not currently** — C2. But the socket is exclusive (C1), so holding it locks out whatever tries next. Resolved by making the transport user-selectable (R20) with automatic fallback (R21) |
| Which transport should be preferred? | **Telnet**, because R12–R15, R17 and R18 are unreachable without it. Owner decision, 2026-08-28 |
| Should the integration ever take the socket against the user's wishes? | No — S8. An explicit HTTP-only setting must be honoured absolutely |
| Is `media_player` defensible for a device with no media? | Yes. Home Assistant has no matrix domain, and `media_player` + `source_list` is the established convention (`blackbird` in core). With telnet, `turn_on`/`turn_off` become real via R12 rather than a remembered guess |
| Should `volume_mute` map to extracted-audio enable? | No. Extracted audio is a separate de-embedded feed and does not change what the room hears. It ships as a plainly named switch. Owner decision, 2026-08-28 |
| How many entities should be enabled by default? | Only routing and signal. Everything else is install-time configuration, and C16 makes each enabled entity costly |
| Was the earlier HTTP-only decision wrong? | The *decision* was defensible on what was known; the *premise* was not tested. C2 contradicts it. Recorded here so the reversal is traceable rather than silent |
