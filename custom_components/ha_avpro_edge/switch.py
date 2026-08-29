"""Per-output toggles.

``extracted_audio`` is the honest home for what a ``media_player`` mute would have misreported:
it enables the separate de-embedded audio feed for an output, and has no effect on the audio
going to the display over HDMI.

The TMDS stream toggle is **not** here. Its control endpoint exists, but the firmware this was
built against has no matching status endpoint, so nothing could read it back -- not after a
restart, not after anything else touched the matrix. A switch whose state is a remembered guess
is worse than no switch. It returns as an ``assumed_state`` entity once a live probe establishes
whether the write is even accepted, and whether it gates the output stream or an input's power.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AvProConfigEntry
from .const import KEY_EXTRACTED_AUDIO, KEY_TEST_PATTERN, port_key
from .coordinator import AvProCoordinator
from .entity import AvProEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class AvProSwitchDescription(SwitchEntityDescription):
    kind: str


PER_OUTPUT: tuple[AvProSwitchDescription, ...] = (
    AvProSwitchDescription(
        key=KEY_EXTRACTED_AUDIO,
        kind=KEY_EXTRACTED_AUDIO,
        translation_key="extracted_audio",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
    AvProSwitchDescription(
        key=KEY_TEST_PATTERN,
        kind=KEY_TEST_PATTERN,
        translation_key="test_pattern",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AvProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        AvProSwitch(coordinator, description, output)
        for description in PER_OUTPUT
        if coordinator.supports(description.kind)
        for output in range(1, coordinator.matrix.port_count + 1)
    )


class AvProSwitch(AvProEntity, SwitchEntity):
    """One per-output toggle."""

    entity_description: AvProSwitchDescription

    def __init__(
        self,
        coordinator: AvProCoordinator,
        description: AvProSwitchDescription,
        output: int,
    ) -> None:
        super().__init__(coordinator, port_key(description.kind, output))
        self.entity_description = description
        self._attr_translation_placeholders = {"index": str(output)}

    @property
    def is_on(self) -> bool | None:
        """``None`` when unread. Never coerce that to ``False``: "unknown" and "off" are
        different, and only one of them is true."""
        return self.coordinator.optimistic(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(self._key, False)
