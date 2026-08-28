"""Whether a port is carrying anything at all.

Derived from the same field the signal sensor reports, but as a boolean, because "is the Apple TV
awake" is an automation condition and string-matching a resolution to answer it is fragile.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AvProConfigEntry
from .const import KEY_SIGNAL, port_key
from .coordinator import AvProCoordinator
from .entity import AvProEntity

PARALLEL_UPDATES = 0

DESCRIPTION = BinarySensorEntityDescription(
    key=f"{KEY_SIGNAL}_present",
    translation_key="signal_present",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AvProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        AvProSignalPresent(coordinator, port)
        for port in range(1, coordinator.matrix.port_count + 1)
    )


class AvProSignalPresent(AvProEntity, BinarySensorEntity):
    """True when the matrix reports something on this port."""

    entity_description = DESCRIPTION

    def __init__(self, coordinator: AvProCoordinator, port: int) -> None:
        # A distinct key from the sensor's, so the two entities have distinct unique ids.
        super().__init__(coordinator, port_key(f"{KEY_SIGNAL}_present", port))
        self._port = port
        self._attr_translation_placeholders = {"index": str(port)}

    @property
    def is_on(self) -> bool | None:
        """``None`` until the signal endpoint has been read at least once.

        Reporting "disconnected" before anything has been read would show every port as dead for
        the first few seconds after a restart.
        """
        signals = self.coordinator.matrix.signals
        if not signals or not 1 <= self._port <= len(signals):
            return None
        return bool(signals[self._port - 1])
