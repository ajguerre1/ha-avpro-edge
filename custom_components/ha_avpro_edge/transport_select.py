"""Choosing a wire, and falling back when the preferred one is unavailable.

**Telnet is primary. Always speak telnet unless you don't need to.** This module is where that
rule is enforced, so it is worth being explicit about what "unless you don't need to" means:

| Setting | Behaviour |
|---|---|
| ``auto`` | Telnet. If it is unavailable, fall back to HTTP and retry telnet periodically |
| ``telnet`` | Telnet only. If unavailable, the entry is not ready -- do not silently degrade |
| ``http`` | **Never open port 23.** The escape hatch |

The ``http`` setting is honoured absolutely and is checked before anything else happens: a setting
that is obeyed only most of the time is not a setting. A test asserts nothing connects under it.
Its original justification -- an installation whose control system needs that socket -- no longer
applies here, since Home Assistant is now the only thing driving this matrix, but the hatch stays.
It is the way to run without touching port 23 while diagnosing anything that looks like a control
socket problem.

**Falling back is a fault, not an accommodation.** That is the substantive change from the
original design. When another control system legitimately owned the socket, degrading quietly to
HTTP was the polite and correct answer. With nothing else driving the device, an unavailable
control socket means something is wrong -- a rebooting matrix, a network blip, a wedged session --
and none of those should be absorbed in silence. So the fallback still happens, because leaving
the house with no control at all would be worse, but it raises a repair issue and re-checks every
minute rather than every five.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .avpro.client import AvProClient
from .avpro.http_transport import HttpTransport
from .avpro.supplement import SupplementedTransport
from .avpro.telnet_client import TelnetBusy, TelnetError, TelnetTransport
from .avpro.transport import Transport
from .const import (
    CONF_ALLOW_WRITES,
    CONF_POLLING_PROFILE,
    CONF_TELNET_PORT,
    CONF_TRANSPORT,
    DEFAULT_ALLOW_WRITES,
    DEFAULT_POLLING_PROFILE,
    DEFAULT_TELNET_PORT,
    DEFAULT_TRANSPORT,
    POLLING_PROFILES,
    TELNET_RETRY_INTERVAL,
    TRANSPORT_HTTP,
    TRANSPORT_TELNET,
)

_LOGGER = logging.getLogger(__name__)


def transport_setting(entry: ConfigEntry) -> str:
    return entry.options.get(CONF_TRANSPORT, DEFAULT_TRANSPORT)


def signal_interval(entry: ConfigEntry) -> float:
    """How often to read the signal endpoint when telnet is carrying everything else.

    The user's polling profile, deliberately -- not the 60 s safety-net interval the coordinator
    uses for a pushing transport. That interval is justified by pushes carrying normal operation,
    and signal does not push: a source waking up would take up to a minute to appear.
    """
    profile = entry.options.get(CONF_POLLING_PROFILE, DEFAULT_POLLING_PROFILE)
    return float(POLLING_PROFILES.get(profile, POLLING_PROFILES[DEFAULT_POLLING_PROFILE]))


def wants_telnet(entry: ConfigEntry) -> bool:
    """Whether this entry may open the control socket at all.

    The single check that stands between the user's instruction and the house's control system.
    """
    return transport_setting(entry) != TRANSPORT_HTTP


async def async_select_transport(
    hass: HomeAssistant, entry: ConfigEntry, client: AvProClient
) -> Transport:
    """Return a connected transport, honouring the entry's setting.

    Raises :class:`ConfigEntryNotReady` when telnet was explicitly required and is unavailable --
    silently degrading would hide the very thing the user asked for.
    """
    setting = transport_setting(entry)

    if setting == TRANSPORT_HTTP:
        # No telnet object is even constructed. Nothing to accidentally connect.
        _LOGGER.debug("%s: transport forced to HTTP; port 23 will not be touched", entry.title)
        return HttpTransport(client)

    # The host may already carry an HTTP port; telnet has its own. Strip whatever is there and
    # use the control port, which defaults to 23 but is settable on the device.
    address = entry.data[CONF_HOST].partition(":")[0]
    port = entry.data.get(CONF_TELNET_PORT, DEFAULT_TELNET_PORT)

    telnet = TelnetTransport(
        f"{address}:{port}",
        allow_writes=entry.options.get(CONF_ALLOW_WRITES, DEFAULT_ALLOW_WRITES),
        # Per-entry stream, so two matrices never reconnect in lockstep.
        seed=entry.entry_id,
    )

    # Telnet cannot read signal detection -- established against the live matrix, not assumed:
    # 32 command spellings across two probe rounds all answered CMD ERR, and GET STA carries no
    # signal line. Since media_player.state and eight entities are built on it, the telnet
    # transport is supplemented with the one HTTP endpoint that has it. See avpro/supplement.py
    # for why this is the documented exception rather than a breach of the transport rule.
    supplemented = SupplementedTransport(telnet, client, interval=signal_interval(entry))

    try:
        await supplemented.async_connect()
    except (TelnetBusy, TelnetError) as err:
        if setting == TRANSPORT_TELNET:
            raise ConfigEntryNotReady(str(err)) from err
        # Home Assistant is the only thing that should be driving this matrix, so this is a fault
        # rather than an accommodation. Falling back keeps the matrix controllable -- refusing to
        # load would leave the house with nothing -- but it is not a quiet outcome any more.
        _LOGGER.warning(
            "%s: the telnet control socket is unavailable (%s). Falling back to the HTTP "
            "interface, which works but cannot see output stream state, input power, key lock or "
            "the LCD timeout, and must poll rather than being pushed to. Nothing else should be "
            "holding this socket; re-checking every %ss",
            entry.title,
            err,
            int(TELNET_RETRY_INTERVAL),
        )
        return HttpTransport(client)

    _LOGGER.debug("%s: using the telnet transport, with signal supplemented over HTTP", entry.title)
    return supplemented


@callback
def async_watch_for_telnet(hass: HomeAssistant, entry: ConfigEntry) -> Callable[[], None]:
    """Re-check the control socket while running degraded, and reload once it is ours again.

    Started only after a fallback. Without it the entry stays on HTTP until somebody notices and
    reloads by hand -- which, for a fault that usually clears itself in seconds, means an
    integration permanently missing four kinds of control because the matrix rebooted once.

    Recovery goes through a reload rather than swapping the transport in place. Capabilities decide
    which entities exist, so gaining four of them is exactly what a reload is for; doing it live
    would mean adding entities to a running platform and reconciling the coordinator's state
    underneath them.
    """

    async def _probe(_now: Any = None) -> None:
        address = entry.data[CONF_HOST].partition(":")[0]
        port = entry.data.get(CONF_TELNET_PORT, DEFAULT_TELNET_PORT)
        # Never writes, and says so: this is a liveness check, not a session.
        probe = TelnetTransport(f"{address}:{port}", allow_writes=False, seed=entry.entry_id)
        try:
            await probe.async_connect()
        except TelnetError:
            return
        finally:
            # Released either way. Holding it here would be the one thing that could genuinely
            # keep the real session from starting.
            await probe.async_disconnect()

        _LOGGER.info("%s: the telnet control socket is available again; reloading", entry.title)
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    return async_track_time_interval(
        hass, _probe, timedelta(seconds=TELNET_RETRY_INTERVAL), cancel_on_shutdown=True
    )
