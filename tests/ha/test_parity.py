"""Control-system parity (T-G1..T-G4).

The functions the manufacturer's own control-system driver has and this did not. Parity is scoped
against what that driver *does*, because that is the only definition that decides anything: the
matrix loses its dependency when nothing is left that only the driver can do.

Its command surface is ex-audio enable, ex-audio matrix mode, ex-audio output, LCD time, keylock,
**Input Hot Plug Reset** and a raw-command action. The first five were already covered by entities.
The last two are here.
"""

from __future__ import annotations

import asyncio

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.ha_avpro_edge.const import DOMAIN

from .conftest import make_entry

pytestmark = pytest.mark.enable_socket


async def _setup(hass: HomeAssistant, fake, **options):
    entry = make_entry(fake.host, telnet_port=fake.telnet_port, **options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _until(hass: HomeAssistant, condition, *, what: str) -> None:
    """Wait for the device rather than for Home Assistant's own loop."""
    for _ in range(60):
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
        if condition():
            return
    raise AssertionError(f"timed out waiting for {what}")


# ---------------------------------------------------------------------------------------------
# T-G1 -- Input Hot Plug Reset
# ---------------------------------------------------------------------------------------------


async def test_hot_plug_reset_drops_the_input_and_restores_it(hass: HomeAssistant, fake) -> None:
    """T-G1. The point is the transition, not the end state.

    A source that has settled on the wrong resolution needs to *see* its hot-plug line go away
    and come back. Asserting only the final value would pass just as well against an
    implementation that did nothing at all, so the OFF is what this actually checks.
    """
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator
    fake.telnet_commands.clear()

    await coordinator.async_hot_plug_reset(1)
    await _until(
        hass,
        lambda: fake.state.input_power[0] is True,
        what="the input to come back up",
    )

    sent = [c.upper() for c in fake.telnet_commands if "TMDS" in c.upper()]
    assert sent == ["SET IN1 TMDS OFF", "SET IN1 TMDS ON"], f"expected a cycle, got {sent}"


async def test_hot_plug_reset_leaves_the_other_inputs_alone(hass: HomeAssistant, fake) -> None:
    """T-G1. Resetting one source must not interrupt three others."""
    entry = await _setup(hass, fake)
    await entry.runtime_data.coordinator.async_hot_plug_reset(2)
    await _until(hass, lambda: fake.state.input_power[1] is True, what="input 2 to come back")

    assert fake.state.input_power == [True, True, True, True]


async def test_the_button_exists_only_where_input_power_can_be_read(
    hass: HomeAssistant, fake
) -> None:
    """T-G1. A button whose control cannot be read back is a request with no receipt."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.ha_avpro_edge.const import CONF_TRANSPORT, TRANSPORT_HTTP

    telnet_entry = await _setup(hass, fake)
    registry = er.async_get(hass)
    buttons = [
        e
        for e in er.async_entries_for_config_entry(registry, telnet_entry.entry_id)
        if e.domain == "button"
    ]
    assert len(buttons) == 4

    assert await hass.config_entries.async_unload(telnet_entry.entry_id)
    await hass.async_block_till_done()

    http_entry = await _setup(hass, fake, **{CONF_TRANSPORT: TRANSPORT_HTTP})
    http_buttons = [
        e
        for e in er.async_entries_for_config_entry(registry, http_entry.entry_id)
        if e.domain == "button"
    ]
    assert http_buttons == []


# ---------------------------------------------------------------------------------------------
# T-G2 -- route_all as an action
# ---------------------------------------------------------------------------------------------


async def test_route_all_is_registered_before_any_entry_loads(hass: HomeAssistant) -> None:
    """T-G2. The ``action-setup`` rule.

    Registered for the integration, not per entry, so an automation referencing it still
    validates when the matrix happens to be unreachable at startup. Otherwise a device that is
    briefly absent turns every automation using it into a configuration error.
    """
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "route_all")
    assert hass.services.has_service(DOMAIN, "send_command")


async def test_route_all_sends_every_output_to_one_input(hass: HomeAssistant, fake) -> None:
    """T-G2."""
    entry = await _setup(hass, fake)

    await hass.services.async_call(
        DOMAIN, "route_all", {"config_entry_id": entry.entry_id, "source": 2}, blocking=True
    )
    await _until(hass, lambda: fake.state.video_routes == [2, 2, 2, 2], what="every output to move")


async def test_an_action_against_an_unknown_matrix_says_so(hass: HomeAssistant, fake) -> None:
    """T-G2. A typo'd entry id must be a clear message, not a traceback."""
    await _setup(hass, fake)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "route_all", {"config_entry_id": "nonsense", "source": 1}, blocking=True
        )


