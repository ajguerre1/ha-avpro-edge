"""The signal supplement (T-E5): telnet, plus the one thing it cannot read.

Against the fake matrix over real sockets on both wires at once, which is exactly the arrangement
this class creates in production.

The premise was established on hardware rather than assumed. Two probe rounds tried 32 command
spellings for a telnet signal query -- ``GET IN1 SIG``, ``SIGNAL``, ``STA``, ``HDMI``, ``RES``,
``5V``, ``HPD``, ``CONNECT``, ``ACTIVE``, ``LINK``, and the ``OUT``/bare variants -- and every one
answered ``CMD ERR`` against known-good controls (``GET IN1 EDID`` -> ``IN1 EDID 30``,
``GET OUT1 STREAM`` -> ``OUT1 STREAM ON``). ``GET STA`` carries no signal line either: 50 lines,
of which the 6 the grammar drops are all network configuration.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from avpro.client import AvProClient
from avpro.supplement import SupplementedTransport
from avpro.telnet_client import TelnetTransport
from fake_avpro import FakeMatrix

pytestmark = pytest.mark.enable_socket


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


async def _supplemented(session, fake: FakeMatrix, *, interval: float = 3600) -> tuple:
    """A connected supplement. The default interval is long enough never to fire on its own."""
    telnet = TelnetTransport(f"127.0.0.1:{fake.telnet_port}")
    transport = SupplementedTransport(telnet, AvProClient(session, fake.host), interval=interval)
    await transport.async_connect()
    return transport, telnet


# ---------------------------------------------------------------------------------------------
# The gap it exists to close
# ---------------------------------------------------------------------------------------------


async def test_telnet_alone_cannot_read_signal(session) -> None:
    """The premise, asserted rather than trusted.

    If a future firmware starts reporting signal over telnet, this fails and the whole class can
    be deleted -- which is the outcome worth noticing.
    """
    async with FakeMatrix() as fake:
        telnet = TelnetTransport(f"127.0.0.1:{fake.telnet_port}")
        await telnet.async_connect()
        try:
            assert not telnet.capabilities.can_read("signal")
            census = await telnet.async_read_all()
            assert not [key for key in census.values if key.startswith("signal_")]
        finally:
            await telnet.async_disconnect()


async def test_the_supplement_makes_signal_readable(session) -> None:
    """T-E5. The capability is what drives entity creation, so it has to change too."""
    async with FakeMatrix() as fake:
        transport, _ = await _supplemented(session, fake)
        try:
            assert transport.capabilities.can_read("signal")
            # Still not writable: no endpoint on either wire sets a measurement.
            assert not transport.capabilities.can_write("signal")
        finally:
            await transport.async_disconnect()


async def test_the_census_carries_signal(session) -> None:
    """T-E5. Signal has to arrive with the census, not after it.

    Entities are created from the census. Signal arriving a tick later would mean the capability
    check ran before it was known, and the entities would never exist.
    """
    async with FakeMatrix() as fake:
        transport, _ = await _supplemented(session, fake)
        try:
            census = await transport.async_read_all()
            assert census.complete is True
            signals = {k: v for k, v in census.values.items() if k.startswith("signal_")}
            assert len(signals) == 4, f"expected four signal keys, got {signals}"
            # And everything telnet contributes is still there.
            assert census.values["video_route_1"] == 1
            assert census.values["stream_1"] is True
        finally:
            await transport.async_disconnect()


# ---------------------------------------------------------------------------------------------
# It must not make anything worse
# ---------------------------------------------------------------------------------------------


async def test_a_failing_signal_read_does_not_break_the_census(session) -> None:
    """A supplement that could fail an update would make the transport less reliable than before.

    The web server being unreachable must not take routing unavailable, which telnet is reporting
    perfectly well.
    """
    async with FakeMatrix() as fake:
        telnet = TelnetTransport(f"127.0.0.1:{fake.telnet_port}")
        # A client pointed at a closed port: reachable device, dead HTTP.
        transport = SupplementedTransport(
            telnet, AvProClient(session, "127.0.0.1:1"), interval=3600
        )
        await transport.async_connect()
        try:
            census = await transport.async_read_all()
            assert census.complete is True
            assert census.values["video_route_1"] == 1, "telnet's contribution was lost"
            assert not [k for k in census.values if k.startswith("signal_")]
        finally:
            await transport.async_disconnect()


async def test_commands_go_to_the_primary_never_over_http(session) -> None:
    """The supplement reads. Writing is the primary's job, always."""
    async with FakeMatrix() as fake:
        transport, _ = await _supplemented(session, fake)
        try:
            fake.requests.clear()
            await transport.async_command("video_route_1", 3)
            await asyncio.sleep(0.3)

            assert fake.state.video_routes[0] == 3
            assert fake.requests == [], f"a command leaked onto HTTP: {fake.requests}"
            assert any("SET OUT1 VS IN3" in c.upper() for c in fake.telnet_commands)
        finally:
            await transport.async_disconnect()


