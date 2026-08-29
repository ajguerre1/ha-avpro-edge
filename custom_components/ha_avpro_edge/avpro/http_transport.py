"""The CGI interface, behind the common :class:`Transport` interface.

Wraps :class:`AvProClient` rather than replacing it: the client stays the low-level "fetch a URL,
hand the body to the parser" layer, and this adds the transport contract on top -- the tiered
poll schedule, capability tracking, and the mapping from a canonical state key to a command.

That mapping used to live in the coordinator. It belongs here: which endpoint sets a scaler mode
is a property of the HTTP interface, not of the matrix, and telnet answers it completely
differently.

This transport does not push. ``subscribe`` accepts a callback and never calls it, so the
coordinator needs no special case for the difference.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .capabilities import Capabilities
from .client import AvProClient
from .http_decode import HTTP_READABLE, HTTP_WRITABLE, decode
from .models import (
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
from .protocol import CommandEndpoint, ParseOutcome, StatusEndpoint
from .report import EMPTY, DeviceReport
from .schedule import PollSchedule
from .state import split_key
from .transport import TransportCapabilities

_LOGGER = logging.getLogger(__name__)

#: The one endpoint whose failure means the device genuinely cannot be seen. Everything else is
#: allowed to be absent on some firmware without failing an update.
HOT = StatusEndpoint.VIDEO


class UnsupportedCommand(Exception):
    """This transport cannot set that key, or the device refused it."""


class HttpTransport:
    """Polled, stateless, coexists with anything else on the network."""

    name = "http"
    pushes = False

    def __init__(self, client: AvProClient, *, port_count: int = 4) -> None:
        self._client = client
        self._schedule = PollSchedule()
        self._device = Capabilities()
        self._port_count = port_count

    @property
    def host(self) -> str:
        return self._client.host

    @property
    def capabilities(self) -> TransportCapabilities:
        """What this wire offers, less anything this firmware turned out to lack."""
        missing: set[str] = set()
        if not self._device.endpoint_available(StatusEndpoint.EDID):
            missing.add("edid")
        if not self._device.endpoint_available(StatusEndpoint.INFO):
            missing.add("signal")
        caps = TransportCapabilities(readable=HTTP_READABLE, writable=HTTP_WRITABLE, pushes=False)
        return caps.without(*missing) if missing else caps

    def set_port_count(self, count: int) -> None:
        """Told by the coordinator once routing has established the real width."""
        self._port_count = count

    # -- lifecycle -----------------------------------------------------------------------

    async def async_connect(self) -> None:
        """Nothing to do. HTTP holds no connection, which is exactly why it coexists."""

    async def async_disconnect(self) -> None:
        """Nothing to release."""

    def subscribe(self, on_report: Callable[[DeviceReport], None]) -> Callable[[], None]:
        """Accepted and never called. This wire cannot push."""
        return lambda: None

    # -- reading -------------------------------------------------------------------------

    async def async_read_all(self) -> DeviceReport:
        """Every endpoint, once, as a census."""
        report = EMPTY
        for endpoint in StatusEndpoint:
            report = report.merge(await self._read(endpoint))
        return DeviceReport(values=report.values, complete=bool(report.values))

    async def async_refresh(self) -> DeviceReport:
        """The endpoints due on this tick, per the tiered schedule."""
        report = EMPTY
        for endpoint in self._schedule.next_endpoints():
            report = report.merge(await self._read(endpoint))
        return report

    async def _read(self, endpoint: StatusEndpoint) -> DeviceReport:
        """Read one endpoint, recording what its answer says about this firmware.

        An absent or refused endpoint is a capability, not an error -- this firmware really does
        lack one, and treating that as failure would take every entity unavailable.
        """
        if not self._device.endpoint_available(endpoint):
            return EMPTY

        parsed = await self._client.async_read(endpoint)

        if parsed.outcome in (ParseOutcome.NOT_FOUND, ParseOutcome.UNSUPPORTED):
            _LOGGER.info(
                "%s is not available on this firmware (%s); entities needing it are not created",
                endpoint.value,
                parsed.outcome.value,
            )
            self._device = self._device.with_absent(endpoint)
            return EMPTY

        if not parsed.ok and endpoint is HOT:
            # Routing cannot be inferred from anything else, so this one really is a failure.
            raise UnsupportedCommand(f"{endpoint.value}: {parsed.detail}")

        report = decode(endpoint, parsed, port_count=self._port_count)

        # Routing establishes how wide this unit is, which every other decode depends on.
        if endpoint is HOT and report.values:
            width = max((index for _, index in map(split_key, report.values) if index), default=0)
            if width:
                self._port_count = width

        return report

    # -- writing -------------------------------------------------------------------------

    async def async_command(self, key: str, value: Any) -> None:
        """Set one canonical state key over the CGI interface."""
        endpoint, button = self._command_for(key, value)

        if not self._device.command_available(endpoint):
            raise UnsupportedCommand(f"{endpoint.value} was refused by this firmware")

        result = await self._client.async_command(endpoint, button)
        if not result.supported:
            self._device = self._device.with_unsupported(endpoint)
            raise UnsupportedCommand(f"{endpoint.value} answered NO SUPPORT")

        # So the confirming read looks at the thing that was just written rather than waiting for
        # a cold endpoint to come round, which can be twelve ticks away.
        self._schedule.promote(_STATUS_FOR[endpoint])

    def _command_for(self, key: str, value: Any) -> tuple[CommandEndpoint, str]:
        """Canonical key and value to the endpoint and button code that set it."""
        from . import protocol as p

        kind, index = split_key(key)

        if kind == "bind_mode":
            return CommandEndpoint.AUDIO, p.bind_mode(CODE_BY_BIND_MODE[BindMode(value)])

        if index is None:
            raise UnsupportedCommand(f"no HTTP command is defined for {key!r}")

        match kind:
            case "video_route":
                return CommandEndpoint.VIDEO, p.video_route(index, int(value))
            case "audio_route":
                return CommandEndpoint.AUDIO, p.audio_route(index, int(value))
            case "extracted_audio":
                return CommandEndpoint.AUDIO, p.extracted_audio(index, bool(value))
            case "audio_delay":
                return CommandEndpoint.AUDIO, p.audio_delay(
                    index, CODE_BY_AUDIO_DELAY[AudioDelay(value)]
                )
            case "scaler":
                return CommandEndpoint.SYSTEM, p.scaler_mode(
                    index, CODE_BY_SCALER_MODE[ScalerMode(value)]
                )
            case "image_enhancement":
                return CommandEndpoint.SYSTEM, p.image_enhancement(
                    index, CODE_BY_IMAGE_ENHANCEMENT[ImageEnhancement(value)]
                )
            case "test_pattern":
                return CommandEndpoint.SYSTEM, p.test_pattern(index, bool(value))
            case "edid":
                return CommandEndpoint.EDID, edid_command(str(value), index)
            case _:
                # stream, input_power, key_lock, lcd_timeout: telnet-only, by construction.
                raise UnsupportedCommand(f"the CGI interface cannot set {kind!r}")

    async def async_route_all(self, source: int) -> None:
        """Route every output in a single request rather than one per output."""
        from . import protocol as p

        result = await self._client.async_command(CommandEndpoint.VIDEO, p.video_route_all(source))
        if not result.supported:
            raise UnsupportedCommand("route-all answered NO SUPPORT")
        self._schedule.promote(StatusEndpoint.VIDEO)

    # -- diagnostics ---------------------------------------------------------------------

    @property
    def device_capabilities(self) -> Capabilities:
        """What this firmware turned out to lack. Surfaced in diagnostics."""
        return self._device

    @property
    def tick(self) -> int:
        return self._schedule.tick


#: Which status endpoint confirms a write to each command endpoint.
_STATUS_FOR = {
    CommandEndpoint.VIDEO: StatusEndpoint.VIDEO,
    CommandEndpoint.AUDIO: StatusEndpoint.AUDIO,
    CommandEndpoint.SYSTEM: StatusEndpoint.SYSTEM,
    CommandEndpoint.TMDS: StatusEndpoint.TMDS,
    CommandEndpoint.EDID: StatusEndpoint.EDID,
    CommandEndpoint.NAME: StatusEndpoint.WEB,
}
