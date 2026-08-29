"""The base every entity in this integration is built on.

Its whole job is to write state only when something actually changed.

``DataUpdateCoordinator`` notifies every listener on every cycle it considers an update, and the
stock ``CoordinatorEntity`` responds by writing state each time. On the installation this was
built for, that fans out to many dashboards. The three layers that stop it are:

1. ``MatrixState`` compares by value, so ``always_update=False`` suppresses the notification
   entirely on a quiet tick;
2. an optimistic write publishes once, so the confirming poll produces no further change;
3. this class, which compares what the entity would actually report against what it last
   reported, and writes only on a difference.

The third layer is the one that catches the rest: a poll where *some* value moved still notifies
every entity, and without this each of the forty-odd would write state because one of them had
news.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AvProCoordinator


class AvProEntity(CoordinatorEntity[AvProCoordinator]):
    """Common device link, availability and change-gated state writes."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AvProCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key

        # Entry-id based, never MAC-based. The *entry's* unique id may legitimately migrate from
        # a host-derived value to a MAC once the network body parses, and entity identities have
        # to survive that or the installation loses its history and its entity ids.
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._snapshot: Any = object()  # sentinel: never equal to a real value

        state = coordinator.matrix
        connections = set()
        if state.mac:
            connections.add((CONNECTION_NETWORK_MAC, state.mac))

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections=connections,
            manufacturer=MANUFACTURER,
            model=state.model,
            sw_version=state.firmware,
            name=entry.title,
            # The device's own web page. Always HTTP, even when the active transport is telnet --
            # this is a link for a human, not a control channel.
            configuration_url=f"http://{coordinator.transport.host}",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def _state_snapshot(self) -> Any:
        """Everything this entity renders, in one comparable value.

        Subclasses that expose attributes as well as a state must include them here, or an
        attribute-only change will never reach the UI.
        """
        return self.coordinator.optimistic(self._key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._snapshot = self._state_snapshot()

    def _handle_coordinator_update(self) -> None:
        """Write state only if this entity's own rendering changed."""
        current = self._state_snapshot()
        if current == self._snapshot:
            return
        self._snapshot = current
        self.async_write_ha_state()
