"""Signal on the telnet transport, at the Home Assistant layer (T-E5).

Telnet reads five kinds HTTP has no status endpoint for -- stream, input power, key lock, the LCD
timeout and the device address -- which is the substantive reason it is the primary transport.

It also cannot read one kind HTTP can: **signal**. That asymmetry is not a detail, because signal
is what `media_player.state` and the signal entities are built on, and they are among the few
entities enabled by default.
"""

from __future__ import annotations

import asyncio

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


async def _until(hass: HomeAssistant, condition, *, what: str) -> None:
    """Wait for the device to actually apply a write.

    ``async_block_till_done`` settles Home Assistant's own event loop and nothing else. A telnet
    command crosses a real socket to the fake, which applies it and announces it back, so the
    assertion has to wait for the device rather than for the framework.

    Polled rather than slept: a sleep long enough for a loaded CI runner is dead time on every
    passing run, and one tuned on the development box is a flake in CI.
    """
    for _ in range(40):
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
        if condition():
            return
    raise AssertionError(f"timed out waiting for {what}")


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


# ---------------------------------------------------------------------------------------------
# T-E1 / T-E2 / T-E3 -- the controls only telnet can read
# ---------------------------------------------------------------------------------------------
#
# Asserted through the coordinator for the entities disabled by default, because a disabled entity
# has no state object to inspect. What matters for those is that the value is readable and
# writable at all; whether HA is currently rendering them is a user's decision, not a defect.


async def test_stream_is_real_state_rather_than_a_remembered_guess(
    hass: HomeAssistant, fake
) -> None:
    """T-E1. The reason this entity exists at all.

    It was deliberately withheld while HTTP was the only wire: that interface can write the
    control and cannot read it, so the switch would have shown whatever Home Assistant last sent,
    staying wrong after a restart and after anything else touched the matrix. Telnet reports
    ``OUT1 STREAM ON``, so this is a reading rather than a memory -- and it must not claim
    ``assumed_state``, which would tell the UI the opposite.
    """
    entry = await _setup(hass, fake)

    state = hass.states.get("switch.ac_mx44_auhd_output_1_stream")
    assert state is not None, "the stream switch was not created on telnet"
    assert state.state == "on"
    assert not state.attributes.get("assumed_state")

    # Changed on the device, not through us: a remembered value could not follow this.
    fake.state.stream[0] = False
    await entry.runtime_data.coordinator.async_refresh()
    await _until(
        hass,
        lambda: hass.states.get("switch.ac_mx44_auhd_output_1_stream").state == "off",
        what="the out-of-band stream change to reach the entity",
    )


async def test_stream_survives_a_reload_because_it_is_read_not_remembered(
    hass: HomeAssistant, fake
) -> None:
    """T-E1. The specific failure that kept this entity out of the HTTP-only design."""
    entry = await _setup(hass, fake)
    fake.state.stream[1] = False

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("switch.ac_mx44_auhd_output_2_stream").state == "off"


async def test_input_power_reflects_the_tmds_line(hass: HomeAssistant, fake) -> None:
    """T-E2. ``IN1 TMDS ON`` gates the clock the matrix drives towards a source."""
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator

    assert coordinator.optimistic("input_power_1") is True

    fake.state.input_power[0] = False
    await coordinator.async_refresh()
    await _until(
        hass,
        lambda: coordinator.optimistic("input_power_1") is False,
        what="the TMDS change to be read back",
    )


async def test_key_lock_reads_and_writes(hass: HomeAssistant, fake) -> None:
    """T-E3. The control that stops the front panel responding."""
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator

    assert coordinator.optimistic("key_lock") is False

    await coordinator.async_set("key_lock", True)
    await _until(hass, lambda: fake.state.key_lock is True, what="the key lock write to land")

    assert any("SET KEY LOCK ON" in c.upper() for c in fake.telnet_commands)


