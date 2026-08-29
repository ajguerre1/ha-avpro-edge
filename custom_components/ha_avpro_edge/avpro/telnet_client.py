"""The telnet transport: a held connection, pushes, and the full command set.

The only module in this package permitted to open a socket. That permission is narrow and
deliberate -- see ``tests/test_transport_discipline.py``, which enforces both that nothing else
connects and that this does not connect when the user has asked for HTTP.

**Why the connection is held.** The device volunteers changes on this wire within ~300-400 ms of
anything touching the matrix, from any source. A connect-per-command design would give that up,
and push is most of what telnet is for. The socket is released on unload, on reload, and when the
transport is switched to HTTP.

**Why there is no request/response correlation.** The protocol offers none: replies and
unsolicited pushes share one stream, and the device appends routing dumps to command responses
(``GET ADDR`` came back with four route lines attached). So every inbound line goes to the same
parser and merges into a report, and a command is confirmed by its value arriving -- exactly as
over HTTP. Inventing correlation would be a shallow layer over something that does not need one.

**One client at a time.** Four simultaneous connections to the real unit produced one success and
three timeouts. Holding this socket means nothing else can have it, which is why the transport is
user-selectable and why the ``http`` setting is honoured absolutely.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable
from typing import Any, Final

from . import telnet_protocol as tp
from .models import (
    CODE_BY_AUDIO_DELAY,
    CODE_BY_BIND_MODE,
    CODE_BY_IMAGE_ENHANCEMENT,
    CODE_BY_SCALER_MODE,
    AudioDelay,
    BindMode,
    ImageEnhancement,
    ScalerMode,
)
from .report import DeviceReport
from .state import split_key
from .transport import TransportCapabilities

_LOGGER = logging.getLogger(__name__)

#: The control port. The one place in the codebase this number may appear as a target.
TELNET_PORT: Final = 23

#: Connect and census timeout. The unit answers ``GET STA`` in well under a second on a healthy
#: LAN; this only fires on a wedged or absent device.
CONNECT_TIMEOUT: Final = 10.0

#: How long a silent socket is tolerated before it is treated as dead. The device volunteers a
#: routing dump every 8-16 s unprompted, so a minute of nothing means the connection is gone even
#: though TCP has not noticed.
SILENCE_TIMEOUT: Final = 60.0

#: Reconnect backoff ladder, seconds. Jittered per client.
BACKOFF: Final = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

#: Commands are terminated with CRLF. The device requires the return; without it nothing happens
#: and nothing is reported, which is a confusing way to fail.
_EOL: Final = b"\r\n"

#: Everything this wire can read, which is every kind the integration models.
TELNET_READABLE: Final[frozenset[str]] = frozenset(
    {
        "video_route",
        "audio_route",
        "extracted_audio",
        "audio_delay",
        "bind_mode",
        "scaler",
        "image_enhancement",
        "test_pattern",
        "edid",
        "stream",
        "input_power",
        "key_lock",
        "lcd_timeout",
        "address",
        "mac",
    }
)

#: Port names are the one thing telnet cannot do -- there is no rename command at all. Everything
#: else readable is also writable, except the identity fields.
TELNET_WRITABLE: Final[frozenset[str]] = TELNET_READABLE - {"address", "mac"}


class TelnetError(Exception):
    """The connection failed, or the device refused a command."""


class TelnetBusy(TelnetError):
    """The single control socket is already taken by something else."""


class TelnetTransport:
    """Holds one connection to the matrix and speaks the ASCII command set."""

    name = "telnet"
    pushes = True

    def __init__(self, host: str, *, allow_writes: bool = True, seed: str | None = None) -> None:
        self._host = host
        self.allow_writes = allow_writes
        # Per-client stream. Never random.seed(), which mutates global state for everything else
        # in the process and would make two matrices reconnect in lockstep.
        self._rng = random.Random(seed if seed is not None else host)

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._subscribers: list[Callable[[DeviceReport], None]] = []
        self._census: asyncio.Future[DeviceReport] | None = None
        self._failures = 0

    # -- identity ------------------------------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            readable=TELNET_READABLE, writable=TELNET_WRITABLE, pushes=True
        )

    # -- lifecycle -----------------------------------------------------------------------

    async def async_connect(self) -> None:
        """Open the connection and start reading.

        Raises :class:`TelnetBusy` when the single slot is already held, which the caller uses to
        decide whether to fall back rather than treating it as a hard failure.
        """
        host, _, port = self._host.partition(":")
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port) if port else TELNET_PORT),
                timeout=CONNECT_TIMEOUT,
            )
        except TimeoutError as err:
            # A timeout here is what a taken slot looks like: the device accepts nothing while
            # another client holds it, so the connection never completes rather than being
            # refused. Distinguished from an outright refusal, which means nothing is listening.
            raise TelnetBusy(
                f"{self._host}: the control socket did not accept a connection within "
                f"{CONNECT_TIMEOUT}s -- another controller may be holding it"
            ) from err
        except OSError as err:
            raise TelnetError(f"{self._host}: {err}") from err

        self._failures = 0
        self._reader_task = asyncio.create_task(self._read_loop())

    async def async_disconnect(self) -> None:
        """Close the connection and hand the socket back."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None

        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._reader, self._writer = None, None

    def subscribe(self, on_report: Callable[[DeviceReport], None]) -> Callable[[], None]:
        """Register for unsolicited reports."""
        self._subscribers.append(on_report)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(on_report)

        return _unsubscribe

    def backoff_delay(self) -> float:
        """How long to wait before the next reconnect attempt, jittered."""
        step = BACKOFF[min(self._failures, len(BACKOFF) - 1)]
        self._failures += 1
        return step * (0.8 + 0.4 * self._rng.random())

    # -- reading -------------------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Feed every inbound line to the parser and dispatch what it yields.

        Replies and pushes are indistinguishable on this wire and are deliberately not
        distinguished. A block of lines is gathered until the stream goes quiet, then parsed as
        one report -- which is what makes a multi-line ``GET STA`` arrive as a single census
        rather than forty-five separate updates.
        """
        assert self._reader is not None
        buffer: list[str] = []

        while True:
            try:
                line = await asyncio.wait_for(
                    self._reader.readline(), timeout=0.25 if buffer else SILENCE_TIMEOUT
                )
            except TimeoutError:
                if buffer:
                    self._dispatch(buffer)
                    buffer = []
                    continue
                # Nothing at all for a minute, on a device that volunteers routing every 8-16 s.
                _LOGGER.debug(
                    "%s: telnet silent for %ss, treating as dead", self._host, SILENCE_TIMEOUT
                )
                self._fail_census(TelnetError(f"{self._host}: connection went silent"))
                return
            except (OSError, asyncio.IncompleteReadError) as err:
                self._fail_census(TelnetError(f"{self._host}: {err}"))
                return

            if not line:  # EOF
                if buffer:
                    self._dispatch(buffer)
                self._fail_census(TelnetError(f"{self._host}: connection closed by the device"))
                return

            buffer.append(line.decode("ascii", errors="replace"))

    def _dispatch(self, lines: list[str]) -> None:
        """Parse a gathered block and hand it to whoever is waiting."""
        text = "".join(lines)
        report = tp.parse_lines(text)
        if not report:
            return

        # A block carrying the fields only GET STA produces is the census. The device frames
        # nothing, so this is the only way to recognise it.
        if tp.looks_like_census(report):
            report = DeviceReport(values=report.values, complete=True)
            if self._census is not None and not self._census.done():
                self._census.set_result(report)
                return

        for subscriber in list(self._subscribers):
            subscriber(report)

    def _fail_census(self, error: Exception) -> None:
        if self._census is not None and not self._census.done():
            self._census.set_exception(error)

    # -- the transport contract ----------------------------------------------------------

    async def async_read_all(self) -> DeviceReport:
        """``GET STA`` -- the whole device in one command."""
        if not self.connected:
            raise TelnetError(f"{self._host}: not connected")

        loop = asyncio.get_running_loop()
        self._census = loop.create_future()
        try:
            await self._send("GET STA")
            return await asyncio.wait_for(self._census, timeout=CONNECT_TIMEOUT)
        except TimeoutError as err:
            raise TelnetError(f"{self._host}: no reply to GET STA") from err
        finally:
            self._census = None

    async def async_refresh(self) -> DeviceReport:
        """The periodic safety net.

        Pushes carry normal operation; this exists only so a missed one cannot leave state stale
        indefinitely. It is a ``GET STA`` on the open session -- never an HTTP poll, which would
        be two transports doing one transport's job.
        """
        return await self.async_read_all()

    async def async_command(self, key: str, value: Any) -> None:
        """Set one canonical state key."""
        if not self.allow_writes:
            raise TelnetError(f"{self._host}: writes are disabled for this configuration entry")
        await self._send(self.command_for(key, value))

    async def _send(self, command: str) -> None:
        """Write one command. Serialised, because the device has one input queue."""
        async with self._lock:
            if self._writer is None or self._writer.is_closing():
                raise TelnetError(f"{self._host}: not connected")
            try:
                self._writer.write(command.encode("ascii") + _EOL)
                await self._writer.drain()
            except OSError as err:
                raise TelnetError(f"{self._host}: {err}") from err

    # -- the command vocabulary ----------------------------------------------------------

    @staticmethod
    def command_for(key: str, value: Any) -> str:
        """Canonical key and value to an ASCII command.

        Spacing is the device's and is inconsistent: ``SET OUTx VIDEOy`` has no space before the
        digit while ``SET OUTx IMAGE ENH y`` does. Both are verbatim from the unit's own help.
        """
        kind, index = split_key(key)

        match kind:
            case "key_lock":
                return f"SET KEY LOCK {'ON' if value else 'OFF'}"
            case "lcd_timeout":
                return f"SET LCD ON T{int(value)}"
            case "bind_mode":
                return f"SET EXAMX MODE{CODE_BY_BIND_MODE[BindMode(value)]}"

        if index is None:
            raise TelnetError(f"no telnet command is defined for {key!r}")

        match kind:
            case "video_route":
                return f"SET OUT{index} VS IN{int(value)}"
            case "audio_route":
                return f"SET OUT{index} AS IN{int(value)}"
            case "extracted_audio":
                return f"SET OUT{index} EXA {'EN' if value else 'DIS'}"
            case "audio_delay":
                return f"SET OUT{index} EXADL PH{CODE_BY_AUDIO_DELAY[AudioDelay(value)]}"
            case "scaler":
                # No space before the digit. The device's spelling, not a typo.
                return f"SET OUT{index} VIDEO{CODE_BY_SCALER_MODE[ScalerMode(value)]}"
            case "image_enhancement":
                code = CODE_BY_IMAGE_ENHANCEMENT[ImageEnhancement(value)]
                return f"SET OUT{index} IMAGE ENH {code}"
            case "test_pattern":
                return f"SET OUT{index} SGM {'EN' if value else 'DIS'}"
            case "stream":
                return f"SET OUT{index} STREAM {'ON' if value else 'OFF'}"
            case "input_power":
                return f"SET IN{index} TMDS {'ON' if value else 'OFF'}"
            case "edid":
                option = str(value)
                if option.startswith("copy_output_"):
                    # A different command shape, not an index.
                    return f"SET IN{index} EDID CY OUT{option.removeprefix('copy_output_')}"
                preset = tp.edid_index(option)
                if preset is None:
                    raise TelnetError(f"{option!r} is not an EDID this transport can set")
                return f"SET IN{index} EDID {preset}"
            case _:
                raise TelnetError(f"no telnet command is defined for {key!r}")


# Deliberately no async_route_all here.
#
# The help documents `SET OUTx VS INy` with x=0 meaning ALL, which would make routing every output
# a single command. But the matching GET form (`GET OUT0 STREAM`) was verified to return nothing
# on this firmware, so 0-as-ALL is not reliably implemented and has not been tested for SET
# against hardware. The coordinator falls back to one command per output, which is correct if
# less elegant. The optimisation goes in after a live probe confirms it, not before.
_ROUTE_ALL_UNVERIFIED = True
