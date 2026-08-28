"""The HTTP client, against the fake matrix over real sockets.

These exercise the transport end to end: real aiohttp, real TCP, real response headers. Every
fault the fake offers exists to prove one defence here.

``pytest-socket`` arrives with ``pytest-homeassistant-custom-component`` and blocks sockets for
the whole session in CI, so this module opts back in explicitly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from avpro.client import (
    AvProClient,
    AvProConnectionError,
    AvProWritesDisabled,
)
from avpro.protocol import CommandEndpoint, ParseOutcome, StatusEndpoint
from fake_avpro import FakeMatrix

pytestmark = pytest.mark.enable_socket


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


async def _client(session, fake: FakeMatrix, **kwargs) -> AvProClient:
    return AvProClient(session, fake.host, **kwargs)


# ---------------------------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------------------------


async def test_reads_every_status_endpoint(session) -> None:
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        for endpoint in StatusEndpoint:
            parsed = await client.async_read(endpoint)
            assert parsed.ok, f"{endpoint.value} -> {parsed.outcome} {parsed.detail}"


async def test_video_read_yields_the_models_routing(session) -> None:
    async with FakeMatrix() as fake:
        fake.state.video_routes = [3, 1, 4, 2]
        client = await _client(session, fake)
        parsed = await client.async_read(StatusEndpoint.VIDEO)
        assert parsed.fields == ("O1I3", "O2I1", "O3I4", "O4I2")


async def test_the_body_is_decoded_without_charset_detection(session) -> None:
    """The device sends ``Content-Type: text/html;`` -- no charset at all.

    ``resp.text()`` would fall back to charset detection, which needs a library Home Assistant
    does not ship. Reading bytes and decoding explicitly is what makes this work.
    """
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        parsed = await client.async_read(StatusEndpoint.WEB)
        assert parsed.fields[0] == "AC-MX44-AUHD"


# ---------------------------------------------------------------------------------------------
# The HTTP-200 trap
# ---------------------------------------------------------------------------------------------


async def test_an_absent_endpoint_reports_not_found_and_does_not_raise(session) -> None:
    """The whole point: this arrives as 200 with an HTML body."""
    async with FakeMatrix(faults={"tmds-404"}) as fake:
        client = await _client(session, fake)
        parsed = await client.async_read(StatusEndpoint.TMDS)
        assert parsed.outcome is ParseOutcome.NOT_FOUND


async def test_an_absent_endpoint_does_not_affect_the_others(session) -> None:
    async with FakeMatrix(faults={"tmds-404"}) as fake:
        client = await _client(session, fake)
        assert (await client.async_read(StatusEndpoint.TMDS)).outcome is ParseOutcome.NOT_FOUND
        assert (await client.async_read(StatusEndpoint.VIDEO)).ok


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("empty-body", ParseOutcome.MALFORMED),
        ("garbage", ParseOutcome.NOT_FOUND),
    ],
)
async def test_broken_bodies_are_values_not_exceptions(session, fault, expected) -> None:
    async with FakeMatrix(faults={fault}) as fake:
        client = await _client(session, fake)
        parsed = await client.async_read(StatusEndpoint.VIDEO)
        assert parsed.outcome is expected


async def test_a_truncated_body_never_yields_a_wrong_route(session) -> None:
    """Truncation mid-field leaves the Key= prefix intact, so the grammar still accepts it.

    That is fine and is not the layer that protects against it: a cut field simply stops being a
    recognisable route token, and the fold drops it. What must never happen is a *plausible but
    wrong* route surviving, which is what a positional parser would produce here.
    """
    from avpro import protocol as proto
    from avpro.state import MatrixState, fold_video

    async with FakeMatrix(faults={"truncated"}) as fake:
        fake.state.video_routes = [1, 2, 3, 4]
        client = await _client(session, fake)
        parsed = await client.async_read(StatusEndpoint.VIDEO)

        recognised = [t for t in parsed.fields if proto.parse_video_route(t) is not None]
        assert len(recognised) < 4  # the body really was cut

        folded = fold_video(MatrixState(), parsed)
        for output, source in enumerate(folded.video_routes, start=1):
            assert source in (None, fake.state.video_routes[output - 1])


# ---------------------------------------------------------------------------------------------
# Transport failures -- these DO raise
# ---------------------------------------------------------------------------------------------


async def test_an_unreachable_host_raises_a_connection_error(session) -> None:
    client = AvProClient(session, "127.0.0.1:1", timeout=2.0)
    with pytest.raises(AvProConnectionError):
        await client.async_read(StatusEndpoint.VIDEO)


async def test_a_slow_device_times_out_rather_than_wedging(session) -> None:
    # Only needs to outlast the client's timeout. Kept short because the fake's handler runs to
    # completion even after the client has given up, and teardown waits for it.
    async with FakeMatrix(faults={"slow"}, slow_seconds=0.8) as fake:
        client = await _client(session, fake, timeout=0.2)
        with pytest.raises(AvProConnectionError):
            await client.async_read(StatusEndpoint.VIDEO)


async def test_the_timeout_is_shorter_than_a_poll_tick(session) -> None:
    """A reply slower than the tick would let two poll cycles overlap."""
    from avpro.client import DEFAULT_TIMEOUT

    assert DEFAULT_TIMEOUT < 5.0


async def test_a_server_that_closes_every_connection_is_handled(session) -> None:
    """The real device does this on every request, without a Connection header."""
    async with FakeMatrix(faults={"keepalive-refused"}) as fake:
        client = await _client(session, fake)
        for _ in range(5):
            assert (await client.async_read(StatusEndpoint.VIDEO)).ok


# ---------------------------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------------------------


async def test_a_route_command_changes_the_device(session) -> None:
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        result = await client.async_command(CommandEndpoint.VIDEO, "O1I3")
        assert result.supported
        parsed = await client.async_read(StatusEndpoint.VIDEO)
        assert parsed.fields[0] == "O1I3"


async def test_route_all_uses_a_single_request(session) -> None:
    """One request instead of four is a real difference on a serialised transport."""
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        fake.requests.clear()
        await client.async_command(CommandEndpoint.VIDEO, "O5I2")
        assert len(fake.requests) == 1
        parsed = await client.async_read(StatusEndpoint.VIDEO)
        assert parsed.fields == ("O1I2", "O2I2", "O3I2", "O4I2")


async def test_no_support_is_reported_not_treated_as_success(session) -> None:
    async with FakeMatrix(faults={"no-support"}) as fake:
        client = await _client(session, fake)
        result = await client.async_command(CommandEndpoint.TMDS, "T1AON")
        assert not result.supported
        assert result.outcome is ParseOutcome.UNSUPPORTED


async def test_writes_can_be_disabled_entirely(session) -> None:
    """The read-only mode has to be structural, not a convention."""
    async with FakeMatrix() as fake:
        client = await _client(session, fake, allow_writes=False)
        with pytest.raises(AvProWritesDisabled):
            await client.async_command(CommandEndpoint.VIDEO, "O1I3")
        assert fake.requests == []  # nothing reached the device


async def test_a_disabled_client_can_still_read(session) -> None:
    async with FakeMatrix() as fake:
        client = await _client(session, fake, allow_writes=False)
        assert (await client.async_read(StatusEndpoint.VIDEO)).ok


# ---------------------------------------------------------------------------------------------
# Serialisation -- the lock is structural
# ---------------------------------------------------------------------------------------------


async def test_concurrent_requests_are_serialised(session) -> None:
    """The device is a small embedded server that also serves its own web UI.

    Gathering the tier reads would be an entirely reasonable-looking "optimisation", so the
    serialisation is a lock rather than a convention.
    """
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        # Measured by the fake at the wire. Counting inside the client would only show callers
        # queued on the lock, which proves nothing about what reaches the device.
        await asyncio.gather(*(client.async_read(e) for e in list(StatusEndpoint)[:5]))
        assert fake.concurrent_peak == 1
        assert len(fake.requests) == 5


async def test_a_write_cannot_interleave_with_a_read(session) -> None:
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        await asyncio.gather(
            client.async_read(StatusEndpoint.VIDEO),
            client.async_command(CommandEndpoint.VIDEO, "O1I3"),
            client.async_read(StatusEndpoint.AUDIO),
        )
        assert fake.concurrent_peak == 1


async def test_the_transport_lock_is_exposed_for_read_modify_write(session) -> None:
    """Renaming rewrites all eight names, so both halves must hold the same lock."""
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        assert isinstance(client.lock, asyncio.Lock)


# ---------------------------------------------------------------------------------------------
# Model-independence
# ---------------------------------------------------------------------------------------------


async def test_an_eight_port_unit_reports_eight_routes(session) -> None:
    async with FakeMatrix(faults={"other-model"}) as fake:
        client = await _client(session, fake)
        parsed = await client.async_read(StatusEndpoint.VIDEO)
        assert len(parsed.fields) == 8


async def test_a_unit_without_a_mac_still_reads(session) -> None:
    async with FakeMatrix(faults={"no-mac"}) as fake:
        client = await _client(session, fake)
        parsed = await client.async_read(StatusEndpoint.NETWORK)
        assert parsed.ok
        assert parsed.fields[0] == ""


# ---------------------------------------------------------------------------------------------
# Per-client randomness
# ---------------------------------------------------------------------------------------------


async def test_two_clients_produce_independent_cache_busters(session) -> None:
    """Never ``random.seed()``: it mutates global state for all of Home Assistant, and two
    instances seeded alike would jitter in lockstep."""
    async with FakeMatrix() as fake:
        a = AvProClient(session, fake.host, seed="a")
        b = AvProClient(session, fake.host, seed="b")
        assert a._cache_buster() != b._cache_buster()


async def test_a_clients_cache_buster_varies_between_requests(session) -> None:
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        assert client._cache_buster() != client._cache_buster()


# ---------------------------------------------------------------------------------------------
# The tripwire
# ---------------------------------------------------------------------------------------------


async def test_the_client_never_touches_the_telnet_port(session) -> None:
    """On real hardware that port has one slot and it belongs to the control system."""
    async with FakeMatrix() as fake:
        client = await _client(session, fake)
        for endpoint in StatusEndpoint:
            await client.async_read(endpoint)
        await client.async_command(CommandEndpoint.VIDEO, "O1I2")
        assert fake.telnet_connections == 0
