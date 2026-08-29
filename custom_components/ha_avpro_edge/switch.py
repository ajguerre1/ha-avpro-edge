"""Toggles: per output, per input, and one for the whole device.

``extracted_audio`` is the honest home for what a ``media_player`` mute would have misreported:
it enables the separate de-embedded audio feed for an output, and has no effect on the audio
going to the display over HDMI.

**The stream toggle is here now, and it is not ``assumed_state``.** It was left out while HTTP was
the only transport, because the CGI interface has a control endpoint for it and no matching status
endpoint -- nothing could read it back after a restart, or after anything else touched the matrix,
and a switch whose state is a remembered guess is worse than no switch. Telnet reports
``OUT1 STREAM ON`` directly, so on that wire it is real state and says so. Under HTTP the entity is
not created at all, which is the same capability rule every other control here follows.

``input_power`` is per **input**, not per output: ``IN1 TMDS ON`` gates whether the matrix drives
that input's TMDS clock, which is how a source is told to wake or sleep.
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
from .const import (
    KEY_EXTRACTED_AUDIO,
    KEY_INPUT_POWER,
    KEY_KEY_LOCK,
    KEY_STREAM,
    KEY_TEST_PATTERN,
    port_key,
)
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
    AvProSwitchDescription(
        key=KEY_STREAM,
        kind=KEY_STREAM,
        translation_key="stream",
        device_class=SwitchDeviceClass.SWITCH,
        # Neither a config entity nor disabled by default, unlike everything else in this module.
        # Turning an output's stream off blanks the display on the other end: that is an everyday
        # action, not an install-time setting, and it is the closest thing this hardware has to
        # "turn that screen off".
        entity_registry_enabled_default=True,
    ),
)

#: Per *input*: this gates the TMDS clock the matrix drives towards a source.
PER_INPUT: tuple[AvProSwitchDescription, ...] = (
    AvProSwitchDescription(
        key=KEY_INPUT_POWER,
        kind=KEY_INPUT_POWER,
        translation_key="input_power",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)

#: One for the whole matrix rather than per port.
DEVICE_LEVEL: tuple[AvProSwitchDescription, ...] = (
    AvProSwitchDescription(
        key=KEY_KEY_LOCK,
        kind=KEY_KEY_LOCK,
        translation_key="key_lock",
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
    ports = coordinator.matrix.port_count

    # Capability-driven throughout: a wire that cannot read a setting gets no entity for it,
    # rather than one that reads unknown forever.
    entities: list[AvProSwitch] = [
        AvProSwitch(coordinator, description, index)
        for group in (PER_OUTPUT, PER_INPUT)
        for description in group
        if coordinator.supports(description.kind)
        for index in range(1, ports + 1)
    ]
    entities += [
        AvProSwitch(coordinator, description, None)
        for description in DEVICE_LEVEL
        if coordinator.supports(description.kind)
    ]
    async_add_entities(entities)


class AvProSwitch(AvProEntity, SwitchEntity):
    """One toggle, indexed by port or belonging to the device as a whole."""

    entity_description: AvProSwitchDescription

    def __init__(
        self,
        coordinator: AvProCoordinator,
        description: AvProSwitchDescription,
        index: int | None,
    ) -> None:
        key = description.kind if index is None else port_key(description.kind, index)
        super().__init__(coordinator, key)
        self.entity_description = description
        if index is not None:
            self._attr_translation_placeholders = {"index": str(index)}

    @property
    def is_on(self) -> bool | None:
        """``None`` when unread. Never coerce that to ``False``: "unknown" and "off" are
        different, and only one of them is true."""
        return self.coordinator.optimistic(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set(self._key, False)
