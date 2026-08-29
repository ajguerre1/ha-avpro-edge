"""The coordinator: polling, the optimistic overlay, and every command semantic.

Entities stay thin -- they read through :meth:`AvProCoordinator.optimistic` and ask this class to
change things. Nothing about any wire protocol appears in a platform module, and after the
transport seam landed, nothing about any wire protocol appears *here* either: this talks to a
:class:`Transport` and never to a telnet or HTTP client.

Three behaviours are load-bearing and easy to undo by accident.

**Failure is asymmetric.** Only a failure to see routing can fail an update. An endpoint that is
absent on this firmware, refused, or momentarily unreadable records what was learned and lets the
cycle succeed. Without that, the one tab this firmware genuinely lacks would take every entity
unavailable.

**Nothing is written unless it changed.** ``always_update=False`` over a value-comparable
``MatrixState`` means a quiet cycle notifies no listeners at all. On an installation driving
around fifty wall panels, every state write fans out to all of them.

**A write is never re-sent.** The overlay bridges the device's apply latency and then yields. If
the device disagrees after the settle window, the device wins. Re-asserting would turn a
disagreement with another control system into two controllers fighting indefinitely.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .avpro import state as st
from .avpro.client import AvProConnectionError, AvProWritesDisabled
from .avpro.http_transport import HttpTransport, UnsupportedCommand
from .avpro.pending import PendingWrites
from .avpro.report import DeviceReport
from .avpro.state import MatrixState
from .avpro.transport import Transport
from .const import (
    DOMAIN,
    KEY_VIDEO_ROUTE,
    WRITE_EXPIRY_MARGIN,
    WRITE_SETTLE_WINDOW,
    port_key,
)

_LOGGER = logging.getLogger(__name__)


class AvProCoordinator(DataUpdateCoordinator[MatrixState]):
    """Polls one matrix through whichever transport it was given, and owns every write."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        transport: Transport,
        *,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=update_interval,
            # A quiet tick must notify nobody. MatrixState compares by value, so this is the
            # single largest defence against fanning noise out to every wall panel.
            always_update=False,
            # The default is cooldown=10, immediate=True. An immediate refresh would fire before
            # the matrix had applied the command, guaranteeing a stale read and a visible flick.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=WRITE_SETTLE_WINDOW, immediate=False
            ),
        )
        self.transport = transport
        self.pending = PendingWrites(hass.loop.time)

        #: How often a commanded value was never confirmed. Surfaced in diagnostics: a user seeing
        #: hundreds of these on one output learns instantly that something else owns it.
        self.overrides: Counter[str] = Counter()

        self._state = MatrixState()
        self._unavailable_logged = False
        self._cancel_expiry: Callable[[], None] | None = None
        self._unsubscribe: Callable[[], None] | None = None

    # -- lifecycle -----------------------------------------------------------------------

    async def async_prepare(self) -> None:
        """Subscribe to whatever the transport volunteers.

        Connecting is the selector's job, not this one's -- it has to connect in order to know
        whether telnet is even available, and connecting twice would take the socket, drop it and
        take it again.
        """
        self._unsubscribe = self.transport.subscribe(self._on_push)

    async def async_shutdown(self) -> None:
        """Release the transport and drop anything outstanding.

        A replayed optimistic value is a stale claim, and on telnet the socket must be handed back
        so another controller can have it.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None
        self.pending.clear()
        await self.transport.async_disconnect()
        await super().async_shutdown()

    # -- reading -------------------------------------------------------------------------

    async def _async_update_data(self) -> MatrixState:
        """Ask the transport for whatever is due, and fold it in."""
        try:
            report = (
                await self.transport.async_read_all()
                if not self._state.census_done
                else await self.transport.async_refresh()
            )
        except AvProConnectionError as err:
            self._note_unavailable(str(err))
            raise UpdateFailed(str(err)) from err
        except UnsupportedCommand as err:
            # Raised only for the hot endpoint; everything else degrades to a capability.
            self._note_unavailable(str(err))
            raise UpdateFailed(str(err)) from err

        self._unavailable_logged = False
        return self._absorb(report)

    @callback
    def _on_push(self, report: DeviceReport) -> None:
        """An unsolicited report from a transport that pushes.

        Treated exactly like a poll result. A change made by another control system, from the
        front panel, or from the device's web page all look identical from here: a value moved.
        """
        previous = self._state
        state = self._absorb(report)
        if state is not previous:
            self.async_set_updated_data(state)

    def _absorb(self, report: DeviceReport) -> MatrixState:
        """Apply a report, resolve the overlay against it, and return the new state."""
        self._state = st.apply(self._state, report)
        # Resolved after applying: the overlay must be compared against the reading that just
        # landed, not the one before it.
        self.pending.confirm(self.device_value)
        return self._state

    def _note_unavailable(self, detail: str) -> None:
        """Log an outage once, not once per tick.

        A matrix switched off overnight would otherwise write an identical line every five
        seconds until morning.
        """
        if not self._unavailable_logged:
            _LOGGER.warning("%s is unreachable: %s", self.transport.host, detail)
            self._unavailable_logged = True

    # -- state ---------------------------------------------------------------------------

    @property
    def matrix(self) -> MatrixState:
        """Device truth, without the optimistic overlay."""
        return self._state

    def device_value(self, key: str) -> Any:
        """The last polled value for a state key, or ``None`` if never reported."""
        return self._state.get(key)

    def optimistic(self, key: str) -> Any:
        """What an entity should report: a commanded value if outstanding, else the device."""
        return self.pending.get(key, self._state.get(key))

    def supports(self, kind: str) -> bool:
        """Whether the active transport can read this kind of value at all.

        Drives entity creation, so a wire that cannot see output stream state simply has no
        entity for it rather than one that reads unknown forever.
        """
        return self.transport.capabilities.can_read(kind)

    # -- writing -------------------------------------------------------------------------

    async def async_set(self, key: str, value: Any) -> None:
        """Command one value and show it immediately, pending confirmation by a later reading."""
        if self.optimistic(key) == value:
            # The "already there" guard. Without it, a scene setting four outputs to the same
            # input issues four writes on every activation.
            return

        await self._send(lambda: self.transport.async_command(key, value))

        self.pending.record(key, value, WRITE_SETTLE_WINDOW)
        self._arm_expiry()
        # Publish the optimistic value once. Deliberately not async_set_updated_data, which would
        # also mark the update successful and reschedule the poll timer, silently pushing every
        # poll out by a full interval on every write.
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_route_all(self, source: int) -> None:
        """Route every output to one input, in one request rather than one per output."""
        router = getattr(self.transport, "async_route_all", None)
        if router is None:
            for output in range(1, self._state.port_count + 1):
                await self.async_set(port_key(KEY_VIDEO_ROUTE, output), source)
            return

        await self._send(lambda: router(source))
        for output in range(1, self._state.port_count + 1):
            self.pending.record(port_key(KEY_VIDEO_ROUTE, output), source, WRITE_SETTLE_WINDOW)
        self._arm_expiry()
        self.async_update_listeners()
        await self.async_request_refresh()

    async def _send(self, action: Callable[[], Any]) -> None:
        """Run a write, translating transport failures into user-facing errors."""
        try:
            await action()
        except AvProWritesDisabled as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="writes_disabled"
            ) from err
        except UnsupportedCommand as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_unsupported"
            ) from err
        except AvProConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"host": self.transport.host},
            ) from err

    # -- the overlay's deadline ----------------------------------------------------------

    def _arm_expiry(self) -> None:
        if self._cancel_expiry is not None:
            self._cancel_expiry()
        self._cancel_expiry = async_call_later(
            self.hass, WRITE_SETTLE_WINDOW + WRITE_EXPIRY_MARGIN, self._sweep_expired
        )

    @callback
    def _sweep_expired(self, _now: Any) -> None:
        """Hand authority back to the device for anything never confirmed.

        ``@callback`` is required, not decorative: without it Home Assistant treats this as
        blocking and may run it in an executor thread, and ``async_update_listeners`` below then
        reaches ``async_write_ha_state`` off the event loop.
        """
        self._cancel_expiry = None
        expired = self.pending.expire()
        if not expired:
            return

        for key in sorted(expired):
            self.overrides[key] += 1
            _LOGGER.info(
                "%s: %s was commanded but the matrix reports %r; accepting the device. "
                "Something else may control this output",
                self.transport.host,
                key,
                self.device_value(key),
            )
        self.async_update_listeners()

        if self.pending:
            self._arm_expiry()

    # -- diagnostics ---------------------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        """A dump safe to paste into a public issue.

        Port names, the host, the MAC and the network body are site data and are deliberately
        absent. What is left is shapes and counts, which is what a bug report actually needs.
        """
        state = self._state
        report: dict[str, Any] = {
            "transport": self.transport.name,
            "pushes": self.transport.pushes,
            "model": state.model,
            "firmware": state.firmware,
            "port_count": state.port_count,
            "has_mac": state.mac is not None,
            "named_outputs": sum(1 for n in state.output_names if n),
            "named_inputs": sum(1 for n in state.input_names if n),
            "kinds_seen": sorted(state.seen),
            "readable": sorted(self.transport.capabilities.readable),
            "census_done": state.census_done,
            "pending_writes": sorted(self.pending.keys()),
            "writes_overridden_by_key": dict(self.overrides),
            "routing": {
                "video": list(state.video_routes),
                "audio": list(state.series("audio_route")),
                "bind_mode": state.bind_mode,
            },
            "signal_present": [bool(s) for s in state.signals],
        }
        if isinstance(self.transport, HttpTransport):
            report["http"] = {
                "tick": self.transport.tick,
                **self.transport.device_capabilities.as_diagnostics(),
            }
        return report
