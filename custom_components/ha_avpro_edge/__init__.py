"""AVPro Edge — Home Assistant integration for AUHD-series HDMI matrix switchers.

Talks to the matrix over its CGI interface on port 80, never over telnet. The telnet server on
this hardware accepts one client at a time, and in a typical installation that slot belongs to a
control system the house depends on; see ``avpro/client.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .avpro.client import AvProClient
from .avpro.http_transport import HttpTransport
from .const import (
    CONF_ALLOW_WRITES,
    CONF_POLLING_PROFILE,
    DEFAULT_ALLOW_WRITES,
    DEFAULT_POLLING_PROFILE,
    POLLING_PROFILES,
)
from .coordinator import AvProCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class AvProRuntime:
    """What the entry keeps alive while it is loaded."""

    coordinator: AvProCoordinator
    #: Held so the options listener can flip read-only mode without reloading the entry. The
    #: coordinator deliberately cannot reach it -- it talks to a Transport, not to a wire.
    client: AvProClient


type AvProConfigEntry = ConfigEntry[AvProRuntime]


def _interval(entry: ConfigEntry) -> timedelta:
    profile = entry.options.get(CONF_POLLING_PROFILE, DEFAULT_POLLING_PROFILE)
    return timedelta(
        seconds=POLLING_PROFILES.get(profile, POLLING_PROFILES[DEFAULT_POLLING_PROFILE])
    )


async def async_setup_entry(hass: HomeAssistant, entry: AvProConfigEntry) -> bool:
    """Set one matrix up."""
    client = AvProClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        allow_writes=entry.options.get(CONF_ALLOW_WRITES, DEFAULT_ALLOW_WRITES),
        # Per-entry random stream for the cache-buster. Never random.seed(), which would mutate
        # global state for everything else in the process and make two matrices move in lockstep.
        seed=entry.entry_id,
    )

    # The coordinator talks to a Transport, never to a wire directly. Today that is always HTTP;
    # the telnet transport slots in here without the coordinator or any entity changing.
    transport = HttpTransport(client)

    coordinator = AvProCoordinator(hass, entry, transport, update_interval=_interval(entry))
    await coordinator.async_prepare()

    # Raises ConfigEntryNotReady on failure, which is `test-before-setup`: entities are never
    # created against a matrix we have not actually read.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = AvProRuntime(coordinator=coordinator, client=client)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AvProConfigEntry) -> bool:
    """Tear one matrix down."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: AvProConfigEntry) -> None:
    """Apply options in place rather than reloading.

    Reloading would drop every entity and rebuild it to change a polling interval, which on an
    installation driving wall panels is a visible blink across the house.
    """
    coordinator = entry.runtime_data.coordinator
    entry.runtime_data.client.allow_writes = entry.options.get(
        CONF_ALLOW_WRITES, DEFAULT_ALLOW_WRITES
    )
    # The setter reschedules the timer for us.
    coordinator.update_interval = _interval(entry)
    coordinator.async_update_listeners()
