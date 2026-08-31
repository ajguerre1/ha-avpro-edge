"""HTTP transport for the AVPro Edge CGI interface.

This is the only module in the package that holds a socket, a lock or a clock. It knows nothing
about what ``VidSta`` means -- it fetches bytes, hands them to :mod:`.protocol`, and returns the
parsed result.

**HTTP, never telnet.** The unit also listens on port 23, and the vendor's own control-system
driver uses it. That server accepts exactly one client at a time: four simultaneous connection
attempts produced one success and three timeouts. In a typical installation the single slot is
already held, persistently, by a third-party control system the site depends on. Opening it here
would take it away from that controller. Nothing in this package may import a telnet library or
connect to port 23, and ``tests/test_no_telnet.py`` enforces it.

Four measured behaviours of this firmware shape the transport:

* **A missing endpoint answers 200** with an HTML body, so status codes are not consulted for
  anything except "did the server reply at all".
* **The connection is closed after every response**, even when the request asks to keep it alive
  and even though no ``Connection: close`` header is sent back. Reusing a pooled connection
  therefore fails; ``Connection: close`` is sent so aiohttp does not pool one, and a
  ``ServerDisconnectedError`` is retried once for the case where it does anyway.
* **The content type is ``text/html;``** -- a trailing semicolon and no charset. ``resp.text()``
  would fall back to charset detection, which needs a library Home Assistant does not ship, so
  the body is read as bytes and decoded explicitly.
* **The server banner claims IIS/6.0 and ASP.NET.** It is canned by the OEM firmware and means
  nothing; never identify the device from it.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

import aiohttp

from . import protocol as p
from .protocol import CommandEndpoint, ParsedStatus, ParseOutcome, StatusEndpoint
from .transport import TransportError

_LOGGER = logging.getLogger(__name__)

#: Per-request timeout. Deliberately shorter than the 5 s poll tick so a slow reply can never let
#: two poll cycles overlap. The device answers in 9-12 ms on a healthy LAN, so this is ~300x the
#: observed latency and only fires on a genuinely wedged unit.
DEFAULT_TIMEOUT: float = 4.0

#: Sent on every request. The device closes the connection regardless; saying so up front stops
#: aiohttp from returning it to the pool and then failing on the next use.
_HEADERS = {"Connection": "close", "Accept": "*/*"}


class AvProError(Exception):
    """Base class for every error this client raises."""


class AvProConnectionError(AvProError, TransportError):
    """The unit could not be reached, timed out, or dropped the connection.

    Distinct from a parse failure: this means the transport failed, not that the device said
    something unexpected.

    Also a :class:`TransportError`, so the coordinator can catch every wire's connection failure
    without naming any of them. It already caught this one by name; telnet's equivalent was the
    one missing, and naming types individually is how that happened.
    """


class AvProWritesDisabled(AvProError):
    """A command was attempted while the client is in read-only mode."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of a write.

    The matrix does not acknowledge commands in any useful way -- a bogus endpoint returns 200
    just like a real one -- so the only signal worth extracting is whether the firmware said
    ``NO SUPPORT``. Everything else is confirmed later, by value, from a poll.
    """

    outcome: ParseOutcome
    raw: str

    @property
    def supported(self) -> bool:
        """False only when the device explicitly refused the command."""
        return self.outcome is not ParseOutcome.UNSUPPORTED


class AvProClient:
    """Talks to one matrix over HTTP.

    Every request -- read or write -- passes through a single lock. That is structural, not a
    convention: the device is a small embedded server that also serves its own web UI and shares
    the network with another control system, and a future contributor reaching for
    ``asyncio.gather`` to "speed up" the tier reads would otherwise be entirely reasonable. It
    also means a write can never interleave with the read-modify-write of a rename.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        allow_writes: bool = True,
        seed: str | None = None,
    ) -> None:
        """``session`` is injected so Home Assistant's shared session is reused.

        ``seed`` gives this client its own random stream. Never ``random.seed()``: that mutates
        global state for everything else running in the same process, and two matrices seeded
        alike would jitter in lockstep, which is the opposite of what jitter is for.
        """
        self._session = session
        self._host = host
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._lock = asyncio.Lock()
        self._rng = random.Random(seed if seed is not None else host)
        self.allow_writes = allow_writes

    @property
    def host(self) -> str:
        """The host this client talks to."""
        return self._host

    @property
    def lock(self) -> asyncio.Lock:
        """The transport lock.

        Exposed so a read-modify-write -- renaming one port when the device rewrites all eight in
        a single request -- can hold it across both halves. Without that, a poll can interleave
        and the write ends up derived from a stale read.
        """
        return self._lock

    def _url(self, path: str) -> str:
        return f"http://{self._host}{path}"

    def _cache_buster(self) -> str:
        """Reproduce the web UI's random query suffix."""
        return f"{self._rng.random():.17f}"

    async def _fetch(self, path: str) -> str:
        """Perform one GET and return the decoded body. Raises only on transport failure."""
        url = self._url(path)
        async with self._lock:
            for attempt in (1, 2):
                try:
                    async with self._session.get(
                        url, headers=_HEADERS, timeout=self._timeout
                    ) as response:
                        # The status code is read for logging only. This firmware answers 200
                        # for endpoints that do not exist, so it decides nothing.
                        raw = await response.read()
                        return raw.decode("ascii", errors="replace")
                except aiohttp.ServerDisconnectedError:
                    # The device closes every connection. If aiohttp handed us a pooled one it
                    # had already closed, the request never reached the wire, so re-sending is
                    # not a re-application -- it is the first attempt. Retried once only.
                    if attempt == 2:
                        raise AvProConnectionError(f"{self._host}: connection dropped") from None
                    _LOGGER.debug("%s: pooled connection was dead, retrying once", self._host)
                except TimeoutError as err:
                    raise AvProConnectionError(
                        f"{self._host}: no reply within {self._timeout.total}s"
                    ) from err
                except aiohttp.ClientError as err:
                    raise AvProConnectionError(f"{self._host}: {err}") from err
        raise AvProConnectionError(f"{self._host}: unreachable")  # pragma: no cover - defensive

    async def async_read(self, endpoint: StatusEndpoint) -> ParsedStatus:
        """Read one status endpoint.

        Never raises for a device-level problem: an absent endpoint, a ``NO SUPPORT`` and a
        malformed body all come back as outcomes, because a single odd endpoint must not be able
        to fail a whole update. Only a transport failure raises.
        """
        body = await self._fetch(p.status_path(endpoint, self._cache_buster()))
        parsed = p.parse_status(endpoint, body)
        if not parsed.ok:
            _LOGGER.debug(
                "%s: %s -> %s (%s)", self._host, endpoint.value, parsed.outcome, parsed.detail
            )
        return parsed

    async def async_command(self, endpoint: CommandEndpoint, button: str) -> CommandResult:
        """Send one command.

        Nothing here retries on a disagreeing poll. With another control system on the same
        matrix, "the value did not change, so send it again" is how two controllers re-assert
        their last command at each other indefinitely. The only retry is the transport-level one
        in :meth:`_fetch`, for a request that never reached the wire.
        """
        if not self.allow_writes:
            raise AvProWritesDisabled(
                f"{self._host}: writes are disabled for this configuration entry"
            )

        body = await self._fetch(p.command_path(endpoint, button, self._cache_buster()))
        text = body.strip()

        if p.NO_SUPPORT in text.upper():
            _LOGGER.warning(
                "%s: %s refused command %s (device reported NO SUPPORT)",
                self._host,
                endpoint.value,
                button,
            )
            return CommandResult(ParseOutcome.UNSUPPORTED, text)

        return CommandResult(ParseOutcome.OK, text)

    async def async_identify(self) -> ParsedStatus:
        """Read the identity endpoint.

        Used by the config flow to establish that the host is an AVPro matrix at all, before any
        entry is created.
        """
        return await self.async_read(StatusEndpoint.WEB)
