"""A fake AVPro Edge matrix that speaks the real CGI protocol, with fault injection.

Run it standalone::

    python tools/fake_avpro.py --port 8080
    python tools/fake_avpro.py --list-faults

or drive it from a test::

    async with FakeMatrix(faults={"tmds-404"}) as fake:
        client = AvProClient(session, fake.host)

Built on ``aiohttp.web``, which the vendored client already depends on, so the fake costs no
extra test dependency.

**Every value here is invented.** A capture from a real unit carries the owner's room names and
source labels in every status body, so no real capture may become a fixture.

Each fault exists to prove one specific defence. A fault with nothing asserting on it is dead
weight; a defence with no fault behind it is a claim, not a test.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

_LOGGER = logging.getLogger(__name__)

#: What each fault is for. Printed by --list-faults and asserted on in tests/test_harness.py, so
#: a fault cannot be added without saying which defence it exercises.
FAULTS: dict[str, str] = {
    "tmds-404": (
        "TMDSDivSta.CGI serves the HTML 'not found' body with status 200. Proves an absent "
        "endpoint is recorded as a capability and does not fail the update."
    ),
    "no-support": (
        "Every command answers NO SUPPORT. Proves the refusal is recognised rather than parsed "
        "as state."
    ),
    "slow-apply": (
        "A write is accepted but is invisible in the status body for a while. Proves the pending "
        "overlay bridges apply latency instead of clearing on the first stale read."
    ),
    "never-apply": (
        "Writes are accepted and silently dropped. Proves the overlay expires, the device wins, "
        "and nothing is re-sent."
    ),
    "external-change": (
        "A background task re-routes output 1 on its own. Simulates a second control system; "
        "proves out-of-band changes are picked up and overrides are counted."
    ),
    "amp-in-name": (
        "A port name contains '&'. Proves the arity guard refuses the response rather than "
        "shifting every name one position."
    ),
    "no-trailing-amp": (
        "INFDivSta.CGI omits its trailing '&'. Proves both shapes yield the same field count."
    ),
    "keepalive-refused": (
        "The socket is closed after every response without a Connection header. Proves the "
        "client does not depend on connection reuse."
    ),
    "truncated": (
        "Bodies are cut mid-field. The Key= prefix survives, so the grammar still accepts the "
        "body -- this proves the cut field stops being a recognisable token and is dropped, "
        "rather than yielding a plausible but wrong route the way a positional parser would."
    ),
    "garbage": "Bodies are an unrelated HTML page. Proves the Key= prefix check rejects them.",
    "empty-body": (
        "Bodies are empty with status 200, which embedded servers do under load. Proves an "
        "empty body is MALFORMED rather than an endpoint with no fields."
    ),
    "slow": "Responses are delayed past the client timeout. Proves the timeout fires.",
    "no-mac": (
        "NETDivSta.CGI reports no MAC. Proves the config flow falls back to a host-derived "
        "unique id instead of refusing a working unit."
    ),
    "other-model": (
        "Reports an 8x8 model with eight routes. Proves port counts are derived from the device "
        "rather than hardcoded."
    ),
}

_NOT_FOUND_BODY = "<HTML>\n<BODY>\nSorry, the page you requested was not found.\n</BODY>\n</HTML>\n"


@dataclass
class MatrixModel:
    """The fake unit's mutable state. Invented values only."""

    model: str = "AC-MX44-AUHD"
    firmware: str = "V1.41"
    mac: str = "AA:BB:CC:DD:EE:FF"
    ports: int = 4
    output_names: list[str] = field(default_factory=lambda: ["OutA", "OutB", "OutC", "OutD"])
    input_names: list[str] = field(default_factory=lambda: ["SrcA", "SrcB", "SrcC", "SrcD"])
    video_routes: list[int] = field(default_factory=lambda: [1, 2, 3, 4])
    audio_routes: list[int] = field(default_factory=lambda: [1, 2, 3, 4])
    extracted_audio: list[bool] = field(default_factory=lambda: [False, True, False, True])
    audio_delay: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    bind_mode: int = 2
    scaler: list[int] = field(default_factory=lambda: [1, 1, 1, 1])
    enhancement: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    test_pattern: list[bool] = field(default_factory=lambda: [False] * 4)
    edid: list[str] = field(default_factory=lambda: ["EDIDU1"] * 4)
    signals: list[str] = field(
        default_factory=lambda: ["3840X2160P@60HZ YUV420", "1920X1080P@60HZ", "", "1920X1080P@60HZ"]
    )

    def widen(self, ports: int, model: str) -> None:
        """Reshape as a larger member of the family, for the ``other-model`` fault."""
        self.model = model
        self.ports = ports
        self.output_names = [f"Out{i}" for i in range(1, ports + 1)]
        self.input_names = [f"Src{i}" for i in range(1, ports + 1)]
        self.video_routes = [((i % ports) + 1) for i in range(ports)]
        self.audio_routes = list(self.video_routes)
        self.extracted_audio = [False] * ports
        self.audio_delay = [0] * ports
        self.scaler = [1] * ports
        self.enhancement = [0] * ports
        self.test_pattern = [False] * ports
        self.edid = ["EDIDU1"] * ports
        self.signals = [""] * ports


