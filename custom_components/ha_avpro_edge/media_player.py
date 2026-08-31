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

``turn_on``/``turn_off`` are **declared at runtime, from what the live transport can read**, not
hardcoded. They are backed by the output's stream control, which the CGI interface can write and
cannot read back: on that wire ``state`` would be a remembered guess, wrong after a restart and
wrong the moment anything else touched the matrix. Telnet reports ``OUT1 STREAM ON`` directly, so
there they are real.

Declaring a feature the transport cannot honour is worse than not having it. A greyed-out button
tells the user something true; a button that lies about what the matrix did does not.
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
from .avpro.models import signal_present
from .const import KEY_STREAM, KEY_VIDEO_ROUTE, port_key
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
    _attr_translation_key = "output"

    def __init__(self, coordinator: AvProCoordinator, output: int) -> None:
        super().__init__(coordinator, port_key(KEY_VIDEO_ROUTE, output))
        self._output = output
        # Named by index, never by the device's own port name: those are room names, and using
        # one here would bake site data into the entity id permanently.
        self._attr_translation_placeholders = {"index": str(output)}

        # Fixed at construction rather than computed per access: supported_features is read
        # constantly by the frontend, and the answer cannot change without a reload, since the
        # transport is chosen at setup.
        features = MediaPlayerEntityFeature.SELECT_SOURCE
        if coordinator.supports(KEY_STREAM):
            features |= MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
        self._attr_supported_features = features
        self._stream_key = port_key(KEY_STREAM, output)

    # -- naming --------------------------------------------------------------------------

    def _input_label(self, source: int) -> str:
        """The configured name for an input, falling back to a positional label."""
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
        """``OFF`` when the output's stream is off, ``ON`` when it carries a signal, else ``IDLE``.

        The ``OFF`` branch exists only where stream is readable. It means something specific and
        actionable: this integration turned the output off and can turn it back on. Everywhere
        else the distinction is between ``ON`` and ``IDLE``.

        ``IDLE`` rather than ``OFF`` for a routed output with no signal: nothing has been turned
        off. The output is working and the source at the other end is asleep or unplugged.
        Reporting ``OFF`` there would imply this integration could wake it, which it cannot.

        That paragraph described behaviour this property did not have. The test was
        ``signals[routed - 1]`` -- truthiness on free text -- and the matrix reports a dark port as
        the string ``NO SIGNAL``, which is truthy. So the exact case the docstring is about
        reported ``ON``. Measured, not reasoned about: an output held ``on`` for 82 seconds while
        its source sat unplugged. See :func:`avpro.models.signal_present`.
        """
        if self.coordinator.optimistic(self._stream_key) is False:
            return MediaPlayerState.OFF

        signals = self.coordinator.matrix.signals
        routed = self.coordinator.optimistic(self._key)
        if routed and 1 <= routed <= len(signals) and signal_present(signals[routed - 1]):
            return MediaPlayerState.ON
        return MediaPlayerState.IDLE

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Re-enable this output's HDMI stream."""
        await self.coordinator.async_set(self._stream_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Blank the display on this output by stopping its stream.

        Not a projector-style power command -- the matrix has no way to power anything down. It
        stops driving the output, which is what makes the screen go dark.
        """
        await self.coordinator.async_set(self._stream_key, False)

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
