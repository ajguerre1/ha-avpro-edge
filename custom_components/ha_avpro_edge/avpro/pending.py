"""The optimistic-write overlay.

Pure apart from an **injected clock**, which is what makes every deadline case a fast offline
test instead of a sleeping one. No I/O, no Home Assistant imports.

The matrix acknowledges nothing useful: a command returns a 200 whether or not it did anything,
and a bogus endpoint returns a 200 too. So a write cannot be confirmed by its response. It is
confirmed **by value** -- the next poll either shows the commanded value or it does not.

This is an overlay, not a mirror. Nothing optimistic is ever written into ``MatrixState``; the
state stays device truth, and the overlay sits in front of it for a bounded time. Reads go
through :meth:`PendingWrites.get`, so an entity shows the commanded value immediately while the
underlying state still shows the old one.

**The rule that is easy to get wrong.** On a device that *pushes*, the right rule is "any report
about this path clears the overlay". Here reports arrive by *poll*, and a poll fired a second
after a write can easily land before an HDMI matrix has finished re-routing. Clearing on that
report would clear the overlay with the **pre-write** value, and the user would watch the input
flick back to the old source and then forward again. So a poll that disagrees does not clear
anything: the entry is **kept** until its deadline passes.

The deadline is the only tuning knob, and it bounds the one thing that cannot be solved. From a
value-only poll there is no way to distinguish "the matrix has not applied it yet" from "another
controller overwrote me half a second later". Making the window exactly as long as the measured
apply latency means the first is covered and the second costs at most that long a wrong reading.
Once the deadline passes the device is authoritative, whatever it says.

**Nothing here ever re-sends a command.** With a second control system on the same matrix,
"the poll disagreed, so write it again" is how two controllers re-assert their last command at
each other indefinitely.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PendingWrite:
    """One commanded value and the monotonic deadline after which the device wins."""

    value: Any
    deadline: float


class PendingWrites:
    """Overlay of commanded-but-unconfirmed values, keyed by canonical state key.

    Keys are the integration's own state keys (``"video_route_1"``, ``"audio_delay_3"``), not
    URLs. There is one key per settable thing and a 1:1 map from key to command, so comparing a
    pending entry against a fresh poll is a dictionary lookup rather than a reverse-engineering
    of which request produced which field.
    """

    __slots__ = ("_clock", "_writes")

    def __init__(self, clock: Callable[[], float]) -> None:
        """``clock`` must return a monotonic time in seconds."""
        self._clock = clock
        self._writes: dict[str, PendingWrite] = {}

    # -- reading -------------------------------------------------------------------------

    def get(self, key: str, fallback: Any = None) -> Any:
        """The commanded value if one is outstanding, else ``fallback``."""
        write = self._writes.get(key)
        return write.value if write is not None else fallback

    def __contains__(self, key: object) -> bool:
        return key in self._writes

    def __len__(self) -> int:
        return len(self._writes)

    def __iter__(self) -> Iterator[str]:
        return iter(self._writes)

    def keys(self) -> frozenset[str]:
        """Every key with an outstanding write."""
        return frozenset(self._writes)

    # -- writing -------------------------------------------------------------------------

    def record(self, key: str, value: Any, window: float) -> None:
        """Note that ``key`` has been commanded to ``value``.

        Re-recording the same key restarts its window, which is correct: the newer command is
        the one whose latency is now being waited out.
        """
        self._writes[key] = PendingWrite(value, self._clock() + window)

    def discard(self, key: str) -> None:
        """Drop an entry without waiting for confirmation or expiry."""
        self._writes.pop(key, None)

    def clear(self) -> None:
        """Drop everything.

        Called when the transport drops. A queued optimistic value replayed minutes later is a
        stale command, and an overlay surviving a reconnect would claim a value nothing ever
        confirmed.
        """
        self._writes.clear()

    # -- resolution ----------------------------------------------------------------------

    def confirm(self, lookup: Callable[[str], Any]) -> frozenset[str]:
        """Drop every entry whose device value now matches what was commanded.

        ``lookup`` maps a state key to the freshly-polled device value, so this module never
        needs to know what ``MatrixState`` looks like.

        A key whose polled value *disagrees* is deliberately left alone -- see the module
        docstring. That is the difference between bridging apply latency and flickering.
        """
        confirmed = {key for key, write in self._writes.items() if lookup(key) == write.value}
        for key in confirmed:
            del self._writes[key]
        return frozenset(confirmed)

    def expire(self) -> frozenset[str]:
        """Drop every entry whose deadline has passed, and report which.

        A returned key means the write was commanded, the settle window elapsed, and the device
        never came to agree. Either it was rejected or another controller changed it back. The
        caller counts these: a user seeing hundreds of expiries on one output learns immediately
        that something else owns it, which no amount of logging conveys as clearly.
        """
        now = self._clock()
        expired = {key for key, write in self._writes.items() if now >= write.deadline}
        for key in expired:
            del self._writes[key]
        return frozenset(expired)

    def next_deadline(self) -> float | None:
        """The earliest outstanding deadline, or ``None`` when nothing is pending."""
        return min((w.deadline for w in self._writes.values()), default=None)
