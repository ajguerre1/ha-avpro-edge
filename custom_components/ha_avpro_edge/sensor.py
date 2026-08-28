"""Detected signal, per port.

The value is free text exactly as the matrix reports it -- ``3840X2160P@60HZ YUV420`` and the
like. Deliberately not parsed into an enumeration or split into resolution/rate/colourimetry
fields: any set of values written today is a bug on tomorrow's firmware, and this device's own
vocabulary is not documented anywhere. Passing it through means a format nobody anticipated shows
up as itself rather than as "unknown".

Whether the four fields describe inputs or outputs is not established from the CGI interface
alone, so they are labelled by port index rather than asserting one or the other.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AvProConfigEntry
from .const import KEY_SIGNAL, port_key
from .coordinator import AvProCoordinator
from .entity import AvProEntity

PARALLEL_UPDATES = 0

DESCRIPTION = SensorEntityDescription(
    key=KEY_SIGNAL,
    translation_key="signal",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AvProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        AvProSignalSensor(coordinator, port) for port in range(1, coordinator.matrix.port_count + 1)
    )


class AvProSignalSensor(AvProEntity, SensorEntity):
    """What the matrix reports is present on one port."""

    entity_description = DESCRIPTION

    def __init__(self, coordinator: AvProCoordinator, port: int) -> None:
        super().__init__(coordinator, port_key(KEY_SIGNAL, port))
        self._port = port
        self._attr_translation_placeholders = {"index": str(port)}

    @property
    def native_value(self) -> str | None:
        """``None`` for a port reporting nothing, which is how "no source" arrives."""
        signals = self.coordinator.matrix.signals
        if 1 <= self._port <= len(signals):
            return signals[self._port - 1]
        return None
