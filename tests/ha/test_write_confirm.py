"""The write-then-confirm dance, asserted on **state-write counts**.

This is the panel fan-out guard, and it has to be explicit rather than an implicit property of
the design. The installation this was built for drives around fifty wall panels that receive
every state change, so "one command produces one state write" is a functional requirement, not a
nicety.

The sequence being pinned down:

    write  -> exactly one state change, showing the commanded value immediately
    poll   -> zero further state changes, because the overlay already published it
    stale  -> zero state changes, and the commanded value survives (the KEEP rule)
    expiry -> exactly one state change, back to device truth, and no second command
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import Event, HomeAssistant, callback

from custom_components.ha_avpro_edge.const import (
    CONF_TRANSPORT,
    KEY_VIDEO_ROUTE,
    TRANSPORT_HTTP,
    port_key,
)

from .conftest import make_entry

pytestmark = pytest.mark.enable_socket

ENTITY = "media_player.ac_mx44_auhd_output_1"

#: These pin the write-then-confirm dance, which is transport-agnostic by design. They are run
#: over HTTP because that is the wire whose requests are individually countable -- the assertions
#: are about how many commands are issued, and one HTTP request maps to one command. The same
#: semantics over telnet are covered in test_transport_selection.py.
HTTP_ONLY = {CONF_TRANSPORT: TRANSPORT_HTTP}


def _count_changes(hass: HomeAssistant, entity_id: str) -> list[Event]:
    """Record every state_changed event for one entity."""
    events: list[Event] = []

    @callback
    def _listener(event: Event) -> None:
        if event.data.get("entity_id") == entity_id:
            events.append(event)

    hass.bus.async_listen("state_changed", _listener)
    return events


async def test_a_write_produces_exactly_one_state_change(
    hass: HomeAssistant, fake, http_entry
) -> None:
    """T-W1. The optimistic publish, and nothing else."""
    events = _count_changes(hass, ENTITY)

    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": ENTITY, "source": "SrcC"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    assert hass.states.get(ENTITY).attributes["source"] == "SrcC"


async def test_the_confirming_poll_writes_no_further_state(
    hass: HomeAssistant, fake, http_entry
) -> None:
    """T-W2. The overlay already published the value, so the poll that agrees must be silent."""
    coordinator = http_entry.runtime_data.coordinator

    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": ENTITY, "source": "SrcC"},
        blocking=True,
    )
    await hass.async_block_till_done()

    events = _count_changes(hass, ENTITY)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert events == []
    assert hass.states.get(ENTITY).attributes["source"] == "SrcC"


async def test_a_quiet_poll_writes_nothing_at_all(hass: HomeAssistant, fake, http_entry) -> None:
    """T-W5. The single largest defence against fanning noise out to fifty panels."""
    coordinator = http_entry.runtime_data.coordinator
    events = _count_changes(hass, ENTITY)

    for _ in range(5):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert events == []


async def test_a_stale_poll_inside_the_window_does_not_revert_the_entity(
    hass: HomeAssistant,
) -> None:
    """T-W3. The KEEP rule, at the Home Assistant layer.

    With slow-apply the matrix accepts the command but does not show it yet. If the overlay
    cleared on that reading, the entity would visibly flick back to the old source.
    """
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"slow-apply"}, slow_apply_seconds=0.5) as fake:
        entry = make_entry(fake.host, telnet_port=fake.telnet_port, **HTTP_ONLY)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data.coordinator
        key = port_key(KEY_VIDEO_ROUTE, 1)

        await coordinator.async_set(key, 3)
        await hass.async_block_till_done()
        assert coordinator.optimistic(key) == 3

        # A poll landing before the matrix has applied it.
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.optimistic(key) == 3, "the overlay cleared on a stale read"

        # Once the matrix catches up, the value is confirmed rather than merely assumed.
        await asyncio.sleep(0.6)
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.matrix.video_routes[0] == 3
        assert key not in coordinator.pending


async def test_an_ignored_write_settles_on_device_truth_and_is_never_re_sent(
    hass: HomeAssistant,
) -> None:
    """T-W4. Two controllers re-asserting at each other is the failure this prevents."""
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"never-apply"}) as fake:
        entry = make_entry(fake.host, telnet_port=fake.telnet_port, **HTTP_ONLY)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data.coordinator
        key = port_key(KEY_VIDEO_ROUTE, 1)
        original = coordinator.matrix.video_routes[0]

        fake.requests.clear()
        await coordinator.async_set(key, 3)
        await hass.async_block_till_done()

        commands = [r for r in fake.requests if r.startswith("TimSendCmd")]
        assert len(commands) == 1, "the command was sent more than once"

        # Wait past the settle window plus the expiry margin.
        await asyncio.sleep(2.0)
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert key not in coordinator.pending
        assert coordinator.optimistic(key) == original
        assert coordinator.overrides[key] >= 1

        commands = [r for r in fake.requests if r.startswith("TimSendCmd")]
        assert len(commands) == 1, "a disagreeing poll caused a re-send"


async def test_setting_a_value_it_already_has_sends_nothing(
    hass: HomeAssistant, fake, http_entry
) -> None:
    """A scene setting four outputs to the same input must not write on every activation."""
    coordinator = http_entry.runtime_data.coordinator
    key = port_key(KEY_VIDEO_ROUTE, 1)
    current = coordinator.matrix.video_routes[0]

    fake.requests.clear()
    await coordinator.async_set(key, current)
    await hass.async_block_till_done()

    assert [r for r in fake.requests if r.startswith("TimSendCmd")] == []


async def test_an_out_of_band_change_is_picked_up(hass: HomeAssistant, fake, http_entry) -> None:
    """A change made by another control system looks like any other change: the value moved."""
    coordinator = http_entry.runtime_data.coordinator
    events = _count_changes(hass, ENTITY)

    fake.state.video_routes[0] = 4
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert hass.states.get(ENTITY).attributes["source"] == "SrcD"


async def test_route_all_uses_one_request_for_every_output(
    hass: HomeAssistant, fake, http_entry
) -> None:
    coordinator = http_entry.runtime_data.coordinator

    fake.requests.clear()
    await coordinator.async_route_all(2)
    await hass.async_block_till_done()

    assert len([r for r in fake.requests if r.startswith("TimSendCmd")]) == 1
    assert fake.state.video_routes == [2, 2, 2, 2]


# ---------------------------------------------------------------------------------------------
# T-N11 -- shutdown drops the overlay
# ---------------------------------------------------------------------------------------------


async def test_shutdown_drops_the_pending_overlay(hass: HomeAssistant) -> None:
    """T-N11. An optimistic value that outlives its connection is a claim about nothing.

    The overlay only means "commanded, not yet confirmed" -- and confirmation can only arrive over
    the transport that carried the command. Once that is gone, so is any prospect of the value
    being either confirmed or corrected, and the expiry watchdog has been cancelled along with it.
    Keeping it would leave the entity asserting a value nothing will ever check, until something
    happens to reload the entry.

    Uses ``never-apply`` so the write is guaranteed to still be outstanding at shutdown; against
    the ordinary fake the confirming read can land first and the overlay would be legitimately
    empty, which would pass while asserting nothing.
    """
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"never-apply"}) as fake:
        entry = make_entry(fake.host, telnet_port=fake.telnet_port, **HTTP_ONLY)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data.coordinator
        key = port_key(KEY_VIDEO_ROUTE, 1)

        await coordinator.async_set(key, 3)
        await hass.async_block_till_done()
        assert key in coordinator.pending, "nothing was pending; the test proves nothing"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert key not in coordinator.pending
