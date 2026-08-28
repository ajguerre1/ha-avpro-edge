"""One ``media_player`` per matrix output.

**Why `media_player` for a device with no media.** Home Assistant has no matrix or router domain,
and `media_player` with ``source_list``/``select_source`` is the established convention for this
hardware -- the `blackbird` integration in core does exactly this. It brings the source-picker
card, voice assistants, and ``media_player.select_source`` in scripts, none of which a bare
`select` would.

**What it deliberately does not claim.** Only ``SELECT_SOURCE``. This model has no volume and no
mute worth the name: the extracted-audio enable is a separate de-embedded feed that does not
change what the room hears, so wiring ``volume_mute`` to it would misreport the hardware. It ships
as a plainly named switch instead.

``turn_on``/``turn_off`` are absent for the same reason. The TMDS control that would back them has
no status endpoint on the firmware this was built against, so ``state`` would be a remembered
guess -- wrong after a restart, and wrong the moment anything else touched the matrix.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AvProConfigEntry
from .const import KEY_VIDEO_ROUTE, port_key
from .coordinator import AvProCoordinator
from .entity import AvProEntity

# Serialisation is already structural: the client holds one lock across every request, so a
# per-platform semaphore would only add latency without changing what reaches the device.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AvProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        AvProOutput(coordinator, output) for output in range(1, coordinator.matrix.port_count + 1)
    )


class AvProOutput(AvProEntity, MediaPlayerEntity):
    """A single matrix output, presented as a source selector."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = MediaPlayerEntityFeature.SELECT_SOURCE
    _attr_translation_key = "output"

    def __init__(self, coordinator: AvProCoordinator, output: int) -> None:
        super().__init__(coordinator, port_key(KEY_VIDEO_ROUTE, output))
        self._output = output
        # Named by index, never by the device's own port name: those are room names, and using
        # one here would bake site data into the entity id permanently.
        self._attr_translation_placeholders = {"index": str(output)}

    # -- naming --------------------------------------------------------------------------

    def _input_label(self, source: int) -> str:
        """The owner's name for an input, falling back to a positional label."""
        return self.coordinator.matrix.input_name(source) or f"Input {source}"

    @property
    def source_list(self) -> list[str]:
        """The matrix's inputs, by the names configured on the device.

        Real names are correct here: the rule is that site data must not enter the repository,
        not that it must not reach Home Assistant. A picker showing "Input 3" when the matrix
        itself says "Apple TV" would be worse for no benefit.
        """
        return [
            self._input_label(source) for source in range(1, self.coordinator.matrix.port_count + 1)
        ]

    @property
    def source(self) -> str | None:
        routed = self.coordinator.optimistic(self._key)
        return self._input_label(routed) if routed else None

    # -- state ---------------------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        """``ON`` when the routed input is carrying a signal, otherwise ``IDLE``.

        ``IDLE`` rather than ``OFF``: nothing has been turned off. The output is routed and
        working; the source at the other end is simply asleep or unplugged. Reporting ``OFF``
        would imply this integration could turn it back on, which it cannot.
        """
        signals = self.coordinator.matrix.signals
        routed = self.coordinator.optimistic(self._key)
        if routed and 1 <= routed <= len(signals) and signals[routed - 1]:
            return MediaPlayerState.ON
        return MediaPlayerState.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        matrix = self.coordinator.matrix
        routed = self.coordinator.optimistic(self._key)
        return {
            "output_index": self._output,
            # The device's own label for this output, for automations that want it. Deliberately
            # an attribute rather than part of the entity name.
            "port_name": matrix.output_name(self._output),
            "source_index": routed,
            "detected_signal": (
                matrix.signals[routed - 1]
                if routed and 1 <= routed <= len(matrix.signals)
                else None
            ),
        }

    def _state_snapshot(self) -> Any:
        """Everything rendered, so an attribute-only change still reaches the UI."""
        return (self.source, self.state, tuple(self.source_list), self.available)

    # -- commands ------------------------------------------------------------------------

    async def async_select_source(self, source: str) -> None:
        """Route this output to the named input."""
        for index in range(1, self.coordinator.matrix.port_count + 1):
            if self._input_label(index) == source:
                await self.coordinator.async_set(self._key, index)
                return
        raise ValueError(f"{source!r} is not one of this matrix's inputs")
