"""Transport selection, fallback, and the discipline rule (T-X1..T-X3, T-D1..T-D6).

**Telnet is primary. Always speak telnet unless you don't need to.**

The three assertions declared as strict xfail when the guard was inverted are implemented here.
The middle one is the one that encodes the rule: while telnet is connected, no HTTP request is
issued for anything telnet supports. Hedging by running both wires would double the device load
and leave two sources of truth.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.ha_avpro_edge.const import (
    CONF_TRANSPORT,
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
    entry = await _setup(hass, fake, **{CONF_TRANSPORT: TRANSPORT_HTTP})
    coordinator = entry.runtime_data.coordinator
    for kind in ("stream", "input_power", "key_lock", "lcd_timeout"):
        assert not coordinator.supports(kind)


# ---------------------------------------------------------------------------------------------
# T-X2 -- the rule itself
# ---------------------------------------------------------------------------------------------


async def test_no_http_request_is_issued_for_anything_telnet_supports(
    hass: HomeAssistant, fake
) -> None:
    """Always speak telnet unless you don't need to.

    Running HTTP alongside a healthy telnet session would be hedging: two transports doing one
    transport's job, double the device load, two sources of truth to reconcile.
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

    assert fake.requests == [], f"HTTP was used while telnet was connected: {fake.requests}"


async def test_identity_is_the_only_http_read_and_it_happens_once(
    hass: HomeAssistant, fake
) -> None:
    """The documented exception, bounded.

    Telnet cannot report model, firmware or the port names -- GET STA covers routing and settings
    and stops there. Reading them over HTTP is the same exception that covers renaming: an
    operation only HTTP has. It must happen at setup and never again, or it becomes the hedging
    the rule forbids.
    """
    await _setup(hass, fake)

    identity_reads = [r.split("?")[0] for r in fake.requests]
    assert set(identity_reads) <= {"WEBDivSta.CGI", "NETDivSta.CGI"}
    assert identity_reads.count("WEBDivSta.CGI") == 1


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

    assert fake.requests == []
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
    entity = "media_player.ac_mx44_auhd_output_1"
    assert hass.states.get(entity).attributes["source"] == "SrcA"

    await fake.push_telnet("OUT1 VS IN4\r\n")
    await hass.async_block_till_done()

    assert hass.states.get(entity).attributes["source"] == "SrcD"


async def test_the_socket_is_released_on_unload(hass: HomeAssistant, fake) -> None:
    """Holding it after unload would keep it from whatever wants it next."""
    entry = await _setup(hass, fake)
    transport = entry.runtime_data.coordinator.transport
    assert transport.connected

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not transport.connected
