"""The polling coordinator, and every command semantic in the integration.

Entities stay thin: they read a value through :meth:`AvProCoordinator.optimistic` and ask this
class to change it. Nothing about the CGI protocol appears in a platform module.

Three behaviours here are load-bearing and easy to undo by accident.

**Failure is asymmetric.** Only the hot endpoint -- video routing -- can fail an update. A warm
or cold endpoint that is absent on this firmware, refused, or momentarily unreadable records what
was learned and lets the cycle succeed. Without that asymmetry a single missing tab takes every
entity in the integration unavailable, and this firmware really is missing one.

**Nothing is written unless it changed.** ``always_update=False`` plus a value-comparable
``MatrixState`` means a quiet poll notifies no listeners at all. On an installation driving
around fifty wall panels, every state write fans out to all of them.

**A write is never re-sent.** The overlay bridges the device's apply latency and then yields. If
the poll disagrees after the settle window, the device wins. Re-asserting would turn a
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
from .avpro.capabilities import Capabilities
from .avpro.client import AvProClient, AvProConnectionError, AvProWritesDisabled
from .avpro.models import (
    CODE_BY_AUDIO_DELAY,
    CODE_BY_BIND_MODE,
    CODE_BY_IMAGE_ENHANCEMENT,
    CODE_BY_SCALER_MODE,
    AudioDelay,
    BindMode,
    ImageEnhancement,
    ScalerMode,
    edid_command,
)
from .avpro.pending import PendingWrites
from .avpro.protocol import CommandEndpoint, ParsedStatus, ParseOutcome, StatusEndpoint
from .avpro.schedule import PollSchedule
from .avpro.state import MatrixState
from .const import (
    DOMAIN,
    KEY_AUDIO_DELAY,
    KEY_AUDIO_ROUTE,
    KEY_BIND_MODE,
    KEY_EDID,
    KEY_EXTRACTED_AUDIO,
    KEY_IMAGE_ENHANCEMENT,
    KEY_SCALER,
    KEY_TEST_PATTERN,
    KEY_VIDEO_ROUTE,
    WRITE_EXPIRY_MARGIN,
    WRITE_SETTLE_WINDOW,
    port_key,
)

_LOGGER = logging.getLogger(__name__)

#: The one endpoint whose failure means we genuinely cannot see the device.
_HOT = StatusEndpoint.VIDEO


class AvProCoordinator(DataUpdateCoordinator[MatrixState]):
    """Polls one matrix and owns every write."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AvProClient,
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
            # Waiting out the settle window means the confirming poll lands after the change.
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=WRITE_SETTLE_WINDOW,
                immediate=False,
            ),
        )
        self.client = client
        self.capabilities = Capabilities()
        self.schedule = PollSchedule()
        self.pending = PendingWrites(hass.loop.time)

        #: How often a commanded value was never confirmed. Surfaced in diagnostics: a user
        #: seeing hundreds of these on one output learns instantly that something else owns it,
        #: which no amount of log-reading conveys as clearly.
        self.overrides: Counter[str] = Counter()

        self._state = MatrixState()
        self._unavailable_logged = False
        self._cancel_expiry: Callable[[], None] | None = None

    # -- polling -------------------------------------------------------------------------

    async def _async_update_data(self) -> MatrixState:
        """Read the endpoints due this tick and fold them onto the current state."""
        state = self._state

        for endpoint in self.schedule.next_endpoints():
            if not self.capabilities.endpoint_available(endpoint):
                continue

            try:
                parsed = await self.client.async_read(endpoint)
            except AvProConnectionError as err:
                if endpoint is _HOT:
                    self._note_unavailable(str(err))
                    raise UpdateFailed(str(err)) from err
                _LOGGER.debug("%s unreadable this cycle, keeping previous values", endpoint.value)
                continue

            if parsed.outcome in (ParseOutcome.NOT_FOUND, ParseOutcome.UNSUPPORTED):
                # A capability, not an error. Recorded once; never asked for again.
                _LOGGER.info(
                    "%s is not available on this firmware (%s); its entities will not be created",
                    endpoint.value,
                    parsed.outcome.value,
                )
                self.capabilities = self.capabilities.with_absent(endpoint)
                continue

            if not parsed.ok and endpoint is _HOT:
                self._note_unavailable(parsed.detail)
                raise UpdateFailed(f"{endpoint.value}: {parsed.detail}")

            state = self._fold(state, endpoint, parsed)

        self._unavailable_logged = False
        # Set before resolving: device_value() reads self._state, so the overlay must be compared
        # against the poll that just landed, not the one before it.
        self._state = state
        self._resolve_pending()
        return state

    def _fold(
        self, state: MatrixState, endpoint: StatusEndpoint, parsed: ParsedStatus
    ) -> MatrixState:
        ports = state.port_count
        match endpoint:
            case StatusEndpoint.VIDEO:
                return st.fold_video(state, parsed)
            case StatusEndpoint.WEB:
                return st.fold_web(state, parsed, port_count=ports)
            case StatusEndpoint.AUDIO:
                return st.fold_audio(state, parsed, port_count=ports)
            case StatusEndpoint.SYSTEM:
                return st.fold_system(state, parsed, port_count=ports)
            case StatusEndpoint.INFO:
                return st.fold_info(state, parsed, port_count=ports)
            case StatusEndpoint.EDID:
                return st.fold_edid(state, parsed, port_count=ports)
            case StatusEndpoint.NETWORK:
                return st.fold_network(state, parsed)
            case _:
                return state

    def _note_unavailable(self, detail: str) -> None:
        """Log an outage once, not once per tick.

        A matrix that is off overnight would otherwise write an identical line every five
        seconds until morning.
        """
        if not self._unavailable_logged:
            _LOGGER.warning("%s is unreachable: %s", self.client.host, detail)
            self._unavailable_logged = True

    # -- reading -------------------------------------------------------------------------

    @property
    def matrix(self) -> MatrixState:
        """Device truth, without the optimistic overlay."""
        return self._state

    def optimistic(self, key: str) -> Any:
        """The value an entity should report: a commanded one if outstanding, else the device."""
        return self.pending.get(key, self.device_value(key))

    def device_value(self, key: str) -> Any:
        """The polled value for a state key, or ``None`` if this firmware never reported it."""
        kind, _, index = key.rpartition("_")
        state = self._state

        if kind == KEY_BIND_MODE:
            return state.bind_mode
        if not index.isdigit():
            return None

        position = int(index) - 1
        series: dict[str, tuple] = {
            KEY_VIDEO_ROUTE: state.video_routes,
            KEY_AUDIO_ROUTE: state.audio_routes,
            KEY_EXTRACTED_AUDIO: state.extracted_audio,
            KEY_AUDIO_DELAY: state.audio_delays,
            KEY_SCALER: state.scaler_modes,
            KEY_IMAGE_ENHANCEMENT: state.image_enhancements,
            KEY_TEST_PATTERN: state.test_patterns,
            KEY_EDID: state.edid,
        }
        values = series.get(kind)
        if values is None or not 0 <= position < len(values):
            return None
        return values[position]

    # -- writing -------------------------------------------------------------------------

    async def async_set(self, key: str, value: Any) -> None:
        """Command one value and show it immediately, pending confirmation by a later poll."""
        if self.optimistic(key) == value:
            # The "already there" guard. Without it, a scene that sets four outputs to the same
            # input issues four writes every time it is activated.
            return

        endpoint, button = self._command_for(key, value)

        if not self.capabilities.command_available(endpoint):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_unsupported"
            )

        try:
            result = await self.client.async_command(endpoint, button)
        except AvProWritesDisabled as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="writes_disabled"
            ) from err
        except AvProConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"host": self.client.host},
            ) from err

        if not result.supported:
            self.capabilities = self.capabilities.with_unsupported(endpoint)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_unsupported"
            )

        self.pending.record(key, value, WRITE_SETTLE_WINDOW)
        self.schedule.promote(self._status_for(endpoint))
        self._arm_expiry()

        # Publish the optimistic value once. Deliberately not async_set_updated_data, which would
        # also mark the update successful and reschedule the poll timer -- silently pushing every
        # poll out by a full interval on every write.
        self.async_update_listeners()

        await self.async_request_refresh()

    async def async_route_all(self, source: int) -> None:
        """Route every output to one input, in a single request rather than one per output."""
        from .avpro import protocol as p

        try:
            result = await self.client.async_command(
                CommandEndpoint.VIDEO, p.video_route_all(source)
            )
        except AvProWritesDisabled as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="writes_disabled"
            ) from err
        except AvProConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"host": self.client.host},
            ) from err

        if not result.supported:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_unsupported"
            )

        for output in range(1, self._state.port_count + 1):
            self.pending.record(port_key(KEY_VIDEO_ROUTE, output), source, WRITE_SETTLE_WINDOW)
        self.schedule.promote(StatusEndpoint.VIDEO)
        self._arm_expiry()
        self.async_update_listeners()
        await self.async_request_refresh()

    def _command_for(self, key: str, value: Any) -> tuple[CommandEndpoint, str]:
        """Map a state key and a value onto the endpoint and button code that set it."""
        from .avpro import protocol as p

        kind, _, index = key.rpartition("_")

        if kind == KEY_BIND_MODE:
            return CommandEndpoint.AUDIO, p.bind_mode(CODE_BY_BIND_MODE[BindMode(value)])

        port = int(index)
        match kind:
            case s if s == KEY_VIDEO_ROUTE:
                return CommandEndpoint.VIDEO, p.video_route(port, int(value))
            case s if s == KEY_AUDIO_ROUTE:
                return CommandEndpoint.AUDIO, p.audio_route(port, int(value))
            case s if s == KEY_EXTRACTED_AUDIO:
                return CommandEndpoint.AUDIO, p.extracted_audio(port, bool(value))
            case s if s == KEY_AUDIO_DELAY:
                return CommandEndpoint.AUDIO, p.audio_delay(
                    port, CODE_BY_AUDIO_DELAY[AudioDelay(value)]
                )
            case s if s == KEY_SCALER:
                return CommandEndpoint.SYSTEM, p.scaler_mode(
                    port, CODE_BY_SCALER_MODE[ScalerMode(value)]
                )
            case s if s == KEY_IMAGE_ENHANCEMENT:
                return CommandEndpoint.SYSTEM, p.image_enhancement(
                    port, CODE_BY_IMAGE_ENHANCEMENT[ImageEnhancement(value)]
                )
            case s if s == KEY_TEST_PATTERN:
                return CommandEndpoint.SYSTEM, p.test_pattern(port, bool(value))
            case s if s == KEY_EDID:
                # EDID is the one setting whose read and write vocabularies are identical, so the
                # token goes back out exactly as it came in.
                return CommandEndpoint.EDID, edid_command(str(value), port)
            case _:
                raise HomeAssistantError(f"No command is defined for {key!r}")

    @staticmethod
    def _status_for(endpoint: CommandEndpoint) -> StatusEndpoint:
        """Which status endpoint confirms a write to this command endpoint."""
        return {
            CommandEndpoint.VIDEO: StatusEndpoint.VIDEO,
            CommandEndpoint.AUDIO: StatusEndpoint.AUDIO,
            CommandEndpoint.SYSTEM: StatusEndpoint.SYSTEM,
            CommandEndpoint.TMDS: StatusEndpoint.TMDS,
            CommandEndpoint.EDID: StatusEndpoint.EDID,
            CommandEndpoint.NAME: StatusEndpoint.WEB,
        }[endpoint]

    # -- resolving the overlay -----------------------------------------------------------

    def _resolve_pending(self) -> None:
        """Confirm whatever the fresh poll agrees with.

        Disagreement deliberately clears nothing: inside the settle window the matrix may simply
        not have applied the command yet, and dropping the overlay on that reading is what makes
        an entity flick back to the old value and then forward again. Expiry, not this, is what
        hands authority back to the device.
        """
        self.pending.confirm(self.device_value)

    def _arm_expiry(self) -> None:
        """Schedule the sweep that hands authority back to the device.

        Separate from the poll because with ``always_update=False`` an override where the device
        value was already what ``MatrixState`` held produces no change and therefore no
        notification -- yet what the entity *reports* has to change, from the commanded value to
        device truth.
        """
        if self._cancel_expiry is not None:
            self._cancel_expiry()
        self._cancel_expiry = async_call_later(
            self.hass, WRITE_SETTLE_WINDOW + WRITE_EXPIRY_MARGIN, self._sweep_expired
        )

    @callback
    def _sweep_expired(self, _now: Any) -> None:
        """Hand authority back to the device for anything that never got confirmed.

        ``@callback`` is required, not decorative: without it Home Assistant treats this as a
        blocking function and may run it in an executor thread, and the
        ``async_update_listeners`` below then reaches ``async_write_ha_state`` off the event
        loop. Home Assistant's thread-safety check catches that and raises.
        """
        self._cancel_expiry = None
        expired = self.pending.expire()
        if not expired:
            return

        for key in sorted(expired):
            self.overrides[key] += 1
            _LOGGER.info(
                "%s: %s was commanded but the matrix reports %r; accepting the device. Something "
                "else may control this output",
                self.client.host,
                key,
                self.device_value(key),
            )
        self.async_update_listeners()

        if self.pending:
            self._arm_expiry()

    async def async_shutdown(self) -> None:
        """Drop anything outstanding; a replayed optimistic value is a stale claim."""
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None
        self.pending.clear()
        await super().async_shutdown()

    # -- diagnostics ---------------------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        """A dump that is safe to paste into a public issue.

        Port names, the host, the MAC and everything else in the network body are site data and
        are deliberately absent. What is left is shapes and counts, which is what a bug report
        actually needs.
        """
        state = self._state
        return {
            "model": state.model,
            "firmware": state.firmware,
            "port_count": state.port_count,
            "has_mac": state.mac is not None,
            "named_outputs": sum(1 for n in state.output_names if n),
            "named_inputs": sum(1 for n in state.input_names if n),
            "endpoints_seen": sorted(e.value for e in state.seen),
            "capabilities": self.capabilities.as_diagnostics(),
            "poll_tick": self.schedule.tick,
            "pending_writes": sorted(self.pending.keys()),
            "writes_overridden_by_key": dict(self.overrides),
            "routing": {
                "video": list(state.video_routes),
                "audio": list(state.audio_routes),
                "bind_mode": state.bind_mode,
            },
            "signal_present": [bool(s) for s in state.signals],
        }
