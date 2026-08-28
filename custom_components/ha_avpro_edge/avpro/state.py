"""``MatrixState`` -- an immutable, value-comparable projection of the matrix.

Pure. No I/O, no Home Assistant imports, no clock.

The state is rebuilt by *folding* one parsed status body at a time onto the previous state, each
fold returning a new frozen instance. Two consequences follow, and both are the point:

* **Structural equality works.** ``new == old`` is true whenever nothing actually moved, which is
  what lets the coordinator run with ``always_update=False`` and notify no listeners at all on a
  quiet tick. On an installation driving fifty wall panels that is the difference between a
  silent integration and a permanently chattering one.
* **A partial update cannot corrupt the whole.** Only the fields an endpoint owns are replaced.
  A failed or absent endpoint leaves its fields exactly as they were, so a cold endpoint that
  vanishes on some firmware does not blank the entities built from it.

Absence is modelled explicitly. ``None`` means "this firmware did not tell us", never "off" and
never "input 0". Entities render that as unknown rather than inventing a default -- reporting a
plausible wrong value is worse than reporting nothing, because it is indistinguishable from the
truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final

from . import protocol as p
from .models import (
    AUDIO_DELAY_BY_CODE,
    BIND_MODE_BY_CODE,
    IMAGE_ENHANCEMENT_BY_CODE,
    SCALER_MODE_BY_CODE,
    AudioDelay,
    BindMode,
    ImageEnhancement,
    ScalerMode,
    decode_edid,
)

#: Ports on the model this was developed against. Only a fallback: the real count is derived
#: from however many routes the video status actually reports, so other members of the family
#: (MX42, MX88) size themselves correctly.
DEFAULT_PORT_COUNT: Final = 4

#: Number of leading fields in the web status before the port names begin (model, firmware).
_WEB_HEADER_FIELDS: Final = 2


def _sized(values: dict[int, object], count: int) -> tuple:
    """Turn a sparse ``{1-based index: value}`` map into a dense tuple of length ``count``."""
    return tuple(values.get(i + 1) for i in range(count))


@dataclass(frozen=True, slots=True)
class MatrixState:
    """Everything known about the matrix. Frozen, hashable by value, cheap to compare."""

    # --- identity -----------------------------------------------------------------------
    model: str | None = None
    firmware: str | None = None
    mac: str | None = None

    # --- port naming --------------------------------------------------------------------
    #: The owner's names for each port. Site data: never logged, never in a unique_id, never
    #: in an entity name. Surfaced only as ``source_list`` labels and a state attribute.
    output_names: tuple[str | None, ...] = ()
    input_names: tuple[str | None, ...] = ()

    # --- routing ------------------------------------------------------------------------
    #: 1-based input number feeding each output, indexed by output - 1.
    video_routes: tuple[int | None, ...] = ()
    audio_routes: tuple[int | None, ...] = ()

    # --- extracted audio ----------------------------------------------------------------
    extracted_audio: tuple[bool | None, ...] = ()
    audio_delays: tuple[AudioDelay | None, ...] = ()
    bind_mode: BindMode | None = None

    # --- per-output video processing ----------------------------------------------------
    scaler_modes: tuple[ScalerMode | None, ...] = ()
    image_enhancements: tuple[ImageEnhancement | None, ...] = ()
    test_patterns: tuple[bool | None, ...] = ()

    # --- signal and EDID ----------------------------------------------------------------
    #: Free text exactly as the device reports it, e.g. "3840X2160P@60HZ YUV420". Deliberately
    #: not parsed into an enumeration: any set of values written today is a bug on tomorrow's
    #: firmware. An empty string means the port reported nothing.
    signals: tuple[str | None, ...] = ()
    edid: tuple[str | None, ...] = ()

    #: Which endpoints have been folded in at least once. Entities are created from this, so a
    #: firmware missing an endpoint simply has no entities for it rather than a row of unknowns.
    seen: frozenset[p.StatusEndpoint] = field(default_factory=frozenset)

    @property
    def port_count(self) -> int:
        """How many inputs and outputs this unit has, derived rather than assumed."""
        return len(self.video_routes) or DEFAULT_PORT_COUNT

    def output_name(self, output: int) -> str | None:
        """The owner's name for a 1-based output, if known."""
        return self._at(self.output_names, output)

    def input_name(self, source: int) -> str | None:
        """The owner's name for a 1-based input, if known."""
        return self._at(self.input_names, source)

    @staticmethod
    def _at(values: tuple, index: int):
        return values[index - 1] if 1 <= index <= len(values) else None


# ---------------------------------------------------------------------------------------------
# Folds -- one per status endpoint
# ---------------------------------------------------------------------------------------------
#
# Every fold takes the current state and a parsed body and returns a new state. A fold that
# cannot make sense of the body returns the state unchanged: keeping the last known good value
# beats replacing it with nothing.


def _unchanged_unless_ok(state: MatrixState, parsed: p.ParsedStatus) -> MatrixState | None:
    return None if parsed.ok else state


def fold_web(state: MatrixState, parsed: p.ParsedStatus, *, port_count: int) -> MatrixState:
    """Model, firmware and all port names.

    Guarded by arity, because port names are free text and an embedded ``&`` shifts every field
    after it. A shifted response is rejected outright rather than applied one position out.
    """
    expected = _WEB_HEADER_FIELDS + 2 * port_count
    guarded = p.expect_fields(parsed, expected)
    if (early := _unchanged_unless_ok(state, guarded)) is not None:
        return early

    fields = guarded.fields
    names = fields[_WEB_HEADER_FIELDS:]
    return replace(
        state,
        model=fields[0] or None,
        firmware=fields[1] or None,
        output_names=tuple(n or None for n in names[:port_count]),
        input_names=tuple(n or None for n in names[port_count:]),
        seen=state.seen | {p.StatusEndpoint.WEB},
    )


