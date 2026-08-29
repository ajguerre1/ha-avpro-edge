"""``DeviceReport`` — what a transport is allowed to produce.

Pure. No I/O, no Home Assistant imports, no clock.

This is the seam that lets one state model serve two wires. Telnet says ``OUT1 VS IN2``; HTTP says
``O1I2`` inside a ``VidSta`` body. Both normalise to ``{"video_route_1": 2}``, and from there
nothing downstream — the state fold, the pending overlay, the entities — can tell which wire the
value came from.

Without this seam, adding telnet means either a second state model or transport types leaking up
into the entities. With it, the transports are the only code that knows the difference.

**What ``complete`` means.** Only this: enough has been read to create entities from. It is *not*
a licence to clear fields the report omits, because no single report is ever authoritative about
the whole device -- telnet's ``GET STA`` knows nothing of the port names, and the HTTP census
knows nothing of the output stream state. Clearing on absence would have each transport erase the
other's contribution on every cycle. :func:`avpro.state.apply` therefore only ever merges.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DeviceReport:
    """A set of observations about the device, in canonical state keys."""

    values: Mapping[str, Any] = field(default_factory=dict)

    #: True when this is a census -- a ``GET STA`` or a full HTTP read -- meaning enough has been
    #: gathered to create entities from. False for an unsolicited push or a single-endpoint read.
    complete: bool = False

    def __bool__(self) -> bool:
        return bool(self.values)

    def __contains__(self, key: object) -> bool:
        return key in self.values

    def get(self, key: str, fallback: Any = None) -> Any:
        return self.values.get(key, fallback)

    def merge(self, other: DeviceReport) -> DeviceReport:
        """Combine two reports, ``other`` winning where they overlap.

        Used to assemble one report from several endpoint reads on the HTTP path, and to fold
        successive telnet lines into a single update. The result counts as a census only if some
        contributing report was one -- merging two partial views does not add up to having read
        the device.
        """
        return DeviceReport(
            values={**self.values, **other.values},
            complete=self.complete or other.complete,
        )

    @classmethod
    def census(cls, values: Mapping[str, Any]) -> DeviceReport:
        """A report describing the entire device."""
        return cls(values=dict(values), complete=True)

    @classmethod
    def update(cls, values: Mapping[str, Any]) -> DeviceReport:
        """A report describing only the keys it names."""
        return cls(values=dict(values), complete=False)


#: An empty partial report. Returned when a transport had nothing to say -- a line it did not
#: recognise, an endpoint that is absent -- so callers never have to handle ``None``.
EMPTY = DeviceReport()
