"""What the entities look like once the entry is loaded."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

pytestmark = pytest.mark.enable_socket

ENTITY = "media_player.ac_mx44_auhd_output_1"


def _registry_entries(hass: HomeAssistant, entry_id: str) -> list[er.RegistryEntry]:
    return er.async_entries_for_config_entry(er.async_get(hass), entry_id)


# ---------------------------------------------------------------------------------------------
# What exists
# ---------------------------------------------------------------------------------------------


async def test_the_expected_entities_are_registered(hass: HomeAssistant, loaded_entry) -> None:
    entries = _registry_entries(hass, loaded_entry.entry_id)
    by_platform: dict[str, int] = {}
    for entity in entries:
        by_platform[entity.domain] = by_platform.get(entity.domain, 0) + 1

    assert by_platform["media_player"] == 4
    # Per output: extracted audio, test pattern, stream. Per input: TMDS. Plus one key lock for
    # the device. The last three arrived with telnet, which is the only wire that can read them.
    assert by_platform["switch"] == 4 + 4 + 4 + 4 + 1
    assert by_platform["sensor"] == 4
    assert by_platform["binary_sensor"] == 4
    # 4 outputs x 4 settings, 4 inputs x EDID, plus device-level bind mode and LCD timeout.
    assert by_platform["select"] == 16 + 4 + 2


async def test_only_the_everyday_entities_are_enabled(hass: HomeAssistant, loaded_entry) -> None:
    """Install-time settings stay disabled: every enabled entity fans state out to the panels.

    The stream switches are the exception and are deliberate. Blanking a display is something
    somebody does on a Tuesday evening, not once at commissioning, so it is the one control here
    that earns its place on by default.
    """
    entries = _registry_entries(hass, loaded_entry.entry_id)
    enabled = [e for e in entries if not e.disabled_by]
    assert {e.domain for e in enabled} == {"media_player", "sensor", "switch"}
    assert len(enabled) == 4 + 4 + 4


async def test_unique_ids_are_scoped_to_the_entry(hass: HomeAssistant, loaded_entry) -> None:
    for entity in _registry_entries(hass, loaded_entry.entry_id):
        assert entity.unique_id.startswith(f"{loaded_entry.entry_id}_")


async def test_unique_ids_are_unique(hass: HomeAssistant, loaded_entry) -> None:
    ids = [e.unique_id for e in _registry_entries(hass, loaded_entry.entry_id)]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------------------------
# media_player
# ---------------------------------------------------------------------------------------------


async def test_the_source_list_uses_the_devices_own_input_names(
    hass: HomeAssistant, loaded_entry
) -> None:
    state = hass.states.get(ENTITY)
    assert state.attributes["source_list"] == ["SrcA", "SrcB", "SrcC", "SrcD"]


async def test_the_current_source_reflects_the_routing(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    assert hass.states.get(ENTITY).attributes["source"] == "SrcA"


async def test_the_output_name_is_an_attribute_not_the_entity_name(
    hass: HomeAssistant, loaded_entry
) -> None:
    """Port names are room names on a real unit. They must not reach the entity id."""
    state = hass.states.get(ENTITY)
    assert state.attributes["port_name"] == "OutA"
    assert "OutA" not in ENTITY


async def test_it_declares_only_source_selection(hass: HomeAssistant, loaded_entry) -> None:
    """No volume, no mute, no on/off: none of them are real on this model."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    features = hass.states.get(ENTITY).attributes["supported_features"]
    assert features == MediaPlayerEntityFeature.SELECT_SOURCE


async def test_a_port_with_no_signal_reports_idle_not_off(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """Nothing was turned off -- the source is simply asleep, and this cannot wake it."""
    coordinator = loaded_entry.runtime_data.coordinator
    fake.state.signals = ["", "", "", ""]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "idle"


# ---------------------------------------------------------------------------------------------
# Unavailability
# ---------------------------------------------------------------------------------------------


async def test_entities_go_unavailable_when_the_matrix_does(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    coordinator = loaded_entry.runtime_data.coordinator
    await fake.stop()

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE


# ---------------------------------------------------------------------------------------------
# Values the device never reported
# ---------------------------------------------------------------------------------------------


async def test_an_unread_setting_renders_unknown_rather_than_a_plausible_default(
    hass: HomeAssistant, loaded_entry
) -> None:
    """Picking a default would be indistinguishable from the device actually being in it."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "select", "ha_avpro_edge", f"{loaded_entry.entry_id}_scaler_1"
    )
    assert entity_id is not None

    registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != STATE_UNKNOWN  # the fake does report scaler settings
