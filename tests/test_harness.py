"""The fake device itself.

A fault nothing asserts on is dead weight; a defence with no fault behind it is a claim rather
than a test. These keep the two in step.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp
import pytest
from avpro.protocol import StatusEndpoint, expect_fields, parse_status

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from fake_avpro import FAULTS, FakeMatrix

pytestmark = pytest.mark.enable_socket


async def _get(fake: FakeMatrix, path: str) -> str:
    """Fetch one path from the fake and return its decoded body."""
    async with (
        aiohttp.ClientSession() as session,
        session.get(f"http://{fake.host}/{path}") as response,
    ):
        return (await response.read()).decode("ascii")


async def _get_response(fake: FakeMatrix, path: str) -> tuple[int, str, str | None, str]:
    """Fetch one path and return ``(status, content_type, charset, body)``."""
    async with (
        aiohttp.ClientSession() as session,
        session.get(f"http://{fake.host}/{path}") as response,
    ):
        body = (await response.read()).decode("ascii")
        return response.status, response.headers["Content-Type"], response.charset, body


def test_every_fault_says_which_defence_it_proves() -> None:
    for name, why in FAULTS.items():
        assert len(why) > 40, f"fault {name!r} needs a real explanation, not a label"
        assert "Proves" in why or "proves" in why, f"fault {name!r} does not say what it proves"


def test_every_fault_is_exercised_by_a_test() -> None:
    """Keeps the harness honest: a fault nobody uses is a maintenance liability."""
    suite = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "tests").rglob("test_*.py")
    )
    unused = [name for name in FAULTS if f'"{name}"' not in suite]
    assert not unused, f"faults defined but never exercised: {sorted(unused)}"


def test_an_unknown_fault_is_rejected_loudly() -> None:
    with pytest.raises(ValueError, match="unknown fault"):
        FakeMatrix(faults={"not-a-real-fault"})


async def test_the_default_fake_serves_exactly_what_the_real_firmware_does() -> None:
    """The default is a model of **this** matrix, not of an idealised one.

    This used to assert that every endpoint is served, which was a comfortable thing to believe
    and not true of the hardware: V1.41 has no TMDS tab. The fake served it anyway, so every test
    that did not opt into a fault was exercising a device that does not exist.

    Named rather than skipped, so a firmware that gains the tab shows up as a failure here and
    gets a deliberate decision rather than a silent one.
    """
    absent = {StatusEndpoint.TMDS}

    async with FakeMatrix() as fake:
        for endpoint in StatusEndpoint:
            parsed = parse_status(endpoint, await _get(fake, endpoint.value))
            if endpoint in absent:
                assert not parsed.ok, f"{endpoint.value} is absent on V1.41 but the fake served it"
            else:
                assert parsed.ok, endpoint.value


async def test_the_absent_tab_can_be_turned_on_for_a_firmware_that_has_it() -> None:
    async with FakeMatrix(faults={"tmds-present"}) as fake:
        assert parse_status(StatusEndpoint.TMDS, await _get(fake, StatusEndpoint.TMDS.value)).ok


async def test_an_unknown_path_gets_the_firmwares_200_and_html() -> None:
    """The behaviour the whole parser is defensive about, reproduced faithfully."""
    async with FakeMatrix() as fake:
        status, _content_type, _charset, body = await _get_response(fake, "does-not-exist.CGI")
    assert status == 200
    assert "was not found" in body


async def test_the_content_type_reproduces_the_firmwares_missing_charset() -> None:
    """``text/html;`` with a trailing semicolon and no charset is what makes resp.text() unsafe."""
    async with FakeMatrix() as fake:
        _status, content_type, charset, _body = await _get_response(fake, "VIDDivSta.CGI")
    assert content_type == "text/html;"
    assert charset is None


async def test_the_telnet_tripwire_notices_a_connection() -> None:
    """Proves the trap works, so ``telnet_connections == 0`` elsewhere means something."""
    async with FakeMatrix() as fake:
        assert fake.telnet_connections == 0
        _reader, writer = await asyncio.open_connection("127.0.0.1", fake.tripwire_port)
        writer.close()
        await asyncio.sleep(0.05)
        assert fake.telnet_connections == 1


async def test_the_external_change_fault_really_changes_things() -> None:
    async with FakeMatrix(faults={"external-change"}) as fake:
        before = list(fake.state.video_routes)
        await asyncio.sleep(0.5)
        assert fake.state.video_routes != before


async def test_the_slow_apply_fault_delays_the_visible_result() -> None:
    """The fault the pending overlay exists for."""
    async with FakeMatrix(faults={"slow-apply"}, slow_apply_seconds=0.4) as fake:
        await _get(fake, "TimSendCmd.CGI?button=O1I4")
        assert fake.state.video_routes[0] == 1  # not yet
        await asyncio.sleep(0.6)
        assert fake.state.video_routes[0] == 4  # now


async def test_the_never_apply_fault_silently_drops_writes() -> None:
    async with FakeMatrix(faults={"never-apply"}) as fake:
        await _get(fake, "TimSendCmd.CGI?button=O1I4")
        assert fake.state.video_routes[0] == 1


async def test_the_amp_in_name_fault_produces_a_shifted_body() -> None:
    async with FakeMatrix(faults={"amp-in-name"}) as fake:
        body = await _get(fake, "WEBDivSta.CGI")
    assert not expect_fields(parse_status(StatusEndpoint.WEB, body), 10).ok


async def test_the_no_trailing_amp_fault_still_yields_four_fields() -> None:
    """The real device ends INFDivSta.CGI with a trailing '&'. Both shapes must parse alike."""

    async def _signal_fields(faults: set[str]) -> tuple[str, ...]:
        async with FakeMatrix(faults=faults) as fake:
            body = await _get(fake, "INFDivSta.CGI")
        return parse_status(StatusEndpoint.INFO, body).fields

    with_trailing = await _signal_fields(set())
    without_trailing = await _signal_fields({"no-trailing-amp"})
    assert len(with_trailing) == len(without_trailing) == 4
    assert with_trailing == without_trailing


async def test_the_other_model_fault_reshapes_the_unit() -> None:
    async with FakeMatrix(faults={"other-model"}) as fake:
        assert fake.state.ports == 8
        assert len(fake.state.video_routes) == 8