class FakeMatrix:
    """An aiohttp app serving the CGI protocol, usable as an async context manager."""

    def __init__(
        self,
        *,
        faults: set[str] | None = None,
        slow_apply_seconds: float = 2.0,
        slow_seconds: float = 10.0,
        telnet_tripwire: bool = True,
    ) -> None:
        unknown = (faults or set()) - set(FAULTS)
        if unknown:
            raise ValueError(f"unknown fault(s): {sorted(unknown)}")

        self.faults = faults or set()
        self.state = MatrixModel()
        self.slow_apply_seconds = slow_apply_seconds
        self.slow_seconds = slow_seconds
        self.telnet_tripwire = telnet_tripwire

        #: Every request path served, in order. Tests assert on request *counts* to prove the
        #: poll cadence and that a write issues exactly one request.
        self.requests: list[str] = []
        #: Set if anything ever connected to the telnet tripwire. See ``_start_tripwire``.
        self.telnet_connections = 0
        #: Highest number of requests in flight at once, measured at the wire. A client whose
        #: transport lock works keeps this at 1 no matter how many callers gather on it.
        self.concurrent_peak = 0
        self._in_flight = 0

        self._runner: web.AppRunner | None = None
        self._tripwire: asyncio.Server | None = None
        self._port = 0
        self._pending_apply: list[asyncio.Task[Any]] = []

        if "other-model" in self.faults:
            self.state.widen(8, "AC-MX88-AUHD")
        if "amp-in-name" in self.faults:
            self.state.output_names[0] = "Bar & Grill"

    # -- lifecycle -----------------------------------------------------------------------

    @property
    def port(self) -> int:
        """The ephemeral port the fake bound to."""
        return self._port

    @property
    def host(self) -> str:
        """``host:port``, ready to hand to :class:`AvProClient`."""
        return f"127.0.0.1:{self._port}"

    async def __aenter__(self) -> FakeMatrix:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self._port = site._server.sockets[0].getsockname()[1]

        if self.telnet_tripwire:
            await self._start_tripwire()

        if "external-change" in self.faults:
            self._pending_apply.append(asyncio.create_task(self._external_changes()))

    async def stop(self) -> None:
        for task in self._pending_apply:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._pending_apply.clear()
        if self._tripwire is not None:
            self._tripwire.close()
            await self._tripwire.wait_closed()
            self._tripwire = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _start_tripwire(self) -> None:
        """Listen on an ephemeral port standing in for telnet and record any connection.

        This is a trap, not a fault. The integration must never open a telnet session, because
        on real hardware that port has one slot and it belongs to the house's control system.
        A unit test can assert ``fake.telnet_connections == 0``; ``tests/test_no_telnet.py``
        makes the stronger, static guarantee.
        """

        async def _on_connect(_reader: Any, writer: Any) -> None:
            self.telnet_connections += 1
            _LOGGER.error("TRIPWIRE: something connected to the telnet port")
            writer.close()

        self._tripwire = await asyncio.start_server(_on_connect, "127.0.0.1", 0)

    @property
    def tripwire_port(self) -> int | None:
        """The port the telnet tripwire is listening on, if enabled."""
        if self._tripwire is None:
            return None
        return self._tripwire.sockets[0].getsockname()[1]

    # -- request handling ----------------------------------------------------------------

    async def _handle(self, request: web.Request) -> web.Response:
        path = request.path.lstrip("/")
        self.requests.append(path)

        # Measured at the wire, which is the only place that proves the client's lock actually
        # serialises. Instrumenting the client's own methods would count callers queued *on* the
        # lock, not requests in flight through it.
        self._in_flight += 1
        self.concurrent_peak = max(self.concurrent_peak, self._in_flight)
        try:
            return await self._respond_to(path, request)
        finally:
            self._in_flight -= 1

    async def _respond_to(self, path: str, request: web.Request) -> web.Response:
        if "slow" in self.faults:
            await asyncio.sleep(self.slow_seconds)

        body = self._body_for(path, request)
        if body is None:
            return self._respond(_NOT_FOUND_BODY)

        if "empty-body" in self.faults and path.endswith("DivSta.CGI"):
            return self._respond("")
        if "garbage" in self.faults and path.endswith("DivSta.CGI"):
            return self._respond("<html><body><h1>Login</h1></body></html>")
        if "truncated" in self.faults and path.endswith("DivSta.CGI"):
            return self._respond(body[: len(body) // 2])

        return self._respond(body)

    def _respond(self, body: str) -> web.Response:
        # Content type reproduced verbatim from the real firmware: trailing semicolon, no
        # charset. That is exactly what makes resp.text() unsafe against this device.
        response = web.Response(body=body.encode("ascii", "replace"))
        response.headers["Content-Type"] = "text/html;"
        response.headers["Cache-control"] = "private"
        response.headers["Server"] = "Microsoft-IIS/6.0"
        response.headers["X-Powered-By"] = "ASP.NET"
        if "keepalive-refused" in self.faults:
            response.force_close()
        return response

    def _body_for(self, path: str, request: web.Request) -> str | None:
        st = self.state
        n = st.ports

        match path:
            case "WEBDivSta.CGI":
                names = "&".join([*st.output_names, *st.input_names])
                return f"WebSta={st.model}&{st.firmware}&{names}"

            case "VIDDivSta.CGI":
                return "VidSta=" + "&".join(f"O{i + 1}I{st.video_routes[i]}" for i in range(n))

            case "AUDDivSta.CGI":
                delays = [f"O{i + 1}D{st.audio_delay[i]}" for i in range(n)]
                enables = [f"O{i + 1}A{'ON' if st.extracted_audio[i] else 'OFF'}" for i in range(n)]
                routes = [f"AO{i + 1}I{st.audio_routes[i]}" for i in range(n)]
                return "AudSta=" + "&".join([*delays, *enables, f"AMB{st.bind_mode}", *routes])

            case "SYSDivSta.CGI":
                enh = [f"O{i + 1}E{st.enhancement[i]}" for i in range(n)]
                scal = [f"O{i + 1}V{st.scaler[i]}" for i in range(n)]
                sgm = [f"O{i + 1}SGM{'ON' if st.test_pattern[i] else 'OFF'}" for i in range(n)]
                return "SysSta=" + "&".join([*enh, *scal, *sgm])

            case "INFDivSta.CGI":
                joined = "&".join(st.signals)
                # The real device emits a trailing '&'; the fault drops it.
                return f"INFSta={joined}" + ("" if "no-trailing-amp" in self.faults else "&")

            case "EDIDDivSta.CGI":
                return "EdidSta=" + "&".join(st.edid)

            case "NETDivSta.CGI":
                mac = "" if "no-mac" in self.faults else st.mac
                names = "&".join([*st.output_names, *st.input_names])
                return f"NetSta={mac}&10.0.0.1&255.255.255.0&10.0.0.254&23&STATICIP&{names}"

            case "TMDSDivSta.CGI":
                # Absent on the firmware this was developed against.
                return None if "tmds-404" in self.faults else "TmdsSta=T1AON&T2AON&T3AON&T4AON"

            case _ if path.endswith("SendCmd.CGI"):
                return self._apply(path, request.query.get("button", ""))

        return None

    # -- writes --------------------------------------------------------------------------

    def _apply(self, path: str, button: str) -> str:
        """Apply a command to the model, honouring the write-related faults."""
        if "no-support" in self.faults:
            return "NO SUPPORT"

        # The web UI appends '+<random>' to the button value and the device tolerates it, so its
        # parser evidently matches a leading prefix and ignores the tail.
        #
        # Split on '+' *or* whitespace, because a URL query decoder turns '+' into a space and it
        # is not established whether this firmware decodes at all. Accepting both shapes means
        # the fake models the device under either answer, instead of baking in a guess.
        code = re.split(r"[+\s]", button.strip(), maxsplit=1)[0]

        if "never-apply" in self.faults:
            return ""
        if "slow-apply" in self.faults:
            self._pending_apply.append(asyncio.create_task(self._apply_later(code)))
            return ""

        self._mutate(code)
        return ""

    async def _apply_later(self, code: str) -> None:
        await asyncio.sleep(self.slow_apply_seconds)
        self._mutate(code)

    def _mutate(self, code: str) -> None:
        """Interpret a command code against the model. Unknown codes are ignored."""
        st = self.state
        n = st.ports

        if m := re.fullmatch(r"O(\d+)I(\d+)", code):
            out, src = int(m[1]), int(m[2])
            if out == 5:  # the all-outputs form
                st.video_routes = [src] * n
            elif 1 <= out <= n:
                st.video_routes[out - 1] = src
        elif m := re.fullmatch(r"AO(\d+)I(\d+)", code):
            if 1 <= int(m[1]) <= n:
                st.audio_routes[int(m[1]) - 1] = int(m[2])
        elif m := re.fullmatch(r"O(\d+)A(ON|OFF)", code):
            if 1 <= int(m[1]) <= n:
                st.extracted_audio[int(m[1]) - 1] = m[2] == "ON"
        elif m := re.fullmatch(r"O(\d+)D(\d+)", code):
            if 1 <= int(m[1]) <= n:
                st.audio_delay[int(m[1]) - 1] = int(m[2])
        elif m := re.fullmatch(r"AMB(\d+)", code):
            st.bind_mode = int(m[1])
        elif m := re.fullmatch(r"O(\d+)V(\d+)", code):
            if 1 <= int(m[1]) <= n:
                st.scaler[int(m[1]) - 1] = int(m[2])
        elif m := re.fullmatch(r"O(\d+)E(\d+)", code):
            if 1 <= int(m[1]) <= n:
                st.enhancement[int(m[1]) - 1] = int(m[2])
        elif m := re.fullmatch(r"O(\d+)SGM(ON|OFF)", code):
            if 1 <= int(m[1]) <= n:
                st.test_pattern[int(m[1]) - 1] = m[2] == "ON"

    async def _external_changes(self) -> None:
        """Re-route output 1 periodically, as a second control system would."""
        while True:
            await asyncio.sleep(0.3)
            st = self.state
            st.video_routes[0] = (st.video_routes[0] % st.ports) + 1


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--fault", action="append", default=[], choices=sorted(FAULTS))
    parser.add_argument("--list-faults", action="store_true")
    args = parser.parse_args()

    if args.list_faults:
        width = max(len(name) for name in FAULTS)
        for name, why in sorted(FAULTS.items()):
            print(f"{name:<{width}}  {why}")
        return

    logging.basicConfig(level=logging.INFO)

    async def run() -> None:
        fake = FakeMatrix(faults=set(args.fault))
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", fake._handle)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", args.port).start()
        print(f"fake AVPro matrix on http://127.0.0.1:{args.port}  faults={sorted(fake.faults)}")
        await asyncio.Event().wait()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    _main()
