"""A primary transport, plus the one kind of value it cannot read.

Telnet reads five kinds HTTP has no status endpoint for, and is the primary transport because of
it. But the asymmetry runs both ways: **HTTP can read signal detection and telnet cannot.** That
was established against the live matrix rather than assumed -- 32 command spellings across two
probe rounds (``GET IN1 SIG``, ``SIGNAL``, ``HPD``, ``5V``, ``RES``, ``ACTIVE``, ``LINK`` and the
rest) every one of which answered ``CMD ERR``, against known-good controls like ``GET IN1 EDID``
answering ``IN1 EDID 30``. ``GET STA`` carries no signal line either.

Signal is not a minor field. ``media_player.state`` is derived from it, as are eight entities, four
of which are enabled by default. Without this class, choosing the better transport would silently
turn all of them off -- and worse than off: the signal binary sensor's empty-series guard passes a
tuple of ``None``s, so every port would report *Disconnected* rather than unknown.

**Why this does not violate the transport rule.** "Always speak telnet unless you don't need to"
forbids HTTP for anything telnet *supports*. Signal is not such a thing, so this is the same
documented exception that already covers reading port names and renaming them: HTTP is used for an
operation only HTTP has, and for nothing else. What the rule actually forbids -- hedging, running
both wires for the same value, two sources of truth to reconcile -- does not happen here, because
exactly one wire can produce this value at all.

**Why it owns a timer.** The coordinator polls a pushing transport only every 60 s, since pushes
carry normal operation. Signal does not push. Polling it on the safety-net interval would make a
source waking up take up to a minute to show, so the supplement runs its own clock on the user's
polling profile and delivers results through the same subscriber callback telnet pushes through.
The coordinator needs no knowledge of any of this: it still holds one ``Transport``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from .client import AvProClient, AvProConnectionError
from .http_decode import decode
from .protocol import StatusEndpoint
from .report import EMPTY, DeviceReport
from .transport import Transport, TransportCapabilities

_LOGGER = logging.getLogger(__name__)

#: The endpoint carrying detected signal, and the only one this class ever reads.
SIGNAL_ENDPOINT = StatusEndpoint.INFO

#: The state-key kind it contributes.
SIGNAL_KIND = "signal"


class SupplementedTransport:
    """Delegates everything to a primary transport, and adds signal from HTTP.

    Presents itself as the primary -- ``name`` reports ``"telnet"`` -- because that is what it is
    from every caller's point of view. The supplement is an implementation detail of "what can
    this transport read", which is exactly what :attr:`capabilities` is for.
    """

    def __init__(
        self,
        primary: Transport,
        client: AvProClient,
        *,
        interval: float,
        port_count: int = 4,
    ) -> None:
        self._primary = primary
        self._client = client
        self._interval = interval
        self._port_count = port_count

        self._subscribers: list[Callable[[DeviceReport], None]] = []
        self._task: asyncio.Task[None] | None = None
        self._unsubscribe_primary: Callable[[], None] | None = None
        self._failed = False

    # -- identity, all of it the primary's ------------------------------------------------

    @property
    def name(self) -> str:
        return self._primary.name

    @property
    def host(self) -> str:
        return self._primary.host

    @property
    def pushes(self) -> bool:
        return self._primary.pushes

    @property
    def connected(self) -> bool:
        return self._primary.connected

    @property
    def capabilities(self) -> TransportCapabilities:
        """The primary's, plus signal as readable.

        Not writable: signal is a measurement, and no endpoint on either wire sets it.
        """
        caps = self._primary.capabilities
        return TransportCapabilities(
            readable=caps.readable | {SIGNAL_KIND},
            writable=caps.writable,
            pushes=caps.pushes,
        )

    def set_port_count(self, count: int) -> None:
        self._port_count = count
        setter = getattr(self._primary, "set_port_count", None)
        if setter is not None:
            setter(count)

    # -- lifecycle -----------------------------------------------------------------------

    async def async_connect(self) -> None:
        # Subscribed before connecting: the primary may report something during its own census,
        # and a push dropped because nobody was listening yet is indistinguishable from one the
        # device never sent.
        self._unsubscribe_primary = self._primary.subscribe(self._forward)
        await self._primary.async_connect()
        self._task = asyncio.create_task(self._poll_signal())

    async def async_disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._unsubscribe_primary is not None:
            self._unsubscribe_primary()
            self._unsubscribe_primary = None
        await self._primary.async_disconnect()

    def subscribe(self, on_report: Callable[[DeviceReport], None]) -> Callable[[], None]:
        self._subscribers.append(on_report)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(on_report)

        return _unsubscribe

    def _forward(self, report: DeviceReport) -> None:
        """Pass the primary's pushes straight through."""
        self._dispatch(report)

    def _dispatch(self, report: DeviceReport) -> None:
        for subscriber in list(self._subscribers):
            subscriber(report)

    # -- reading -------------------------------------------------------------------------

    async def async_read_all(self) -> DeviceReport:
        """The primary's census, with signal folded in.

        Signal has to be part of the census rather than arriving later, or entities would be
        created before it is known and the capability check would say it is unreadable.
        """
        report = await self._primary.async_read_all()
        return report.merge(await self._read_signal())

    async def async_refresh(self) -> DeviceReport:
        """The primary's read, with signal folded in.

        Signal is included even though a timer already polls it. "Whatever is due now" is what a
        refresh means, and signal -- the one value here that never pushes -- is due on every
        single one. Leaving it out made the timer the *only* path to a fresh reading, so an
        explicit refresh returned stale signal and the safety net stopped being a safety net for
        the one field that most needed one.

        The extra cost is one request per refresh, which on a pushing transport is once a minute.
        """
        report = await self._primary.async_refresh()
        return report.merge(await self._read_signal())

    async def async_command(self, key: str, value: Any) -> None:
        """Everything writable belongs to the primary; signal is not writable on any wire."""
        await self._primary.async_command(key, value)

    async def _read_signal(self) -> DeviceReport:
        """One HTTP read of the signal endpoint. Never raises.

        A supplement that could fail an update would make the integration *less* reliable than
        the transport it is supplementing -- an unreachable web server would take routing
        unavailable, which telnet was reporting perfectly well.
        """
        try:
            parsed = await self._client.async_read(SIGNAL_ENDPOINT)
        except AvProConnectionError as err:
            if not self._failed:
                _LOGGER.debug("%s: signal read failed: %s", self.host, err)
                self._failed = True
            return EMPTY

        self._failed = False
        return decode(SIGNAL_ENDPOINT, parsed, port_count=self._port_count)

    async def _poll_signal(self) -> None:
        """Read signal on the user's polling profile and push whatever it says.

        Delivered as a partial report through the subscriber path, so it reaches the coordinator
        exactly the way a telnet push does and needs no special handling there.
        """
        while True:
            await asyncio.sleep(self._interval)
            try:
                report = await self._read_signal()
            except Exception:
                # A task that dies takes signal with it permanently, and silently: nothing polls
                # it again and no entity goes unavailable, because the rest of the transport is
                # still perfectly healthy. Whatever the surprise was, the next tick may not hit
                # it, so the loop survives and says so.
                _LOGGER.exception("%s: signal poll raised; continuing", self.host)
                continue
            if report:
                self._dispatch(report)
