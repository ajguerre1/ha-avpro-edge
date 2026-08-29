"""``MatrixState`` -- an immutable, value-comparable projection of the matrix.

Pure. No I/O, no Home Assistant imports, no clock.

State is built by applying :class:`DeviceReport` objects, which are transport-neutral. That is the
whole reason this module has one ``apply`` where it previously had seven ``fold_*`` functions:
those existed only because the HTTP endpoints have different shapes, and once a transport
normalises to a report, the shape is the transport's problem rather than the state's.

Two properties carry the design.

**Structural equality.** ``new == old`` is true whenever nothing actually moved, which lets the
coordinator run with ``always_update=False`` and notify no listeners at all on a quiet cycle. On
a large installation that is the difference between a silent integration and
a permanently chattering one.

**Applying never clears.** A key a report does not mention keeps its previous value, always. That
is deliberate and it is why ``complete`` is not used for clearing: no single report is ever
authoritative about the whole device. Telnet's ``GET STA`` knows nothing of the port names; the
HTTP census knows nothing of the output stream state. A rule like "a complete report clears what
it omits" would have each transport erase the other's contribution on every cycle.

``complete`` therefore means one thing only: enough has been read to create entities from. The
coordinator uses it to gate setup, not to decide what to forget.

Absence is modelled explicitly. ``None`` means "this firmware did not tell us", never "off" and
never "input 0". Entities render that as unknown rather than inventing a default -- a plausible
wrong value is worse than nothing, because it is indistinguishable from the truth.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final

from .report import DeviceReport

#: Ports on the model this was developed against. Only a fallback: the real count is derived from
#: however many routes the device actually reports, so other members of the family size correctly.
DEFAULT_PORT_COUNT: Final = 4

#: ``video_route_2`` -> ``("video_route", 2)``. Device-level keys have no suffix.
_INDEXED: Final = re.compile(r"^(?P<kind>[a-z_]+?)_(?P<index>\d+)$")

# Device-level keys, kept as named properties because they are used everywhere.
KEY_MODEL: Final = "model"
KEY_FIRMWARE: Final = "firmware"
KEY_MAC: Final = "mac"
KEY_BIND_MODE: Final = "bind_mode"

# Indexed key kinds with a named accessor.
KIND_OUTPUT_NAME: Final = "output_name"
KIND_INPUT_NAME: Final = "input_name"
KIND_VIDEO_ROUTE: Final = "video_route"
KIND_SIGNAL: Final = "signal"


def split_key(key: str) -> tuple[str, int | None]:
    """``("video_route", 2)`` for an indexed key, ``(key, None)`` for a device-level one."""
    if match := _INDEXED.match(key):
        return match["kind"], int(match["index"])
    return key, None


@dataclass(frozen=True, slots=True)
class MatrixState:
    """Everything known about the matrix, keyed by canonical state key.

    ``values`` is treated as immutable: :func:`apply` always builds a new mapping rather than
    mutating one. It is a plain dict rather than a ``MappingProxyType`` so that equality stays a
    cheap dict comparison, which is what the change-gating depends on.
    """

    values: Mapping[str, Any] = field(default_factory=dict)

    #: Key kinds observed at least once. Entities are created from this, so a transport that
    #: cannot report something simply has no entities for it rather than a row of unknowns.
    seen: frozenset[str] = field(default_factory=frozenset)

    #: True once enough has been read to create entities from.
    census_done: bool = False

    # -- generic access ------------------------------------------------------------------

    def get(self, key: str, fallback: Any = None) -> Any:
        """The value for a canonical state key, or ``fallback`` if it was never reported."""
        return self.values.get(key, fallback)

    def series(self, kind: str, count: int | None = None) -> tuple[Any, ...]:
        """All values of one indexed kind, as a dense tuple indexed from zero.

        ``series("video_route")`` -> ``(1, 2, 3, 4)``. Missing entries are ``None``.
        """
        size = count if count is not None else self.port_count
        return tuple(self.values.get(f"{kind}_{i}") for i in range(1, size + 1))

    def has(self, kind: str) -> bool:
        """Whether any value of this kind has ever been reported."""
        return kind in self.seen

    # -- named accessors, for the things used everywhere ---------------------------------

    @property
    def model(self) -> str | None:
        return self.values.get(KEY_MODEL)

    @property
    def firmware(self) -> str | None:
        return self.values.get(KEY_FIRMWARE)

    @property
    def mac(self) -> str | None:
        return self.values.get(KEY_MAC)

    @property
    def bind_mode(self) -> Any:
        return self.values.get(KEY_BIND_MODE)

    @property
    def port_count(self) -> int:
        """How many inputs and outputs this unit has, derived rather than assumed.

        Taken from the highest video-route index reported, so an eight-output unit sizes itself
        correctly without the model being recognised.
        """
        highest = 0
        for key in self.values:
            kind, index = split_key(key)
            if kind == KIND_VIDEO_ROUTE and index is not None:
                highest = max(highest, index)
        return highest or DEFAULT_PORT_COUNT

    @property
    def video_routes(self) -> tuple[int | None, ...]:
        return self.series(KIND_VIDEO_ROUTE)

    @property
    def signals(self) -> tuple[str | None, ...]:
        return self.series(KIND_SIGNAL)

    @property
    def output_names(self) -> tuple[str | None, ...]:
        return self.series(KIND_OUTPUT_NAME)

    @property
    def input_names(self) -> tuple[str | None, ...]:
        return self.series(KIND_INPUT_NAME)

    def output_name(self, output: int) -> str | None:
        """The configured name for a 1-based output, if known. Site data -- never log it."""
        return self.values.get(f"{KIND_OUTPUT_NAME}_{output}") if output >= 1 else None

    def input_name(self, source: int) -> str | None:
        """The configured name for a 1-based input, if known. Site data -- never log it."""
        return self.values.get(f"{KIND_INPUT_NAME}_{source}") if source >= 1 else None


def apply(state: MatrixState, report: DeviceReport) -> MatrixState:
    """Fold one report onto the state, returning a new one.

    Never clears: a key the report does not mention keeps whatever it had. See the module
    docstring for why -- no single report is authoritative about the whole device, so clearing on
    absence would make each transport erase the other's contribution.

    Returns the *same object* when nothing changed, so the caller's identity check is as cheap as
    its equality check.
    """
    if not report.values:
        # Still enough to complete the census, if that is what this report was.
        if report.complete and not state.census_done:
            return replace(state, census_done=True)
        return state

    merged = {**state.values, **report.values}
    census_done = state.census_done or report.complete

    if merged == state.values and census_done == state.census_done:
        return state

    kinds = {split_key(key)[0] for key in report.values}
    return MatrixState(
        values=merged,
        seen=state.seen | kinds,
        census_done=census_done,
    )
