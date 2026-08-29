"""``DeviceReport`` — what a transport is allowed to produce.

Pure. No I/O, no Home Assistant imports, no clock.

This is the seam that lets one state model serve two wires. Telnet says ``OUT1 VS IN2``; HTTP says
``O1I2`` inside a ``VidSta`` body. Both normalise to ``{"video_route_1": 2}``, and from there
nothing downstream — the state fold, the pending overlay, the entities — can tell which wire the
value came from.

Without this seam, adding telnet means either a second state model or transport types leaking up
into the entities. With it, the transports are the only code that knows the difference.

**Complete versus partial matters.** A telnet push carrying only ``OUT1 VS IN2`` says nothing
about the other three outputs, so it must not be allowed to blank them. A full ``GET STA`` does
describe the whole device, so a field it omits is genuinely absent. Conflating the two would make
every push erase most of the state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DeviceReport:
    """A set of observations about the device, in canonical state keys."""

    values: Mapping[str, Any] = field(default_factory=dict)

    #: True when this describes the *whole* device -- a ``GET STA`` or a full HTTP census. False
    #: for an unsolicited push or a single-endpoint read, which speak only about what they name.
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
        successive telnet lines into a single update. The result is complete only if some
        contributing report was complete -- merging two partial views of a device does not add up
        to knowing the whole of it.
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
