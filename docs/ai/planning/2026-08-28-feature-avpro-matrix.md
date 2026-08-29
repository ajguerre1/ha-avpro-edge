---
phase: planning
title: Project Planning & Task Breakdown — AVPro Edge matrix integration
description: Break the work into ordered, verifiable tasks
feature: avpro-matrix
status: in-progress
created: 2026-08-28
---

# Project Planning & Task Breakdown

Requirements: `docs/ai/requirements/2026-08-28-feature-avpro-matrix.md`
Design: `docs/ai/design/2026-08-28-feature-avpro-matrix.md`
Testing: `docs/ai/testing/2026-08-28-feature-avpro-matrix.md`

## Milestones

- [x] **M-A — HTTP transport working end to end.** Delivered before this lifecycle was run; see
      "Work completed ahead of the docs" below.
- [x] **M-B — Transport seam.** `DeviceReport` introduced, folds collapsed, HTTP moved behind the
      `Transport` interface. No behaviour change, no new features.
- [x] **M-C — Telnet transport.** Grammar, persistent client, push dispatch, fake device.
- [x] **M-D — Selection and fallback.** Auto/telnet/http option, capability-driven entities.
- [x] **M-E — The features telnet unlocks.** Stream, input power, key lock, LCD timeout, and real
      `turn_on`/`turn_off`.
- [x] **M-G — Control4 parity.** Input hot plug reset and `send_command`, so the driver can go.
      Added 2026-08-29 with goal G6; it did not exist when this plan was written.
- [ ] **M-F — Verified on the live unit** and released. *(0.2.1 is released; nothing is verified
      on hardware, which is the whole of what remains.)*

## Task Breakdown

Legend: **[A]** agent action · **[U]** user action (live-device confirmation).

### M-B — Transport seam *(no new behaviour; the safest possible first move)*

| # | Task | Touches | Depends on | Validates |
|---|---|---|---|---|
| B1 | **[A]** Add `avpro/report.py`: `DeviceReport`, merge semantics, `complete` flag | new | — | T-R1, T-R2 |
| B2 | **[A]** Collapse the seven `fold_*` functions into `state.apply(state, report)` | `state.py` | B1 | T-S1..T-S4 |
| B3 | **[A]** Add `avpro/transport.py`: the `Transport` protocol and `TransportCapabilities` | new | B1 | T-T1 |
| B4 | **[A]** Move HTTP into `avpro/http/`, make its client satisfy `Transport`, emit `DeviceReport` | `protocol.py`, `client.py` | B2, B3 | existing suite must stay green |

> B4 is the checkpoint that proves the seam. If the entire existing test suite passes with the
> HTTP client now emitting reports, the abstraction is not leaking.

### M-C — Telnet transport

| # | Task | Touches | Depends on | Validates |
|---|---|---|---|---|
| C1 | **[A]** `avpro/telnet/protocol.py` — pure line grammar, `OUT1 VS IN2` → `("video_route_1", 2)`, every line form from `GET STA` | new | B1 | T-N1..T-N6 |
| C2 | **[A]** `avpro/telnet/client.py` — persistent socket, line framing, `GET STA` census, push dispatch, reconnect with per-entry jitter | new | C1, B3 | T-N7..T-N12 |
| C3 | **[A]** Extend `tools/fake_avpro.py` with a telnet server: real grammar, push on change, and faults (`socket-busy`, `drops-idle`, `push-only`, `garbled-line`) | `fake_avpro.py` | C1 | T-N13 |
| C4 | **[A]** Replace the telnet tripwire with a *transport-discipline* guard: telnet carries everything it can, HTTP only where telnet cannot go | `test_no_telnet.py` → `test_transport_discipline.py` | C2 | T-X1, T-X2, T-X3 |

> C4 inverts an existing guard rather than weakening it. The old rule was "never speak telnet",
> which was only ever a proxy for "do not take a socket someone else needs". The real rule, per
> the owner: **telnet is primary — always speak telnet unless you don't need to.** So the guard
> now asserts three things instead of one:
>
> - nothing connects to port 23 under the `http` setting (the escape hatch still holds);
> - **no HTTP request is issued for anything telnet supports while telnet is connected** — no
>   hedging, no dual-polling, one source of truth;
> - HTTP is reachable for the operations telnet genuinely lacks (port rename).
>
> The middle assertion is the new one, and it is the one that encodes the owner's rule.

### M-D — Selection and fallback

