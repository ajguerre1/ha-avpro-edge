"""The interface both wires satisfy.

Pure declaration. No I/O, no Home Assistant imports.

The coordinator talks to a ``Transport`` and never to a telnet or HTTP client directly, so adding
the second wire changed no code above this line. ``subscribe`` exists on both implementations even
though only telnet ever calls back: the coordinator polls when ``pushes`` is False and treats a
push as an early result when it is True, rather than branching on which transport it holds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .report import DeviceReport


@dataclass(frozen=True, slots=True)
class TransportCapabilities:
    """What this wire can actually do, so entities are created from fact rather than hope.

    Extends the mechanism the HTTP transport already uses for endpoints a firmware lacks: a
    capability that is absent means no entity, rather than an entity that reads unknown forever.
    """

    #: State-key kinds this transport can read. ``{"video_route", "stream", ...}``
    readable: frozenset[str] = field(default_factory=frozenset)
    #: State-key kinds this transport can write. Usually the same set, but not necessarily --
    #: signal is readable and not writable, and on some firmware the reverse happens.
    writable: frozenset[str] = field(default_factory=frozenset)
    #: True when the device volunteers changes rather than having to be asked.
    pushes: bool = False

    def can_read(self, kind: str) -> bool:
        return kind in self.readable

    def can_write(self, kind: str) -> bool:
        return kind in self.writable

    def without(self, *kinds: str) -> TransportCapabilities:
        """A copy with some kinds removed, for a firmware that turns out to lack them."""
        dropped = frozenset(kinds)
        return TransportCapabilities(
            readable=self.readable - dropped,
            writable=self.writable - dropped,
            pushes=self.pushes,
        )


@runtime_checkable
class Transport(Protocol):
    """What the coordinator may assume, whichever wire is in use."""

    @property
    def name(self) -> str:
        """Short identifier for logs and diagnostics: ``"telnet"`` or ``"http"``."""

    @property
    def host(self) -> str: ...

    @property
    def capabilities(self) -> TransportCapabilities: ...

    @property
    def pushes(self) -> bool:
        """Whether the device volunteers changes on this wire."""

    @property
    def connected(self) -> bool:
        """Whether this transport is currently usable.

        On a wire that holds a socket this is a real question. On a stateless one it is always
        true, and saying so is better than leaving the attribute off: a caller should be able to
        ask without first working out which wire it is holding.
        """

    async def async_connect(self) -> None:
        """Prepare to talk to the device. A no-op for a stateless transport."""

    async def async_disconnect(self) -> None:
        """Release anything held. Must be safe to call when never connected."""

    async def async_read_all(self) -> DeviceReport:
        """Read everything this transport can, as a census."""

    async def async_refresh(self) -> DeviceReport:
        """Read whatever is due now.

        For a pushing transport this is the periodic safety net; for a polling one it is the
        tiered poll. Either way the caller does not need to know which.
        """

    async def async_command(self, key: str, value: Any) -> None:
        """Set one canonical state key. Raises on refusal or transport failure."""

    def subscribe(self, on_report: Callable[[DeviceReport], None]) -> Callable[[], None]:
        """Register a callback for unsolicited reports. Returns an unsubscribe callable.

        A transport that never pushes still accepts a subscriber and simply never calls it, so
        the coordinator has no special case.
        """
