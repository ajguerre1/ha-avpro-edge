"""AVPro Edge — Home Assistant integration for AUHD-series HDMI matrix switchers.

Talks to the matrix over its telnet command set on port 23, falling back to the CGI interface on
port 80 when that socket cannot be had. Telnet is primary because it pushes changes within
~300-400 ms, reads the whole device in one command, and is the only wire that can see output
stream state, input power, key lock and the LCD timeout.

The telnet server accepts **one client at a time**, which is a property of the hardware. In a
typical installation that slot belongs to a third-party control system the site depends on. Where
nothing else needs it, an unavailable control socket is a fault rather than a neighbour's claim --
see ``transport_select.py``.

Signal detection is the one thing telnet cannot read at all, established by probing the live unit
rather than assumed. It is supplemented over HTTP; see ``avpro/supplement.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .avpro.client import AvProClient, AvProConnectionError
from .avpro.http_decode import decode
from .avpro.protocol import StatusEndpoint
from .avpro.transport import Transport
from .const import (
    CONF_ALLOW_WRITES,
    CONF_POLLING_PROFILE,
    DEFAULT_ALLOW_WRITES,
    DEFAULT_POLLING_PROFILE,
    DOMAIN,
    ISSUE_TELNET_UNAVAILABLE,
    POLLING_PROFILES,
    PUSH_SAFETY_NET_INTERVAL,
)
from .coordinator import AvProCoordinator
from .services import async_register_services
from .transport_select import async_select_transport, async_watch_for_telnet, wants_telnet

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once, for the integration rather than per entry.

    Per the ``action-setup`` quality-scale rule. An automation referencing ``route_all`` should
    validate whether or not a matrix happens to be loaded right now -- otherwise a device that is
    briefly unreachable at startup turns every automation using it into a configuration error.
    """
    async_register_services(hass)
    return True


@dataclass
class AvProRuntime:
    """What the entry keeps alive while it is loaded."""

    coordinator: AvProCoordinator
    #: Held so the options listener can flip read-only mode without reloading the entry. The
    #: coordinator deliberately cannot reach it -- it talks to a Transport, not to a wire.
    client: AvProClient


type AvProConfigEntry = ConfigEntry[AvProRuntime]


def _interval(entry: ConfigEntry, transport: Transport) -> timedelta:
    """How often to read.

    A pushing transport is not polled on the user's profile at all -- doing so would be asking a
    device that already volunteers its changes. What remains is a slow safety net, so a missed
    push cannot leave state stale indefinitely.
    """
    if transport.pushes:
        return timedelta(seconds=PUSH_SAFETY_NET_INTERVAL)
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

    # Telnet if it will have us, HTTP otherwise. The coordinator is handed a connected
    # Transport and never learns which wire it got.
    transport = await async_select_transport(hass, entry, client)

    coordinator = AvProCoordinator(
        hass, entry, transport, update_interval=_interval(entry, transport)
    )
    await coordinator.async_prepare()

    if transport.name != "http":
        await _async_seed_identity(client, coordinator)

    # Raises ConfigEntryNotReady on failure, which is `test-before-setup`: entities are never
    # created against a matrix we have not actually read.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = AvProRuntime(coordinator=coordinator, client=client)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _async_report_degraded(hass, entry, transport)
    return True


@callback
def _async_report_degraded(
    hass: HomeAssistant, entry: AvProConfigEntry, transport: Transport
) -> None:
    """Surface a fallback the user did not ask for, and start watching for recovery.

    Only when the entry wanted telnet and did not get it. Someone who chose ``http`` is getting
    exactly what they asked for and must not be nagged about it -- a repair issue that appears
    for a deliberate configuration is noise, and noise is how the useful ones get ignored.
    """
    issue_id = f"{ISSUE_TELNET_UNAVAILABLE}_{entry.entry_id}"

    if not wants_telnet(entry) or transport.name != "http":
        # Covers recovery too: a reload that lands on telnet clears the issue on its way past.
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TELNET_UNAVAILABLE,
        translation_placeholders={"name": entry.title},
    )
    entry.async_on_unload(async_watch_for_telnet(hass, entry))


async def async_unload_entry(hass: HomeAssistant, entry: AvProConfigEntry) -> bool:
    """Tear one matrix down."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: AvProConfigEntry) -> None:
    """Apply options in place rather than reloading.

    Reloading would drop every entity and rebuild it to change a polling interval, which on an
    installation driving many dashboards is a visible blink across all of them.
    """
    coordinator = entry.runtime_data.coordinator
    allow_writes = entry.options.get(CONF_ALLOW_WRITES, DEFAULT_ALLOW_WRITES)
    entry.runtime_data.client.allow_writes = allow_writes
    # Whichever wire is live also needs telling. The telnet transport holds its own flag because
    # it does not go through the HTTP client at all.
    if hasattr(coordinator.transport, "allow_writes"):
        coordinator.transport.allow_writes = allow_writes
    # The setter reschedules the timer for us.
    coordinator.update_interval = _interval(entry, coordinator.transport)
    coordinator.async_update_listeners()


async def _async_seed_identity(client: AvProClient, coordinator: AvProCoordinator) -> None:
    """Read model, firmware and the port names over HTTP, once.

    Telnet cannot report any of them -- ``GET STA`` covers routing and settings and stops there.
    So this is the same documented exception that covers renaming: HTTP is used for an operation
    only HTTP has, and for nothing else. It runs once at setup rather than on a schedule, because
    port names change when somebody renames them, which a reload picks up.

    Best-effort. A matrix whose identity body will not parse is still perfectly usable; the
    source picker just falls back to positional labels.
    """
    for endpoint in (StatusEndpoint.WEB, StatusEndpoint.NETWORK):
        try:
            parsed = await client.async_read(endpoint)
        except AvProConnectionError as err:
            _LOGGER.debug("identity read of %s failed: %s", endpoint.value, err)
            continue
        coordinator.seed(decode(endpoint, parsed, port_count=coordinator.matrix.port_count))
