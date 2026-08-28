"""Enumerated matrix settings.

All disabled by default and categorised as configuration. They are install-time settings on a
device whose routing is the only thing that changes day to day, and every enabled entity on this
installation fans its state out to around fifty wall panels.

The per-input EDID selector is deliberately **not** here yet. Reads return tokens like
``EDIDU1`` while writes take a preset index, and the mapping between the two vocabularies has not
been established against hardware. Shipping a selector built on a guessed mapping would let a
user pick "4K60 8CH HDR" and silently get something else, which is worse than not offering it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AvProConfigEntry
from .avpro.models import AudioDelay, BindMode, ImageEnhancement, ScalerMode
from .avpro.state import MatrixState
from .const import (
    KEY_AUDIO_DELAY,
    KEY_AUDIO_ROUTE,
    KEY_BIND_MODE,
    KEY_IMAGE_ENHANCEMENT,
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

DEVICE_LEVEL = AvProSelectDescription(
    key=KEY_BIND_MODE,
    kind=KEY_BIND_MODE,
    translation_key="bind_mode",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    options_for=_enum_options(BindMode),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AvProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    ports = coordinator.matrix.port_count

    entities: list[AvProSelect] = [
        AvProSelect(coordinator, description, output)
        for description in PER_OUTPUT
        for output in range(1, ports + 1)
    ]
    entities.append(AvProSelect(coordinator, DEVICE_LEVEL, None))
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
