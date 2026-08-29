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
    if not coordinator.supports(KEY_SIGNAL):
        # A firmware whose signal endpoint is absent gets no entity, rather than four that can
        # never say anything. Telnet cannot read signal either, which is why the telnet transport
        # is supplemented over HTTP -- see avpro/supplement.py.
        return
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
        """``None`` until this port's signal has actually been read.

        The guard used to be ``if not signals``, which is a different question and the wrong one.
        An unread series is not empty -- it is a tuple of ``None`` per port, which is truthy -- so
        the check passed and ``bool(None)`` reported **every port as disconnected**, confidently
        and permanently, on any transport that could not read signal at all. An automation
        conditioned on "no signal" would have fired against a matrix whose ports were all live.

        The distinction is the one this whole integration is built on: absence is not off.
        """
        signals = self.coordinator.matrix.signals
        if not 1 <= self._port <= len(signals):
            return None
        detected = signals[self._port - 1]
        return None if detected is None else bool(detected)

    def _state_snapshot(self) -> bool | None:
        """What this entity renders -- **not** what its key says.

        The base implementation gates on ``coordinator.optimistic(self._key)``, and this entity's
        key is ``signal_present_N``, which no transport reports and no state ever contains. So the
        gate compared ``None`` to ``None`` on every single update and returned early every time:
        the entity was frozen at whatever it read when the platform was set up, for the life of
        the entry. Unplug a source and it would keep saying *Connected* until Home Assistant
        restarted.

        The cause is one field doing two jobs. ``_key`` is both the unique id and the state key to
        watch, and the comment above chose a distinct key to get a distinct unique id -- correct
        for the first job, and silently wrong for the second. Nothing complained, because a change
        gate that never fires looks exactly like a value that never changes.
        """
        return self.is_on