async def test_the_refresh_picks_up_a_signal_change(session) -> None:
    """A refresh means "read whatever is due", and signal is due on every one.

    The first version of this class left signal out of ``async_refresh``, on the reasoning that a
    timer already polls it. That made the timer the only path to a fresh reading: an explicit
    refresh returned stale signal, and the 60 s safety net stopped covering the one field that
    never pushes. CI caught it as an output still reporting "on" after every source went dark.
    """
    async with FakeMatrix() as fake:
        transport, _ = await _supplemented(session, fake)
        try:
            census = await transport.async_read_all()
            assert census.values["signal_1"], "the fake should start with a live signal"

            fake.state.signals = ["", "", "", ""]
            report = await transport.async_refresh()

            assert not report.values["signal_1"], "the refresh returned a stale signal"
            # And it is still the primary that answers for everything telnet owns.
            assert report.values["video_route_1"] == 1
        finally:
            await transport.async_disconnect()


async def test_the_refresh_reads_signal_and_nothing_else_over_http(session) -> None:
    """One endpoint. Anything else would be the hedging the transport rule forbids."""
    async with FakeMatrix() as fake:
        transport, _ = await _supplemented(session, fake)
        try:
            fake.requests.clear()
            await transport.async_refresh()
            endpoints = {r.split("?")[0] for r in fake.requests}
            assert endpoints == {"INFDivSta.CGI"}, f"unexpected HTTP traffic: {fake.requests}"
        finally:
            await transport.async_disconnect()


# ---------------------------------------------------------------------------------------------
# The timer
# ---------------------------------------------------------------------------------------------


async def test_signal_is_polled_on_its_own_interval_and_pushed(session) -> None:
    """T-E5. Signal does not push, so the supplement polls it and pushes the result itself.

    Delivered through the same subscriber path telnet uses, so the coordinator handles it exactly
    like any other push and needs no knowledge that a second wire exists.
    """
    async with FakeMatrix() as fake:
        transport, _ = await _supplemented(session, fake, interval=0.2)
        received: list[dict] = []
        transport.subscribe(lambda report: received.append(dict(report.values)))
        try:
            for _ in range(30):
                await asyncio.sleep(0.1)
                if any(k.startswith("signal_") for r in received for k in r):
                    break

            signal_reports = [r for r in received if any(k.startswith("signal_") for k in r)]
            assert signal_reports, "the signal poll never delivered anything"
            assert all(r for r in signal_reports), "an empty report was dispatched"
        finally:
            await transport.async_disconnect()


async def test_a_telnet_push_still_reaches_subscribers(session) -> None:
    """The supplement wraps the primary, so it must not swallow what the primary volunteers."""
    async with FakeMatrix() as fake:
        transport, _ = await _supplemented(session, fake)
        received: list[dict] = []
        transport.subscribe(lambda report: received.append(dict(report.values)))
        try:
            await fake.push_telnet("OUT1 VS IN4\r\n")
            for _ in range(30):
                await asyncio.sleep(0.1)
                if any(r.get("video_route_1") == 4 for r in received):
                    break
            assert any(r.get("video_route_1") == 4 for r in received), (
                "a telnet push did not reach subscribers through the supplement"
            )
        finally:
            await transport.async_disconnect()


async def test_disconnect_stops_the_poll_and_releases_the_socket(session) -> None:
    """A timer surviving disconnect would keep polling a device nobody is watching."""
    async with FakeMatrix() as fake:
        transport, telnet = await _supplemented(session, fake, interval=0.1)
        assert transport.connected

        await transport.async_disconnect()
        assert not transport.connected
        assert not telnet.connected

        fake.requests.clear()
        await asyncio.sleep(0.4)
        assert fake.requests == [], "the signal poll outlived the connection"


async def test_it_presents_itself_as_the_primary(session) -> None:
    """Diagnostics and the interval logic both branch on this; it must say telnet."""
    async with FakeMatrix() as fake:
        transport, telnet = await _supplemented(session, fake)
        try:
            assert transport.name == "telnet"
            assert transport.pushes is True
            assert transport.host == telnet.host
        finally:
            await transport.async_disconnect()