| # | Task | Touches | Depends on | Validates |
|---|---|---|---|---|
| D1 | **[A]** `CONF_TRANSPORT` option (`auto`/`telnet`/`http`), strings, translations | `const.py`, `config_flow.py`, `strings.json` | — | T-D1 |
| D2 | **[A]** Coordinator selects a transport, falls back on failure, retries telnet every 5 min | `coordinator.py` | C2, D1 | T-D2..T-D4 |
| D3 | **[A]** Drive entity creation from `TransportCapabilities`, extending the existing absent-endpoint mechanism | platforms | D2 | T-D5 |
| D4 | **[A]** Poll only when the transport does not push; keep a 60 s `GET STA` safety net when it does | `coordinator.py` | D2 | T-D6 |

### M-E — Features telnet unlocks

| # | Task | Touches | Depends on | Validates |
|---|---|---|---|---|
| E1 | **[A]** `switch.output_N_stream` — now readable, so no `assumed_state` | `switch.py` | D3 | T-E1 |
| E2 | **[A]** `switch.input_N_power` (TMDS) | `switch.py` | D3 | T-E2 |
| E3 | **[A]** `switch.key_lock`, `select.lcd_timeout` | `switch.py`, `select.py` | D3 | T-E3 |
| E4 | **[A]** `media_player` gains `TURN_ON`/`TURN_OFF` from `stream`, at runtime from capabilities | `media_player.py` | E1 | T-E4 |
| E5 | **[A]** `binary_sensor.input_N_signal` from telnet's boolean, preferred over string-emptiness | `binary_sensor.py` | D3 | T-E5 |
| E6 | **[A]** `iot_class` → `local_push`; README documents the fallback degrading to polling | `manifest.json`, `README.md` | D4 | T-E6 |

### M-F — Verification and release

| # | Task | Touches | Depends on | Validates |
|---|---|---|---|---|
| F1 | **[A]** Full offline + CI suite green | — | E6 | all |
| F2 | **[U]** Confirm a live-write window, then verify routing, stream toggle and push latency on the real unit | — | F1 | T-L1..T-L3 |
| F3 | **[A]** Public-repo audit by enumeration; version bump; release tag | — | F2 | S7 |
| F4 | **[A]** `AV-01` backlog row in the private Home Assistant repo, linking here | private repo | F3 | — |

## Work completed ahead of the docs

Recorded for traceability rather than hidden. M-A was built before this lifecycle ran, which is
why the requirements and design docs are dated after the code. What exists and is CI-green:

| Delivered | Evidence |
|---|---|
| HTTP CGI transport, 8 client modules, no HA imports | 251 offline tests |
| Config flow, coordinator, entity base, diagnostics | `tests/ha/` |
| 45 entities across 5 platforms | `test_platforms.py` |
| Fake device, 15 fault modes, telnet tripwire | `test_harness.py` |
| Bronze quality scale declared and enforced | `test_quality_scale.py` |
| Public repo, 5 CI checks green | GitHub Actions |

The reversal this plan encodes — HTTP-only to telnet-preferred — came from testing an assumption
that the earlier work had accepted without evidence. See the requirements doc's open-items table.

## Dependencies

- **B1 → everything.** The report seam is what stops telnet duplicating the state model.
- **B4 is the go/no-go.** If the existing suite does not stay green with HTTP emitting reports,
  stop and fix the seam before writing any telnet code.
- **C2 → D2 → D3 → all of M-E.** Every new entity depends on capability-driven creation, which
  depends on a working telnet client.
- **F2 is user-gated.** No live writes without an explicit window.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| The refactor in M-B breaks working, shipped behaviour | Medium | B4 is a hard checkpoint: the whole existing suite must pass unchanged. No new features until it does |
| Another controller takes the telnet socket later, silently degrading the integration | Medium | Fallback is automatic and logged once; a diagnostics field reports the active transport |
| Telnet push is missed, leaving state stale | Low | 60 s `GET STA` safety net (D4); the device also volunteers routing every 8–16 s |
| Responses and pushes interleaving corrupts a read | Medium | No request/response correlation is attempted; every line merges into a report, and writes confirm by value |
| Weakening the telnet guard (C4) lets a real regression through | Low | Rewritten, not removed: it must still prove nothing connects under the `http` setting |
| Holding the socket breaks something the owner has not thought of | Low | The `http` setting is an absolute escape hatch, and F2 is done with the owner present |

## Open process questions

- **Feature branch.** The repository has committed to `main` throughout, matching the sibling
  Home Assistant workspace. M-B is a refactor of working code, so a branch would be defensible —
  not created unilaterally.
- **Version.** M-E is additive but changes `iot_class`, so 0.2.0 rather than 0.1.1.
