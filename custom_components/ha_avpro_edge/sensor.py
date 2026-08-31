"""Detected signal, per port.

The value is free text exactly as the matrix reports it -- ``3840X2160P@60HZ YUV420`` and the
like. Deliberately not parsed into an enumeration or split into resolution/rate/colourimetry
fields: any set of values written today is a bug on tomorrow's firmware, and this device's own
vocabulary is not documented anywhere. Passing it through means a format nobody anticipated shows
up as itself rather than as "unknown".

**The four fields describe inputs.** Established 2026-08-31 by two observations that would have
come out reversed had they been outputs: unplugging the *source* on input 3 drove field 3 to
``NO SIGNAL`` (T-L8), and muting *output* 3's stream left field 3 unchanged (T-L2). The planning
docs carried this as open probe P10, unanswerable from the CGI interface alone -- and it was,
because reading the interface was never going to settle it. Only moving one end of the wire did.

The entities are still labelled by port index rather than "Input N". Renaming them now would
change entity ids that are already on dashboards, for a naming improvement rather than a
correctness one. The hedge is no longer load-bearing; it is just the name it shipped with.
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
