"""Which endpoints to read on which tick.

Pure. No I/O, no Home Assistant imports, no clock -- the tick is a counter, not a time.

**Why tiering is not an optimisation.** The unit's own web UI polls only the *visible* tab, one
request every 5 seconds, and only while a browser is open: roughly 0.2 requests per second,
intermittently. Reading all six status endpoints every 5 seconds would be six times that, 24
hours a day, forever, against an embedded HTTP stack that must also serve the web UI and coexist
with whatever control system already owns the matrix. The measured evidence -- 9-10 ms responses,
zero failures at 10 requests per second -- justifies *a* poll every 5 seconds. It does not
justify six.

With the tiers below the steady state is::

    hot   1 endpoint, read every tick             = 1.00 per tick
    warm  2 endpoints, one of them per tick       = 1.00 per tick
    cold  3 endpoints, one of them every 4th tick = 0.25 per tick
                                                    ----
                                                    2.25 requests per 5 s tick = 0.45 req/s

So roughly 2.25x the vendor UI's rate, not 6x -- and against a device that answered 30 requests
at 10/s with zero failures and a 12.5 ms mean, which leaves a margin of more than twenty times.

Note that the warm tier costs a full request per tick, not half of one: *each* warm endpoint is
read every second tick, but there are two of them, so one warm read happens every tick. Halving
the tier's endpoint count would halve its rate; alternating within it does not.
"""

from __future__ import annotations

from typing import Final

from .protocol import StatusEndpoint

#: Read every tick. Video routing is the only thing that changes in normal operation, and the
#: only thing an external control system changes behind Home Assistant's back.
HOT: Final[tuple[StatusEndpoint, ...]] = (StatusEndpoint.VIDEO,)

#: One of these per tick, round-robin -- so each is read every 2 ticks. Audio routing is
#: user-visible, and signal info changes whenever a source wakes or sleeps.
WARM: Final[tuple[StatusEndpoint, ...]] = (StatusEndpoint.AUDIO, StatusEndpoint.INFO)

#: One of these every 4th tick, round-robin -- so each is read every 12 ticks. Scaler settings,
#: EDID assignments and port names are install-time configuration; nobody changes them at runtime.
COLD: Final[tuple[StatusEndpoint, ...]] = (
    StatusEndpoint.SYSTEM,
    StatusEndpoint.EDID,
    StatusEndpoint.WEB,
)

#: Read once at setup to establish identity and capabilities, then never on a schedule. The MAC
#: does not change, and everything else in the network body is deliberately discarded.
CENSUS_ONLY: Final[tuple[StatusEndpoint, ...]] = (StatusEndpoint.NETWORK, StatusEndpoint.TMDS)

#: Ticks between cold reads.
COLD_EVERY: Final = 4


def endpoints_for_tick(tick: int) -> tuple[StatusEndpoint, ...]:
    """The endpoints due on ``tick``, a monotonically increasing counter starting at 0.

    Pure and total: the same tick always yields the same set, which is what makes the whole
    cadence testable without a clock or a device.
    """
    due: list[StatusEndpoint] = [*HOT]
    due.append(WARM[tick % len(WARM)])
    if tick % COLD_EVERY == 0:
        due.append(COLD[(tick // COLD_EVERY) % len(COLD)])
    return tuple(due)


class PollSchedule:
    """Stateful wrapper over :func:`endpoints_for_tick`: census, tick counter and promotion.

    Not thread-safe and does not need to be -- Home Assistant's event loop is single-threaded and
    the coordinator is the only caller.
    """

    __slots__ = ("_census_done", "_promoted", "_tick")

    def __init__(self) -> None:
        self._tick = 0
        self._census_done = False
        self._promoted: set[StatusEndpoint] = set()

    @property
    def tick(self) -> int:
        """How many polls have been handed out so far."""
        return self._tick

    @property
    def census_done(self) -> bool:
        """True once the opening full read has been handed out."""
        return self._census_done

    def promote(self, endpoint: StatusEndpoint) -> None:
        """Read ``endpoint`` on the next tick regardless of its tier.

        Called after a write so the confirming poll actually looks at the thing that was
        written, instead of waiting up to twelve ticks for a cold endpoint to come round.
        """
        self._promoted.add(endpoint)

    def next_endpoints(self) -> tuple[StatusEndpoint, ...]:
        """Hand out the next poll's endpoints and advance the tick.

        The first call returns **everything**: entities are created from what the device actually
        reports, so the opening read has to establish which endpoints exist before any platform
        is set up.
        """
        if not self._census_done:
            self._census_done = True
            self._tick += 1
            self._promoted.clear()
            return tuple(StatusEndpoint)

        due = dict.fromkeys((*endpoints_for_tick(self._tick), *self._promoted))
        self._tick += 1
        self._promoted.clear()
        return tuple(due)