def fold_video(state: MatrixState, parsed: p.ParsedStatus) -> MatrixState:
    """Video routing. This body also establishes how many ports the unit has."""
    if (early := _unchanged_unless_ok(state, parsed)) is not None:
        return early

    routes = {
        output: source
        for token in parsed.fields
        if (pair := p.parse_video_route(token)) is not None
        for output, source in (pair,)
    }
    if not routes:
        return state

    count = max(len(parsed.fields), max(routes))
    return replace(
        state,
        video_routes=_sized(routes, count),
        seen=state.seen | {p.StatusEndpoint.VIDEO},
    )


def fold_audio(state: MatrixState, parsed: p.ParsedStatus, *, port_count: int) -> MatrixState:
    """Extracted-audio routing, enable flags, delays and the device-level bind mode.

    Tokens are classified by pattern rather than read by position, so the four groups may appear
    in any order and an unrecognised extra field is ignored instead of shifting the rest.
    """
    if (early := _unchanged_unless_ok(state, parsed)) is not None:
        return early

    routes: dict[int, int] = {}
    enabled: dict[int, bool] = {}
    delays: dict[int, AudioDelay] = {}
    bind: BindMode | None = state.bind_mode

    for token in parsed.fields:
        if (pair := p.parse_audio_route(token)) is not None:
            routes[pair[0]] = pair[1]
        elif (flag := p.parse_extracted_audio(token)) is not None:
            enabled[flag[0]] = flag[1]
        elif (delay := p.parse_audio_delay(token)) is not None:
            # An unknown delay code is dropped rather than mapped to a neighbouring value.
            if (member := AUDIO_DELAY_BY_CODE.get(delay[1])) is not None:
                delays[delay[0]] = member
        elif (code := p.parse_bind_mode(token)) is not None:
            bind = BIND_MODE_BY_CODE.get(code, bind)

    return replace(
        state,
        audio_routes=_sized(routes, port_count),
        extracted_audio=_sized(enabled, port_count),
        audio_delays=_sized(delays, port_count),
        bind_mode=bind,
        seen=state.seen | {p.StatusEndpoint.AUDIO},
    )


def fold_system(state: MatrixState, parsed: p.ParsedStatus, *, port_count: int) -> MatrixState:
    """Scaler mode, image enhancement and the built-in test pattern, per output."""
    if (early := _unchanged_unless_ok(state, parsed)) is not None:
        return early

    scalers: dict[int, ScalerMode] = {}
    enhancements: dict[int, ImageEnhancement] = {}
    patterns: dict[int, bool] = {}

    for token in parsed.fields:
        if (scaler := p.parse_scaler_mode(token)) is not None:
            if (mode := SCALER_MODE_BY_CODE.get(scaler[1])) is not None:
                scalers[scaler[0]] = mode
        elif (enhancement := p.parse_image_enhancement(token)) is not None:
            if (level := IMAGE_ENHANCEMENT_BY_CODE.get(enhancement[1])) is not None:
                enhancements[enhancement[0]] = level
        elif (pattern := p.parse_test_pattern(token)) is not None:
            patterns[pattern[0]] = pattern[1]

    return replace(
        state,
        scaler_modes=_sized(scalers, port_count),
        image_enhancements=_sized(enhancements, port_count),
        test_patterns=_sized(patterns, port_count),
        seen=state.seen | {p.StatusEndpoint.SYSTEM},
    )


def fold_info(state: MatrixState, parsed: p.ParsedStatus, *, port_count: int) -> MatrixState:
    """Detected signal description per port.

    Whether these four fields describe inputs or outputs is not established from the CGI
    interface alone; they are stored positionally and the entities built on them are labelled
    accordingly.
    """
    if (early := _unchanged_unless_ok(state, parsed)) is not None:
        return early

    values = list(parsed.fields[:port_count])
    values += [None] * (port_count - len(values))
    return replace(
        state,
        signals=tuple(v if v else None for v in values),
        seen=state.seen | {p.StatusEndpoint.INFO},
    )


def fold_edid(state: MatrixState, parsed: p.ParsedStatus, *, port_count: int) -> MatrixState:
    """Per-input EDID selection, decoded from the wire token to an option key.

    Decoded here rather than at the entity so that the state, the pending overlay and the entity
    all hold the same vocabulary -- which is what keeps confirming a write a plain equality
    check. An unrecognised token yields ``None`` rather than being carried through raw, so a
    firmware that invents one shows as unknown instead of as an option nothing can select.
    """
    if (early := _unchanged_unless_ok(state, parsed)) is not None:
        return early

    values = list(parsed.fields[:port_count])
    values += [None] * (port_count - len(values))
    return replace(
        state,
        edid=tuple(decode_edid(v) if v else None for v in values),
        seen=state.seen | {p.StatusEndpoint.EDID},
    )


def fold_network(state: MatrixState, parsed: p.ParsedStatus) -> MatrixState:
    """The unit's MAC address, and nothing else.

    The network body also carries the IP, netmask, gateway and a second copy of every port name.
    None of that is kept: the MAC is the one field with a use (a stable unique id), and the rest
    is site data that would only create another way for it to leak.
    """
    if (early := _unchanged_unless_ok(state, parsed)) is not None:
        return early
    if not parsed.fields:
        return state

    candidate = parsed.fields[0].strip()
    return replace(
        state,
        mac=candidate or None,
        seen=state.seen | {p.StatusEndpoint.NETWORK},
    )
