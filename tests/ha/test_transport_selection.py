"""Transport selection, fallback, and the discipline rule (T-X1..T-X3, T-D1..T-D6).

**Telnet is primary. Always speak telnet unless you don't need to.**

The three assertions declared as strict xfail when the guard was inverted are implemented here.
The middle one is the one that encodes the rule: while telnet is connected, no HTTP request is
issued for anything telnet supports. Hedging by running both wires would double the device load
and leave two sources of truth.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.ha_avpro_edge.const import (
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORT_HTTP,
    TRANSPORT_TELNET,
)

from .conftest import make_entry

pytestmark = pytest.mark.enable_socket


async def _setup(hass: HomeAssistant, fake, **options):
    entry = make_entry(fake.host, telnet_port=fake.telnet_port, **options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------------------------
# T-D2 -- telnet is chosen when it will have us
# ---------------------------------------------------------------------------------------------


async def test_telnet_is_used_by_default(hass: HomeAssistant, fake) -> None:
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.transport.name == "telnet"
    assert coordinator.transport.pushes is True


async def test_the_telnet_only_controls_appear(hass: HomeAssistant, fake) -> None:
    """The five kinds HTTP has no status endpoint for."""
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator
    for kind in ("stream", "input_power", "key_lock", "lcd_timeout"):
        assert coordinator.supports(kind), f"{kind} should be readable over telnet"


# ---------------------------------------------------------------------------------------------
# T-X1 -- the escape hatch, honoured absolutely
# ---------------------------------------------------------------------------------------------


async def test_nothing_connects_to_the_control_socket_under_the_http_setting(
    hass: HomeAssistant, fake
) -> None:
    """S8. The assertion that must never be relaxed.

    An installation whose control system needs that socket has to be able to say so and be
    obeyed -- not mostly obeyed, not obeyed after a failed attempt.
    """
    entry = await _setup(hass, fake, **{CONF_TRANSPORT: TRANSPORT_HTTP})

    assert entry.runtime_data.coordinator.transport.name == "http"
    assert fake.telnet_connections == 0, "something opened the control socket"


async def test_the_telnet_only_entities_are_absent_under_http(hass: HomeAssistant, fake) -> None:
    """T-D5. An entity that can never read is worse than no entity: it reads unknown forever."""
    entry = await _setup(hass, fake, **{CONF_TRANSPORT: TRANSPORT_HTTP})
    coordinator = entry.runtime_data.coordinator
    for kind in ("stream", "input_power", "key_lock", "lcd_timeout"):
        assert not coordinator.supports(kind)


# ---------------------------------------------------------------------------------------------
# T-X2 -- the rule itself
# ---------------------------------------------------------------------------------------------


#: The only HTTP endpoints that may be touched while telnet is connected, each because telnet
#: genuinely cannot do it:
#:
#: * ``WEBDivSta`` / ``NETDivSta`` -- model, firmware and the port names, read once at setup;
#: * ``INFDivSta`` -- signal detection, which 32 probed telnet command spellings could not read.
#:
#: Naming the set is stronger than the old ``== []``. That assertion only said "no HTTP"; this one
#: says which HTTP, so a future routing or EDID read over the wrong wire fails loudly instead of
#: being waved through by a relaxed guard.
HTTP_ALLOWED_UNDER_TELNET = {"WEBDivSta.CGI", "NETDivSta.CGI", "INFDivSta.CGI"}


async def test_no_http_request_is_issued_for_anything_telnet_supports(
    hass: HomeAssistant, fake
) -> None:
    """Always speak telnet unless you don't need to.

    Running HTTP alongside a healthy telnet session would be hedging: two transports doing one
    transport's job, double the device load, two sources of truth to reconcile.

    "Unless you don't need to" is doing real work in that sentence. Signal is a thing telnet
    cannot read at all -- established against the live matrix, not assumed -- so reading it over
    HTTP is not hedging. Exactly one wire can produce that value, which is the same situation as
    renaming a port. What must never appear here is routing, audio, scaler, EDID or stream: every
    one of those telnet does support, and a request for one would mean two sources of truth.
    """
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.transport.name == "telnet"

    fake.requests.clear()

    # A full cycle of everything the integration does routinely.
    await coordinator.async_refresh()
    await coordinator.async_set("video_route_1", 3)
    await coordinator.async_set("stream_2", False)
    await hass.async_block_till_done()

    offenders = [r for r in fake.requests if r.split("?")[0] not in HTTP_ALLOWED_UNDER_TELNET]
    assert not offenders, f"HTTP was used for something telnet supports: {offenders}"


async def test_not_one_command_is_sent_over_http_while_telnet_is_connected(
    hass: HomeAssistant, fake
) -> None:
    """The supplement reads. It must never write.

    Signal is a measurement with no setter on either wire, so any command endpoint appearing here
    means a write escaped onto the wrong transport.
    """
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator

    fake.requests.clear()
    await coordinator.async_set("video_route_1", 3)
    await coordinator.async_set("key_lock", True)
    await hass.async_block_till_done()

    commands = [r for r in fake.requests if "SendCmd" in r or "sendCmd" in r]
    assert not commands, f"a command went over HTTP while telnet was connected: {commands}"


async def test_identity_is_the_only_http_read_and_it_happens_once(
    hass: HomeAssistant, fake
) -> None:
    """The documented exception, bounded.

    Telnet cannot report model, firmware or the port names -- GET STA covers routing and settings
    and stops there. Reading them over HTTP is the same exception that covers renaming: an
    operation only HTTP has. It must happen at setup and never again, or it becomes the hedging
    the rule forbids.

    Signal is the one other read on this list, and unlike identity it is genuinely periodic -- a
    source waking up is a change, not a fact fixed at setup. It is bounded by cadence rather than
    by count, which the interval test below covers.
    """
    await _setup(hass, fake)

    reads = [r.split("?")[0] for r in fake.requests]
    assert set(reads) <= HTTP_ALLOWED_UNDER_TELNET
    assert reads.count("WEBDivSta.CGI") == 1, "identity was read more than once"
    assert reads.count("NETDivSta.CGI") == 1


async def test_the_port_names_survive_on_telnet(hass: HomeAssistant, fake) -> None:
    """Without the identity read the picker would show "Input 1" where the matrix says SrcA."""
    await _setup(hass, fake)
    state = hass.states.get("media_player.ac_mx44_auhd_output_1")
    assert state.attributes["source_list"] == ["SrcA", "SrcB", "SrcC", "SrcD"]


async def test_the_safety_net_read_is_telnet_not_an_http_poll(hass: HomeAssistant, fake) -> None:
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator

    fake.requests.clear()
    fake.telnet_commands.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Filtered rather than compared to empty: the signal supplement runs on its own timer, and a
    # test that happened to straddle one of its ticks would fail for a reason that has nothing to
    # do with what it is asserting.
    offenders = [r for r in fake.requests if r.split("?")[0] not in HTTP_ALLOWED_UNDER_TELNET]
    assert not offenders
    assert any(c.upper() == "GET STA" for c in fake.telnet_commands)


# ---------------------------------------------------------------------------------------------
# T-D3 / T-D4 -- fallback
# ---------------------------------------------------------------------------------------------


async def test_a_held_control_socket_falls_back_to_http(hass: HomeAssistant) -> None:
    """The device is there; it is simply not ours to talk to. Better polling than nothing."""
    from fake_avpro import FakeMatrix

    import custom_components.ha_avpro_edge.avpro.telnet_client as tc

    async with FakeMatrix(faults={"telnet-busy"}) as fake:
        original, tc.CONNECT_TIMEOUT = tc.CONNECT_TIMEOUT, 0.5
        try:
            entry = await _setup(hass, fake)
        finally:
            tc.CONNECT_TIMEOUT = original

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.coordinator.transport.name == "http"


async def test_requiring_telnet_means_not_ready_rather_than_silently_degrading(
    hass: HomeAssistant,
) -> None:
    """Falling back would hide the very thing the user asked for."""
    from fake_avpro import FakeMatrix

    import custom_components.ha_avpro_edge.avpro.telnet_client as tc

    async with FakeMatrix(faults={"telnet-busy"}) as fake:
        original, tc.CONNECT_TIMEOUT = tc.CONNECT_TIMEOUT, 0.5
        try:
            entry = make_entry(
                fake.host, telnet_port=fake.telnet_port, **{CONF_TRANSPORT: TRANSPORT_TELNET}
            )
            entry.add_to_hass(hass)
            assert not await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        finally:
            tc.CONNECT_TIMEOUT = original

    assert entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------------------------
# Falling back is a fault, not an accommodation
# ---------------------------------------------------------------------------------------------
#
# The substantive consequence of Home Assistant being the only thing driving this matrix. While
# another control system legitimately owned the socket, degrading quietly to HTTP was the correct
# answer and a single log line was proportionate. With nothing else on the device, an unavailable
# control socket is the only thing that should raise an alarm -- and nobody reads logs they have
# no reason to open.


def _issues(hass: HomeAssistant) -> list[str]:
    registry = ir.async_get(hass)
    return [issue_id for (domain, issue_id) in registry.issues if domain == DOMAIN]


async def test_an_unexpected_fallback_raises_a_repair_issue(hass: HomeAssistant) -> None:
    from fake_avpro import FakeMatrix

    import custom_components.ha_avpro_edge.avpro.telnet_client as tc

    async with FakeMatrix(faults={"telnet-busy"}) as fake:
        original, tc.CONNECT_TIMEOUT = tc.CONNECT_TIMEOUT, 0.5
        try:
            entry = await _setup(hass, fake)
        finally:
            tc.CONNECT_TIMEOUT = original

        assert entry.runtime_data.coordinator.transport.name == "http"
        assert _issues(hass) == [f"telnet_unavailable_{entry.entry_id}"]


async def test_choosing_http_deliberately_is_not_reported_as_a_fault(
    hass: HomeAssistant, fake
) -> None:
    """A repair issue for a configuration someone chose is noise.

    And noise is how the useful ones come to be ignored.
    """
    await _setup(hass, fake, **{CONF_TRANSPORT: TRANSPORT_HTTP})
    assert _issues(hass) == []


async def test_a_healthy_telnet_session_raises_nothing(hass: HomeAssistant, fake) -> None:
    await _setup(hass, fake)
    assert _issues(hass) == []


async def test_recovering_onto_telnet_clears_the_issue(hass: HomeAssistant, fake) -> None:
    """The issue must not outlive the fault it describes.

    A repair notice that has to be dismissed by hand once the problem has fixed itself trains
    people to dismiss them all.
    """
    registry = ir.async_get(hass)
    entry = make_entry(fake.host, telnet_port=fake.telnet_port)
    entry.add_to_hass(hass)

    # A stale issue from an earlier, degraded run of this same entry.
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"telnet_unavailable_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="telnet_unavailable",
        translation_placeholders={"name": entry.title},
    )
    assert registry.async_get_issue(DOMAIN, f"telnet_unavailable_{entry.entry_id}")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.coordinator.transport.name == "telnet"
    assert _issues(hass) == []


# ---------------------------------------------------------------------------------------------
# Recovering on its own
# ---------------------------------------------------------------------------------------------
#
# The watcher was written and never executed: `async_watch_for_telnet` sat at 0% while the module
# around it was at 82%. It is recovery-only code, which is the same class as the `connected`
# attribute that was missing from `HttpTransport` for seven commits -- a path that runs only when
# something has already gone wrong is exactly the path nothing exercises, and its failure mode is
# an integration that stays degraded for ever after a matrix reboots once.


async def test_the_watcher_stays_quiet_while_the_socket_is_still_held(
    hass: HomeAssistant,
) -> None:
    """A probe that fails must change nothing -- not reload, not clear the issue.

    Reloading on a failed probe would rebuild every entity every minute for as long as the fault
    lasted, which is worse than the fault.
    """
    from fake_avpro import FakeMatrix
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    import custom_components.ha_avpro_edge.avpro.telnet_client as tc
    from custom_components.ha_avpro_edge.const import TELNET_RETRY_INTERVAL

    async with FakeMatrix(faults={"telnet-busy"}) as fake:
        original, tc.CONNECT_TIMEOUT = tc.CONNECT_TIMEOUT, 1.0
        try:
            entry = await _setup(hass, fake)
            assert entry.runtime_data.coordinator.transport.name == "http"

            async_fire_time_changed(
                hass, dt_util.utcnow() + timedelta(seconds=TELNET_RETRY_INTERVAL + 1)
            )
            await hass.async_block_till_done()

            assert entry.runtime_data.coordinator.transport.name == "http"
            assert _issues(hass) == [f"telnet_unavailable_{entry.entry_id}"]
        finally:
            tc.CONNECT_TIMEOUT = original


async def test_the_watcher_reloads_onto_telnet_once_the_socket_is_free(
    hass: HomeAssistant,
) -> None:
    """The reason the watcher exists.

    Without it a matrix that rebooted once leaves the integration permanently on HTTP -- missing
    stream, input power, key lock and the LCD timeout -- until somebody notices and reloads by
    hand. Recovery goes through a reload rather than swapping the transport in place because
    capabilities decide which entities exist, and gaining four of them is what a reload is for.
    """
    from fake_avpro import FakeMatrix
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    import custom_components.ha_avpro_edge.avpro.telnet_client as tc
    from custom_components.ha_avpro_edge.const import TELNET_RETRY_INTERVAL

    async with FakeMatrix(faults={"telnet-busy"}) as fake:
        original, tc.CONNECT_TIMEOUT = tc.CONNECT_TIMEOUT, 1.0
        try:
            entry = await _setup(hass, fake)
            assert entry.runtime_data.coordinator.transport.name == "http"
            assert _issues(hass) == [f"telnet_unavailable_{entry.entry_id}"]

            # Whatever was holding the socket has let go.
            fake.faults.discard("telnet-busy")

            async_fire_time_changed(
                hass, dt_util.utcnow() + timedelta(seconds=TELNET_RETRY_INTERVAL + 1)
            )
            await hass.async_block_till_done()

            assert entry.runtime_data.coordinator.transport.name == "telnet"
            # The probe must not still be holding what it went looking for.
            assert entry.runtime_data.coordinator.transport.connected
            assert _issues(hass) == [], "the issue outlived the fault it described"

            # The four controls only telnet can read are back, which is the point of reloading
            # rather than swapping the transport underneath the existing entities.
            for kind in ("stream", "input_power", "key_lock", "lcd_timeout"):
                assert entry.runtime_data.coordinator.supports(kind)
        finally:
            tc.CONNECT_TIMEOUT = original


# ---------------------------------------------------------------------------------------------
# T-D6 -- a pushing transport is not polled on the user's profile
# ---------------------------------------------------------------------------------------------


async def test_a_pushing_transport_uses_the_slow_safety_net_interval(
    hass: HomeAssistant, fake
) -> None:
    """Polling a device that volunteers its changes is asking a question already answered."""
    from custom_components.ha_avpro_edge.const import PUSH_SAFETY_NET_INTERVAL

    entry = await _setup(hass, fake)
    interval = entry.runtime_data.coordinator.update_interval
    assert interval is not None
    assert interval.total_seconds() == PUSH_SAFETY_NET_INTERVAL


async def test_a_polling_transport_uses_the_chosen_profile(hass: HomeAssistant, fake) -> None:
    entry = await _setup(hass, fake, **{CONF_TRANSPORT: TRANSPORT_HTTP})
    interval = entry.runtime_data.coordinator.update_interval
    assert interval is not None
    assert interval.total_seconds() == 5  # the "balanced" default


# ---------------------------------------------------------------------------------------------
# Pushes reach entities
# ---------------------------------------------------------------------------------------------


async def test_an_out_of_band_change_arrives_by_push(hass: HomeAssistant, fake) -> None:
    """No poll involved: the device volunteers it, and the entity follows."""
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator
    entity = "media_player.ac_mx44_auhd_output_1"
    assert hass.states.get(entity).attributes["source"] == "SrcA"

    await fake.push_telnet("OUT1 VS IN4\r\n")

    # The push crosses a real socket and the client batches inbound lines for 250 ms before
    # parsing, so async_block_till_done alone never sees it. Poll rather than sleep a fixed
    # amount: a sleep tuned on the development box is a flake on a slower CI runner.
    for _ in range(40):
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        if coordinator.matrix.get("video_route_1") == 4:
            break

    # Asserted separately so a failure says whether the push arrived at all, or arrived and then
    # failed to reach the entity.
    assert coordinator.matrix.get("video_route_1") == 4, "the push never reached the coordinator"
    assert hass.states.get(entity).attributes["source"] == "SrcD"


async def test_the_socket_is_released_on_unload(hass: HomeAssistant, fake) -> None:
    """Holding it after unload would keep it from whatever wants it next."""
    entry = await _setup(hass, fake)
    transport = entry.runtime_data.coordinator.transport
    assert transport.connected

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not transport.connected
