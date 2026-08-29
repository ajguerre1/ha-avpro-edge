"""Enumerated matrix settings.

All disabled by default and categorised as configuration. They are install-time settings on a
device whose routing is the only thing that changes day to day, and every enabled entity on this
installation fans its state out to many dashboards.

Which selects exist depends on what the live transport can read. Telnet can read every setting
here; the HTTP interface cannot see output stream state, input power, key lock or the LCD
timeout, so under HTTP those entities are simply not created rather than reading unknown forever.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AvProConfigEntry
from .avpro.models import (
    EDID_OPTIONS,
    AudioDelay,
    BindMode,
    ImageEnhancement,
    LcdTimeout,
    ScalerMode,
)
from .avpro.state import MatrixState
from .const import (
    KEY_AUDIO_DELAY,
    KEY_AUDIO_ROUTE,
    KEY_BIND_MODE,
    KEY_EDID,
    KEY_IMAGE_ENHANCEMENT,
    KEY_LCD_TIMEOUT,
    KEY_SCALER,
    port_key,
)
from .coordinator import AvProCoordinator
from .entity import AvProEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class AvProSelectDescription(SelectEntityDescription):
    """A per-output select whose options come from an enumeration."""

    kind: str
    options_for: Callable[[MatrixState], list[str]]


def _enum_options(enum_cls) -> Callable[[MatrixState], list[str]]:
    return lambda _state: [member.value for member in enum_cls]


PER_OUTPUT: tuple[AvProSelectDescription, ...] = (
    AvProSelectDescription(
        key=KEY_AUDIO_ROUTE,
        kind=KEY_AUDIO_ROUTE,
        translation_key="audio_source",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        # Options are input indices rather than an enum: which inputs exist is a property of the
        # unit, not of the protocol.
        options_for=lambda state: [str(i) for i in range(1, state.port_count + 1)],
    ),
    AvProSelectDescription(
        key=KEY_AUDIO_DELAY,
        kind=KEY_AUDIO_DELAY,
        translation_key="audio_delay",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        options_for=_enum_options(AudioDelay),
    ),
    AvProSelectDescription(
        key=KEY_SCALER,
        kind=KEY_SCALER,
        translation_key="scaler",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        options_for=_enum_options(ScalerMode),
    ),
    AvProSelectDescription(
        key=KEY_IMAGE_ENHANCEMENT,
        kind=KEY_IMAGE_ENHANCEMENT,
        translation_key="image_enhancement",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        options_for=_enum_options(ImageEnhancement),
    ),
)

#: Per *input*, not per output: an EDID tells a source what the display can accept.
PER_INPUT: tuple[AvProSelectDescription, ...] = (
    AvProSelectDescription(
        key=KEY_EDID,
        kind=KEY_EDID,
        translation_key="edid",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        options_for=lambda _state: list(EDID_OPTIONS),
    ),
)

DEVICE_LEVEL: tuple[AvProSelectDescription, ...] = (
    AvProSelectDescription(
        key=KEY_BIND_MODE,
        kind=KEY_BIND_MODE,
        translation_key="bind_mode",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        options_for=_enum_options(BindMode),
    ),
    AvProSelectDescription(
        key=KEY_LCD_TIMEOUT,
        kind=KEY_LCD_TIMEOUT,
        translation_key="lcd_timeout",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        # Exactly four, and that is measured rather than assumed: the live matrix accepted T0-T3
        # and refused T4 and T5. See LcdTimeout for what is measured and what is still inferred.
        options_for=_enum_options(LcdTimeout),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AvProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    ports = coordinator.matrix.port_count

    # Capability-driven: a wire that cannot read a setting gets no entity for it, rather than
    # one that reads unknown forever. Extends the mechanism already used for endpoints a given
    # firmware lacks.
    entities: list[AvProSelect] = [
        AvProSelect(coordinator, description, index)
        for group in (PER_OUTPUT, PER_INPUT)
        for description in group
        if coordinator.supports(description.kind)
        for index in range(1, ports + 1)
    ]
    entities += [
        AvProSelect(coordinator, description, None)
        for description in DEVICE_LEVEL
        if coordinator.supports(description.kind)
    ]
    async_add_entities(entities)


class AvProSelect(AvProEntity, SelectEntity):
    """One enumerated setting."""

    entity_description: AvProSelectDescription

    def __init__(
        self,
        coordinator: AvProCoordinator,
        description: AvProSelectDescription,
        output: int | None,
    ) -> None:
        key = description.kind if output is None else port_key(description.kind, output)
        super().__init__(coordinator, key)
        self.entity_description = description
        if output is not None:
            self._attr_translation_placeholders = {"index": str(output)}

    @property
    def options(self) -> list[str]:
        return self.entity_description.options_for(self.coordinator.matrix)

    @property
    def current_option(self) -> str | None:
        """``None`` when this firmware never reported the setting.

        Rendering "unknown" is right: picking a plausible default would be indistinguishable
        from the device actually being in that state.
        """
        value = self.coordinator.optimistic(self._key)
        if value is None:
            return None
        return str(value)

    async def async_select_option(self, option: str) -> None:
        # Audio routes are integers on the wire; every other select carries an enum's own value.
        value = int(option) if self.entity_description.kind == KEY_AUDIO_ROUTE else option
        await self.coordinator.async_set(self._key, value)
