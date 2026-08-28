"""What this particular unit and firmware can actually do.

Pure. No I/O, no Home Assistant imports, no clock.

The AUHD family shares one command vocabulary across models that do not share one feature set,
and the vendor's own driver notes warn that a command valid on one model answers ``NO SUPPORT``
on another. Firmware revisions differ too: ``TMDSDivSta.CGI`` is simply absent on V1.41 and
answers with the HTML "not found" body.

So capability is **discovered, never predicted**. Two rules make that safe:

* **Findings are sticky.** Once an endpoint has answered "not found", it is not asked again.
  Without that, a cold endpoint that does not exist would be re-requested forever.
* **Absence is not failure.** A missing warm or cold endpoint records a capability and lets the
  update succeed. Only the hot endpoint -- video routing, the one thing that cannot be inferred
  from anything else -- can fail an update. Getting that asymmetry wrong means one absent tab
  takes every entity unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .protocol import CommandEndpoint, StatusEndpoint


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Immutable record of what has been ruled out. Value-comparable, like ``MatrixState``."""

    #: Endpoints that answered the firmware's HTML "not found" body.
    absent: frozenset[StatusEndpoint] = field(default_factory=frozenset)
    #: Command endpoints that answered ``NO SUPPORT``.
    unsupported: frozenset[CommandEndpoint] = field(default_factory=frozenset)

    def endpoint_available(self, endpoint: StatusEndpoint) -> bool:
        """False once the endpoint has been shown not to exist on this firmware."""
        return endpoint not in self.absent

    def command_available(self, endpoint: CommandEndpoint) -> bool:
        """False once the command endpoint has answered ``NO SUPPORT``."""
        return endpoint not in self.unsupported

    def with_absent(self, endpoint: StatusEndpoint) -> Capabilities:
        """Record that a status endpoint does not exist here."""
        if endpoint in self.absent:
            return self
        return replace(self, absent=self.absent | {endpoint})

    def with_unsupported(self, endpoint: CommandEndpoint) -> Capabilities:
        """Record that a command endpoint is not implemented here."""
        if endpoint in self.unsupported:
            return self
        return replace(self, unsupported=self.unsupported | {endpoint})

    def as_diagnostics(self) -> dict[str, list[str]]:
        """A stable, non-sensitive summary. Endpoint paths only -- never device values."""
        return {
            "absent_endpoints": sorted(e.value for e in self.absent),
            "unsupported_commands": sorted(e.value for e in self.unsupported),
        }