async def test_the_lcd_timeout_reads_and_writes_by_label(hass: HomeAssistant, fake) -> None:
    """T-E3. Written as an option key, sent as the wire code the matrix accepts."""
    from custom_components.ha_avpro_edge.avpro.models import LcdTimeout

    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator

    # The fake starts at T2, which is the third of the four.
    assert coordinator.optimistic("lcd_timeout") is LcdTimeout.SECONDS_30

    await coordinator.async_set("lcd_timeout", LcdTimeout.ALWAYS_ON)
    await _until(hass, lambda: fake.state.lcd_timeout == 0, what="the LCD write to land")

    assert any("SET LCD ON T0" in c.upper() for c in fake.telnet_commands)


async def test_the_lcd_select_offers_exactly_the_four_the_matrix_accepts(
    hass: HomeAssistant, fake
) -> None:
    """T-E3. Measured on hardware: T0-T3 accepted, T4 and T5 refused.

    An option the device would reject is worse than a missing one -- it fails silently, at the
    moment somebody picks it.
    """
    from custom_components.ha_avpro_edge.select import DEVICE_LEVEL

    entry = await _setup(hass, fake)
    lcd = next(d for d in DEVICE_LEVEL if d.kind == "lcd_timeout")
    options = lcd.options_for(entry.runtime_data.coordinator.matrix)
    assert options == ["always_on", "seconds_15", "seconds_30", "seconds_60"]


# ---------------------------------------------------------------------------------------------
# T-E4 -- media_player declares turn on/off only where it can honour it
# ---------------------------------------------------------------------------------------------


def _features(hass: HomeAssistant) -> int:
    return hass.states.get("media_player.ac_mx44_auhd_output_1").attributes["supported_features"]


async def test_turn_on_and_off_are_offered_on_telnet(hass: HomeAssistant, fake) -> None:
    """T-E4. Backed by the output's stream, which this wire can read back."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    await _setup(hass, fake)
    features = _features(hass)
    assert features & MediaPlayerEntityFeature.TURN_ON
    assert features & MediaPlayerEntityFeature.TURN_OFF
    assert features & MediaPlayerEntityFeature.SELECT_SOURCE


async def test_turn_on_and_off_are_withheld_on_http(hass: HomeAssistant, fake) -> None:
    """T-E4. Declaring a feature the transport cannot honour is worse than not having it.

    A greyed-out control tells the user something true. A button that lies about what the matrix
    did does not -- and over HTTP the state behind it would be a remembered guess.
    """
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    await _setup(hass, fake, **{CONF_TRANSPORT: TRANSPORT_HTTP})
    features = _features(hass)
    assert not features & MediaPlayerEntityFeature.TURN_ON
    assert not features & MediaPlayerEntityFeature.TURN_OFF
    assert features & MediaPlayerEntityFeature.SELECT_SOURCE


async def test_turning_an_output_off_stops_its_stream(hass: HomeAssistant, fake) -> None:
    """T-E4. Not a power command: the matrix stops driving the output, and the screen goes dark."""
    await _setup(hass, fake)

    await hass.services.async_call(
        "media_player",
        "turn_off",
        {"entity_id": "media_player.ac_mx44_auhd_output_1"},
        blocking=True,
    )
    await _until(hass, lambda: fake.state.stream[0] is False, what="the stream write to land")

    assert hass.states.get("media_player.ac_mx44_auhd_output_1").state == "off"


async def test_an_output_with_no_signal_is_idle_not_off(hass: HomeAssistant, fake) -> None:
    """T-E4. ``off`` now means something specific, so it must not be used for anything else.

    A routed output whose source is asleep is not off -- nothing turned it off, and this
    integration cannot wake the source. Conflating the two would make the distinction useless.
    """
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.coordinator

    fake.state.signals = ["", "", "", ""]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get("media_player.ac_mx44_auhd_output_1")
    assert state.state == "idle"