# ---------------------------------------------------------------------------------------------
# T-G3 / T-G4 -- send_command
# ---------------------------------------------------------------------------------------------


async def test_send_command_returns_what_the_device_said(hass: HomeAssistant, fake) -> None:
    """T-G3. The reply is the whole value of this action.

    This firmware answers an unsupported command with ``NO SUPPORT`` and HTTP 200. Without the
    body, "it worked" and "it was politely ignored" are the same observation.
    """
    entry = await _setup(hass, fake)

    response = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {"config_entry_id": entry.entry_id, "endpoint": "video", "command": "O1I3"},
        blocking=True,
        return_response=True,
    )

    assert response["supported"] is True
    assert "response" in response
    await _until(hass, lambda: fake.state.video_routes[0] == 3, what="the raw command to apply")


async def test_a_refusal_is_reported_rather_than_read_as_success(hass: HomeAssistant) -> None:
    """T-G3. ``NO SUPPORT`` must arrive as ``supported: False``, not as a silent pass."""
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"no-support"}) as fake:
        entry = await _setup(hass, fake)

        response = await hass.services.async_call(
            DOMAIN,
            "send_command",
            {"config_entry_id": entry.entry_id, "endpoint": "video", "command": "O1I3"},
            blocking=True,
            return_response=True,
        )

    assert response["supported"] is False


async def test_the_network_endpoints_cannot_be_named_at_all(hass: HomeAssistant, fake) -> None:
    """T-G4. The assertion that must never be relaxed.

    Changing the matrix's address is a non-goal of this integration because a wrong one is a site
    visit. An escape hatch able to reach ``NetSendCmd`` would reintroduce exactly that, from an
    automation. They are absent from the mapping rather than rejected by a check, so this asserts
    the mapping -- a validation rule can be loosened by accident; a missing entry cannot.
    """
    from custom_components.ha_avpro_edge.services import ALLOWED_ENDPOINTS

    reachable = {endpoint.value.lower() for endpoint in ALLOWED_ENDPOINTS.values()}
    for forbidden in ("net", "dhcp", "rst", "reset"):
        assert not [path for path in reachable if forbidden in path], (
            f"send_command can reach something matching {forbidden!r}: {reachable}"
        )


@pytest.mark.parametrize(
    "endpoint",
    ["network", "net", "NetSendCmd", "reset", "dhcp"],
)
async def test_an_endpoint_outside_the_list_is_refused(
    hass: HomeAssistant, fake, endpoint: str
) -> None:
    """T-G4. And the schema refuses to accept one by name, too."""
    entry = await _setup(hass, fake)

    # voluptuous raises MultipleInvalid, which is not exported anywhere convenient; what matters
    # is that the call does not reach the device, not which exception type says so.
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "send_command",
            {"config_entry_id": entry.entry_id, "endpoint": endpoint, "command": "X1"},
            blocking=True,
        )


@pytest.mark.parametrize("command", ["O1I2&O2I1", "O1I2 ", "../etc", "O1I2?x=1", ""])
async def test_a_command_that_could_carry_a_separator_is_refused(
    hass: HomeAssistant, fake, command: str
) -> None:
    """T-G4. The command becomes a query parameter, so the device's parser would be the guard.

    That parser has already been observed splitting on an unescaped ``&`` in its own responses,
    which is not something to rely on for safety.
    """
    entry = await _setup(hass, fake)
    fake.requests.clear()

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "send_command",
            {"config_entry_id": entry.entry_id, "endpoint": "video", "command": command},
            blocking=True,
        )

    assert fake.requests == [], "a rejected command still reached the device"
