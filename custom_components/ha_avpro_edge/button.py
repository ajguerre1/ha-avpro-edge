"""Momentary actions: things you do to the matrix rather than settings you leave it in.

One entity, and it exists for a specific reason. **Input Hot Plug Reset** is the only function in
the manufacturer's control-system driver that had no counterpart here, so nothing this matrix can
do is out of reach from Home Assistant.

It is a `button` rather than a `switch` because it has no state to be in. Pressing it drops an
input's TMDS and restores it, and a moment later the matrix is exactly as it was; the effect is on
the *source*, which re-reads the EDID and renegotiates. Modelling that as a toggle would invite
the question "is it on?", which has no answer.

Telnet only, like the switch it is built on: the CGI interface cannot read input power back, and a
button whose underlying control cannot be observed is a request with no way to tell whether it
landed.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AvProConfigEntry
from .const import KEY_INPUT_POWER, port_key
from .coordinator import AvProCoordinator
from .entity import AvProEntity

PARALLEL_UPDATES = 0

DESCRIPTION = ButtonEntityDescription(
    key="hot_plug_reset",
    translation_key="hot_plug_reset",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AvProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    if not coordinator.supports(KEY_INPUT_POWER):
        return
    async_add_entities(
        AvProHotPlugReset(coordinator, index)
        for index in range(1, coordinator.matrix.port_count + 1)
    )


class AvProHotPlugReset(AvProEntity, ButtonEntity):
    """Re-trigger HDMI negotiation on one input."""

    entity_description = DESCRIPTION

    def __init__(self, coordinator: AvProCoordinator, index: int) -> None:
        # Keyed off the control it drives, suffixed so it cannot collide with the switch's
        # unique id for the same input.
        super().__init__(coordinator, f"{port_key(KEY_INPUT_POWER, index)}_hot_plug_reset")
        self._index = index
        self._attr_translation_placeholders = {"index": str(index)}

    async def async_press(self) -> None:
        await self.coordinator.async_hot_plug_reset(self._index)
