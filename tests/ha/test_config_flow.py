"""The config flow, end to end against the fake matrix."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ha_avpro_edge.config_flow import normalise_host
from custom_components.ha_avpro_edge.const import (
    CONF_ALLOW_WRITES,
    CONF_POLLING_PROFILE,
    DOMAIN,
)

from .conftest import make_entry

pytestmark = pytest.mark.enable_socket


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


# ---------------------------------------------------------------------------------------------
# Host normalisation -- what people actually paste
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["192.0.2.10", "http://192.0.2.10", "http://192.0.2.10/", "  192.0.2.10  ", "192.0.2.10/index"],
)
def test_hosts_are_normalised(raw: str) -> None:
    """Storing a pasted URL unchanged would produce http://http://... at request time."""
    assert normalise_host(raw) == "192.0.2.10"


# ---------------------------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------------------------


async def test_a_matrix_is_accepted_and_identified(hass: HomeAssistant, fake) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: fake.host}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AC-MX44-AUHD"
    assert result["data"] == {CONF_HOST: fake.host}


async def test_the_unique_id_comes_from_the_mac(hass: HomeAssistant, fake) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: fake.host}
    )
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "aa:bb:cc:dd:ee:ff"


async def test_a_matrix_without_a_mac_falls_back_to_the_host(hass: HomeAssistant) -> None:
    """A working unit must never be refused over a parsing surprise in the network body."""
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"no-mac"}) as fake:
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: fake.host}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        assert entry.unique_id == f"host-{fake.host}"


async def test_an_eight_port_model_is_accepted(hass: HomeAssistant) -> None:
    """Port counts are derived, so a sibling model in the family works."""
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"other-model"}) as fake:
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: fake.host}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "AC-MX88-AUHD"


# ---------------------------------------------------------------------------------------------
# Every error branch
# ---------------------------------------------------------------------------------------------


async def test_an_unreachable_host_reports_cannot_connect(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1:1"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_something_that_is_not_a_matrix_reports_not_avpro(hass: HomeAssistant) -> None:
    """The device answers 200 for paths that do not exist, so reachability proves nothing."""
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"garbage"}) as fake:
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: fake.host}
        )
        assert result["errors"] == {"base": "not_avpro"}


async def test_an_ampersand_in_a_port_name_reports_unexpected_response(
    hass: HomeAssistant,
) -> None:
    """Refusing beats guessing which of the eight names was split."""
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"amp-in-name"}) as fake:
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: fake.host}
        )
        assert result["errors"] == {"base": "unexpected_response"}


async def test_the_form_can_be_retried_after_an_error(hass: HomeAssistant, fake) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1:1"}
    )
    assert result["errors"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: fake.host}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------------------------
# Duplicates and reconfiguration
# ---------------------------------------------------------------------------------------------


async def test_the_same_matrix_cannot_be_added_twice(hass: HomeAssistant, fake) -> None:
    make_entry(fake.host).add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: fake.host}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_updates_the_address(hass: HomeAssistant, fake) -> None:
    entry = make_entry("127.0.0.1:1")
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: fake.host}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == fake.host


async def test_reconfigure_refuses_a_different_matrix(hass: HomeAssistant, fake) -> None:
    """Silently re-targeting an entry would repoint every entity at another set of rooms."""
    entry = make_entry("127.0.0.1:1")
    entry.unique_id = "11:22:33:44:55:66"
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: fake.host}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


# ---------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------


async def test_options_round_trip(hass: HomeAssistant, loaded_entry) -> None:
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_POLLING_PROFILE: "gentle", CONF_ALLOW_WRITES: False},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options[CONF_POLLING_PROFILE] == "gentle"
    assert loaded_entry.options[CONF_ALLOW_WRITES] is False


async def test_changing_options_does_not_reload_the_entry(
    hass: HomeAssistant, loaded_entry
) -> None:
    """Reloading would blank every entity to change a number, which on wall panels is visible."""
    coordinator = loaded_entry.runtime_data.coordinator

    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_POLLING_PROFILE: "gentle", CONF_ALLOW_WRITES: False},
    )
    await hass.async_block_till_done()

    assert loaded_entry.runtime_data.coordinator is coordinator
    assert coordinator.update_interval.total_seconds() == 15
    assert coordinator.client.allow_writes is False
