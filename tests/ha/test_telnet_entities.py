"""Signal on the telnet transport, at the Home Assistant layer (T-E5).

Telnet reads five kinds HTTP has no status endpoint for -- stream, input power, key lock, the LCD
timeout and the device address -- which is the substantive reason it is the primary transport.

It also cannot read one kind HTTP can: **signal**. That asymmetry is not a detail, because signal
is what `media_player.state` and the signal entities are built on, and they are among the few
entities enabled by default.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.ha_avpro_edge.const import CONF_TRANSPORT, TRANSPORT_HTTP

from .conftest import make_entry

pytestmark = pytest.mark.enable_socket


async def _setup(hass: HomeAssistant, fake, **options):
    entry = make_entry(fake.host, telnet_port=fake.telnet_port, **options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------------------------
# T-E5 -- signal, which telnet cannot read at all
# ---------------------------------------------------------------------------------------------
#
# T-E5 was written as "binary_sensor prefers telnet's boolean over string-emptiness when both are
# available". Both are never available: there is no telnet boolean. The grammar has no signal line
# because `GET STA` does not report one, and the identity seed reads only the web and network
# bodies. So on the default transport `signal_N` is never populated by anything.
#
# The consequence is not an unknown. `AvProSignalPresent.is_on` guards on the series being empty,
# but a series of four `None`s is not empty -- it returns `bool(None)`, which is False. Every port
# reports "Disconnected", confidently and permanently.


async def test_signal_is_not_silently_reported_as_disconnected_on_telnet(
    hass: HomeAssistant, fake
) -> None:
    """T-E5. Asserting "no signal" is worse than admitting the wire cannot see it.

    An automation conditioned on `binary_sensor.port_1_signal_present` being off would fire
    permanently, against a matrix whose ports are all live.
    """
    await _setup(hass, fake)

    state = hass.states.get("binary_sensor.port_1_signal_present")
    if state is not None:
        assert state.state != "off", (
            "the signal binary sensor reports 'Disconnected' on a transport that cannot read "
            "signal at all -- it must be unknown, or the entity must not exist"
        )


async def test_an_output_carrying_a_live_source_does_not_report_idle_forever(
    hass: HomeAssistant, fake
) -> None:
    """T-E5. `media_player.state` is derived from signal, so it inherits the same blindness.

    Over HTTP this output reports ON. Over telnet the signal series is all `None`, so it reports
    IDLE regardless of what the matrix is actually doing.
    """
    await _setup(hass, fake)

    state = hass.states.get("media_player.ac_mx44_auhd_output_1")
    assert state is not None
    assert state.state == "on", (
        "the routed input is carrying a signal on the fake, but the output reports "
        f"{state.state!r} -- signal is invisible on this transport"
    )


async def test_the_http_transport_still_sees_signal(hass: HomeAssistant, fake) -> None:
    """The control. If this fails too, the problem is the fake rather than the transport."""
    await _setup(hass, fake, **{CONF_TRANSPORT: TRANSPORT_HTTP})

    state = hass.states.get("media_player.ac_mx44_auhd_output_1")
    assert state is not None
    assert state.state == "on"
