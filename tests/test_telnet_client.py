"""The telnet transport, against the fake matrix over real sockets (T-N7..T-N13).

Real asyncio streams, real TCP, the real line grammar. The fake serves both wires from one
in-memory model, so a telnet write is visible to an HTTP read -- which is what makes the fallback
path testable at all.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from avpro.models import AudioDelay, BindMode, ImageEnhancement, ScalerMode
from avpro.report import DeviceReport
from avpro.telnet_client import TelnetBusy, TelnetError, TelnetTransport
from fake_avpro import FakeMatrix

pytestmark = pytest.mark.enable_socket


async def _connected(fake: FakeMatrix, **kwargs) -> TelnetTransport:
    transport = TelnetTransport(f"127.0.0.1:{fake.telnet_port}", **kwargs)
    await transport.async_connect()
    return transport


# ---------------------------------------------------------------------------------------------
# T-N8 -- one command reads the whole device
# ---------------------------------------------------------------------------------------------


async def test_get_sta_returns_a_complete_census() -> None:
    async with FakeMatrix() as fake:
        transport = await _connected(fake)
        try:
            report = await transport.async_read_all()
        finally:
            await transport.async_disconnect()

    assert report.complete
    assert report.get("video_route_1") == 1
    assert report.get("bind_mode") is BindMode.MATRIX
    # The five kinds HTTP has no status endpoint for -- the reason telnet is primary.
    assert report.get("stream_1") is True
    assert report.get("input_power_1") is True
    assert report.get("key_lock") is False
    assert report.get("lcd_timeout") == 2
    assert report.get("address") == "00"


async def test_the_census_arrives_as_one_report_not_forty_five() -> None:
    """Lines are gathered until the stream quiets, so a multi-line dump is a single update."""
    async with FakeMatrix() as fake:
        transport = await _connected(fake)
        try:
            report = await transport.async_read_all()
        finally:
            await transport.async_disconnect()
    assert len(report.values) > 40


async def test_the_edid_index_decodes_to_the_same_option_key_as_http() -> None:
    async with FakeMatrix() as fake:
        transport = await _connected(fake)
        try:
            report = await transport.async_read_all()
        finally:
            await transport.async_disconnect()
    assert report.get("edid_1") == "user_1"  # telnet 30 == HTTP EDIDU1


# ---------------------------------------------------------------------------------------------
# T-N9 -- pushes
# ---------------------------------------------------------------------------------------------


async def test_a_change_is_pushed_and_names_only_what_moved() -> None:
    async with FakeMatrix() as fake:
        transport = await _connected(fake)
        received: list[DeviceReport] = []
        transport.subscribe(received.append)
        try:
            await transport.async_read_all()
            await transport.async_command("video_route_1", 3)
            await asyncio.sleep(0.3)
        finally:
            await transport.async_disconnect()

    pushes = [r for r in received if "video_route_1" in r]
    assert pushes, "no push arrived for the change"
    assert pushes[-1].get("video_route_1") == 3
    assert not pushes[-1].complete  # a push is partial, not a census
    assert set(pushes[-1].values) == {"video_route_1"}


async def test_unsubscribing_stops_the_callbacks() -> None:
    async with FakeMatrix() as fake:
        transport = await _connected(fake)
        received: list[DeviceReport] = []
        unsubscribe = transport.subscribe(received.append)
        unsubscribe()
        try:
            await transport.async_command("video_route_1", 2)
            await asyncio.sleep(0.3)
        finally:
            await transport.async_disconnect()
    assert received == []


# ---------------------------------------------------------------------------------------------
# T-N13 / the faults
# ---------------------------------------------------------------------------------------------


async def test_a_taken_socket_is_reported_as_busy_not_as_a_failure() -> None:
    """The real unit accepts the TCP connection and then says nothing while its slot is held.

    The distinction matters: busy means fall back to HTTP, failure means the device is not there.
    """
    async with FakeMatrix(faults={"telnet-busy"}) as fake:
        transport = TelnetTransport(f"127.0.0.1:{fake.telnet_port}")
        # Shorten the wait; the behaviour under test is which exception, not how long.
        import avpro.telnet_client as tc

        original, tc.CONNECT_TIMEOUT = tc.CONNECT_TIMEOUT, 0.5
        try:
            with pytest.raises(TelnetBusy):
                await transport.async_connect()
        finally:
            tc.CONNECT_TIMEOUT = original


async def test_nothing_listening_is_a_plain_error_not_busy() -> None:
    async with FakeMatrix(faults={"telnet-refused"}) as fake:
        assert fake.telnet_port is None
        transport = TelnetTransport("127.0.0.1:1")
        with pytest.raises(TelnetError) as caught:
            await transport.async_connect()
        assert not isinstance(caught.value, TelnetBusy)


async def test_a_dropped_idle_connection_is_noticed() -> None:
    """The client must not sit on a dead socket believing it is connected."""
    async with FakeMatrix(faults={"telnet-drops-idle"}) as fake:
        transport = await _connected(fake)
        try:
            await asyncio.sleep(2.5)  # past the fake's idle cutoff
            # The writer may not know the peer has gone -- TCP does not always say so until a
            # write fails. What must not happen is a read hanging forever on a dead socket.
            with pytest.raises(TelnetError):
                await transport.async_read_all()
        finally:
            await transport.async_disconnect()


async def test_a_garbled_line_does_not_corrupt_its_neighbours() -> None:
    async with FakeMatrix(faults={"telnet-garbled"}) as fake:
        transport = await _connected(fake)
        try:
            report = await transport.async_read_all()
        finally:
            await transport.async_disconnect()
    assert report.get("video_route_1") == 1
    assert report.get("stream_1") is True


async def test_a_missed_push_is_caught_by_the_periodic_read() -> None:
    """The safety net, and the reason push is an optimisation rather than the whole design."""
    async with FakeMatrix(faults={"telnet-no-push"}) as fake:
        transport = await _connected(fake)
        received: list[DeviceReport] = []
        transport.subscribe(received.append)
        try:
            await transport.async_read_all()
            await transport.async_command("video_route_1", 4)
            await asyncio.sleep(0.3)
            assert not [r for r in received if "video_route_1" in r], "fault did not suppress"

            # The safety net sees it anyway.
            catch_up = await transport.async_refresh()
        finally:
            await transport.async_disconnect()
    assert catch_up.get("video_route_1") == 4


# ---------------------------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------------------------


async def test_a_command_changes_the_device() -> None:
    async with FakeMatrix() as fake:
        transport = await _connected(fake)
        try:
            await transport.async_command("video_route_2", 4)
            await asyncio.sleep(0.2)
        finally:
            await transport.async_disconnect()
    assert fake.state.video_routes[1] == 4


async def test_the_controls_http_cannot_reach_are_writable_here() -> None:
    """Output stream and input power have no CGI status endpoint at all."""
    async with FakeMatrix() as fake:
        transport = await _connected(fake)
        try:
            await transport.async_command("stream_1", False)
            await transport.async_command("input_power_2", False)
            await asyncio.sleep(0.2)
            report = await transport.async_read_all()
        finally:
            await transport.async_disconnect()
    assert fake.state.stream[0] is False
    assert report.get("stream_1") is False
    assert report.get("input_power_2") is False


async def test_writes_can_be_disabled_entirely() -> None:
    async with FakeMatrix() as fake:
        transport = await _connected(fake, allow_writes=False)
        try:
            fake.telnet_commands.clear()
            with pytest.raises(TelnetError):
                await transport.async_command("video_route_1", 3)
            await asyncio.sleep(0.1)
        finally:
            await transport.async_disconnect()
    assert not [c for c in fake.telnet_commands if c.upper().startswith("SET")]


# ---------------------------------------------------------------------------------------------
# The command vocabulary -- pure, no socket needed
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("video_route_1", 3, "SET OUT1 VS IN3"),
        ("audio_route_2", 4, "SET OUT2 AS IN4"),
        ("extracted_audio_3", True, "SET OUT3 EXA EN"),
        ("extracted_audio_3", False, "SET OUT3 EXA DIS"),
        ("audio_delay_1", AudioDelay.MS_630, "SET OUT1 EXADL PH7"),
        ("bind_mode", BindMode.MATRIX, "SET EXAMX MODE2"),
        # No space before the digit. The device's spelling, not a typo.
        ("scaler_2", ScalerMode.DOWNSCALE_4K_TO_2K, "SET OUT2 VIDEO2"),
        ("image_enhancement_4", ImageEnhancement.STRONG, "SET OUT4 IMAGE ENH 3"),
        ("test_pattern_1", True, "SET OUT1 SGM EN"),
        ("stream_1", False, "SET OUT1 STREAM OFF"),
        ("input_power_3", True, "SET IN3 TMDS ON"),
        ("key_lock", True, "SET KEY LOCK ON"),
        ("lcd_timeout", 2, "SET LCD ON T2"),
        ("edid_1", "user_1", "SET IN1 EDID 30"),
        ("edid_2", "preset_1", "SET IN2 EDID 0"),
        # A different command shape, not an index.
        ("edid_3", "copy_output_2", "SET IN3 EDID CY OUT2"),
    ],
)
def test_command_vocabulary(key: str, value: object, expected: str) -> None:
    assert TelnetTransport.command_for(key, value) == expected


def test_a_key_this_wire_cannot_set_is_refused() -> None:
    with pytest.raises(TelnetError):
        TelnetTransport.command_for("output_name_1", "Kitchen")


# ---------------------------------------------------------------------------------------------
# Capabilities and backoff
# ---------------------------------------------------------------------------------------------


def test_telnet_reads_everything_http_can_and_five_kinds_more() -> None:
    from avpro.http_decode import HTTP_READABLE
    from avpro.telnet_client import TELNET_READABLE

    only_telnet = TELNET_READABLE - HTTP_READABLE
    assert only_telnet == {"stream", "input_power", "key_lock", "lcd_timeout", "address"}


def test_port_names_are_the_one_thing_this_wire_cannot_do() -> None:
    from avpro.telnet_client import TELNET_WRITABLE

    assert "output_name" not in TELNET_WRITABLE
    assert "input_name" not in TELNET_WRITABLE


def test_it_declares_that_it_pushes() -> None:
    assert TelnetTransport("127.0.0.1:1").capabilities.pushes is True


def test_backoff_climbs_and_is_jittered_per_client() -> None:
    """Never random.seed(): two matrices must not reconnect in lockstep."""
    a = TelnetTransport("127.0.0.1:1", seed="a")
    b = TelnetTransport("127.0.0.1:1", seed="b")
    first = [a.backoff_delay() for _ in range(4)]
    assert first == sorted(first)  # climbing
    assert a.backoff_delay() != b.backoff_delay()


async def test_backoff_resets_after_a_successful_connect() -> None:
    """Async, deliberately.

    An earlier version was a sync test calling ``asyncio.run``, which creates a loop and then
    **closes** it. Under pytest-homeassistant-custom-component the loop is session-managed, so
    closing it broke every test collected afterwards with "no current event loop" -- 59 errors,
    and only in CI, because that plugin is not installed on the development box.
    """
    async with FakeMatrix() as fake:
        transport = TelnetTransport(f"127.0.0.1:{fake.telnet_port}")
        for _ in range(5):
            transport.backoff_delay()
        await transport.async_connect()
        try:
            assert transport.backoff_delay() < 2.0  # back to the bottom of the ladder
        finally:
            await transport.async_disconnect()
