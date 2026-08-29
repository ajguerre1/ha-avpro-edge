"""Entry setup, teardown and the failure asymmetry."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.ha_avpro_edge.avpro.protocol import StatusEndpoint
from custom_components.ha_avpro_edge.const import CONF_TRANSPORT, DOMAIN, TRANSPORT_HTTP

from .conftest import make_entry

pytestmark = pytest.mark.enable_socket


async def test_the_entry_loads(hass: HomeAssistant, loaded_entry) -> None:
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert loaded_entry.runtime_data.coordinator is not None


async def test_an_unreachable_matrix_is_not_ready_rather_than_broken(
    hass: HomeAssistant,
) -> None:
    entry = make_entry("127.0.0.1:1")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_the_entry_unloads_cleanly(hass: HomeAssistant, loaded_entry) -> None:
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED


async def test_the_device_is_registered_with_its_identity(
    hass: HomeAssistant, loaded_entry
) -> None:
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, loaded_entry.entry_id)})
    assert device is not None
    assert device.manufacturer == "AVPro Edge"
    assert device.model == "AC-MX44-AUHD"
    assert device.sw_version == "V1.41"
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff") in device.connections


async def test_the_opening_http_read_covers_every_endpoint(hass: HomeAssistant, fake) -> None:
    """Entities are created from what the device reports, so the census has to be complete.

    Forces HTTP: on telnet the census is a single GET STA and no endpoint is fetched at all.
    """
    entry = make_entry(fake.host, telnet_port=fake.telnet_port, **{CONF_TRANSPORT: TRANSPORT_HTTP})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    requested = {r.split("?")[0] for r in fake.requests}
    assert {e.value for e in StatusEndpoint} <= requested


# ---------------------------------------------------------------------------------------------
# The failure asymmetry -- the part with a total-outage blast radius
# ---------------------------------------------------------------------------------------------


async def test_an_absent_endpoint_does_not_prevent_setup(hass: HomeAssistant) -> None:
    """This firmware genuinely lacks the TMDS tab. If that failed setup, nothing would load."""
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"tmds-present"}) as fake:
        entry = make_entry(
            fake.host, telnet_port=fake.telnet_port, **{CONF_TRANSPORT: TRANSPORT_HTTP}
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED


async def test_an_absent_endpoint_is_recorded_and_never_re_requested(
    hass: HomeAssistant,
) -> None:
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"tmds-present"}) as fake:
        entry = make_entry(
            fake.host, telnet_port=fake.telnet_port, **{CONF_TRANSPORT: TRANSPORT_HTTP}
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data.coordinator
        # Endpoint-level capability now belongs to the transport, which is the thing that knows
        # what endpoints are.
        transport = coordinator.transport
        assert not transport.device_capabilities.endpoint_available(StatusEndpoint.TMDS)

        fake.requests.clear()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert not [r for r in fake.requests if r.startswith("TMDSDivSta")]


async def test_entities_are_created_despite_the_missing_endpoint(hass: HomeAssistant) -> None:
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"tmds-present"}) as fake:
        entry = make_entry(
            fake.host, telnet_port=fake.telnet_port, **{CONF_TRANSPORT: TRANSPORT_HTTP}
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert len(hass.states.async_entity_ids("media_player")) == 4


# ---------------------------------------------------------------------------------------------
# Model independence
# ---------------------------------------------------------------------------------------------


async def test_an_eight_port_unit_gets_eight_outputs(hass: HomeAssistant) -> None:
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"other-model"}) as fake:
        entry = make_entry(
            fake.host, telnet_port=fake.telnet_port, **{CONF_TRANSPORT: TRANSPORT_HTTP}
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert len(hass.states.async_entity_ids("media_player")) == 8


# ---------------------------------------------------------------------------------------------
# The house's control system
# ---------------------------------------------------------------------------------------------


async def test_setting_up_does_use_the_control_socket_by_default(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """This assertion used to be its own opposite.

    While the integration was HTTP-only, any telnet connection was a bug. Telnet is now the
    primary transport, so connecting is correct -- and the assertion that the socket is left
    alone moved to where it still holds: under the http setting, in
    tests/ha/test_transport_selection.py.
    """
    assert fake.telnet_connections >= 1
    assert loaded_entry.runtime_data.coordinator.transport.name == "telnet"
