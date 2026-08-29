"""Signal, and the difference between "no signal" and "not known".

The `bool(None)` defect lived here. `signals` is a **dense** tuple -- one entry per port, `None`
for a port never read -- so an unread series is not empty, it is `(None, None, None, None)`, which
is truthy. The old guard asked `if not signals`, which passed, and `bool(None)` then reported
every port as *Disconnected*: confidently, permanently, and in the one direction an automation
acts on.

That is why these tests exist at all. `binary_sensor.py` sat at 79% with the whole `is_on` property
uncovered, so the fix that closed the bug was itself untested -- and the identical expression in
`coordinator.diagnostics()` was still wrong. Nothing here is hypothetical: the `signal-absent`
fault reproduces the exact production shape, a telnet transport whose HTTP supplement cannot read.
"""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.ha_avpro_edge.binary_sensor import AvProSignalPresent

from .conftest import make_entry

pytestmark = pytest.mark.enable_socket

BINARY = "binary_sensor.ac_mx44_auhd_port_1_signal_present"


async def _enable_binary_sensors(hass: HomeAssistant, entry) -> None:
    """They are diagnostic and disabled by default, so a reload is needed to see them."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == "binary_sensor":
            registry.async_update_entity(entity.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------------------------
# The three answers is_on can give
# ---------------------------------------------------------------------------------------------


async def test_a_port_carrying_something_reports_on(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    await _enable_binary_sensors(hass, loaded_entry)
    assert hass.states.get(BINARY).state == STATE_ON


async def test_a_port_carrying_nothing_currently_reports_unknown(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """This asserted `off` first, and `off` is unreachable.

    An empty field is a real measurement -- the matrix looked and there was nothing -- but
    `_decode_info` maps it to `None`, the same value as a port never read. `is_on` then answers
    `None`, so a CONNECTIVITY binary sensor never reaches "Disconnected" on any transport.

    Pinned as it behaves rather than as it arguably should, because which of the two is right
    depends on what a real matrix returns for an unplugged input -- an observation nobody has
    made yet. See `test_a_blank_field_is_indistinguishable_from_an_unread_one`. If that is
    settled and the decode changes, this test failing is the correct outcome, not collateral.
    """
    await _enable_binary_sensors(hass, loaded_entry)
    coordinator = loaded_entry.runtime_data.coordinator
    assert hass.states.get(BINARY).state == STATE_ON

    fake.state.signals = ["", "", "", ""]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Asserted separately, so a failure says whether the reading reached the coordinator or
    # reached it and then failed to reach the entity.
    assert coordinator.matrix.signals == (None, None, None, None)
    assert hass.states.get(BINARY).state == STATE_UNKNOWN


async def test_off_is_currently_unreachable_whatever_the_matrix_says(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """The consequence, stated as an assertion so it cannot be forgotten.

    Every value `is_on` can return, over every signal string the device could plausibly send.
    `STATE_OFF` is absent from the results -- not because no case produces it, but because none
    can.
    """
    await _enable_binary_sensors(hass, loaded_entry)
    coordinator = loaded_entry.runtime_data.coordinator

    seen = set()
    for reading in ("3840X2160P@60HZ YUV420", "", "1920X1080P@60HZ", "NO SIGNAL"):
        fake.state.signals = [reading] * 4
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        seen.add(hass.states.get(BINARY).state)

    assert STATE_OFF not in seen, "off became reachable -- update the decode note and delete this"
    assert seen == {STATE_ON, STATE_UNKNOWN}


async def test_a_port_never_read_reports_unknown_not_off(hass: HomeAssistant) -> None:
    """The bug, reproduced end to end.

    Telnet cannot read signal, so the supplement reads it over HTTP -- and declares the capability
    readable before knowing whether that read will work, because it cannot know. When the endpoint
    is missing the entities therefore exist with nothing behind them, which is the one shape where
    "no signal" and "not measured" are distinguishable and the difference matters.

    `off` here would mean the matrix reported an idle port. It did not report anything.
    """
    from fake_avpro import FakeMatrix

    async with FakeMatrix(faults={"signal-absent"}) as fake:
        entry = make_entry(fake.host, telnet_port=fake.telnet_port)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data.coordinator
        assert coordinator.transport.name == "telnet"
        # The entities exist: the supplement says signal is readable regardless.
        assert coordinator.supports("signal")
        assert coordinator.matrix.signals == (None, None, None, None)

        await _enable_binary_sensors(hass, entry)
        assert hass.states.get(BINARY).state == STATE_UNKNOWN


async def test_a_port_the_matrix_does_not_have_says_nothing(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """The range guard, which no wiring reaches -- so it is asserted directly.

    A defensive branch that is never executed is a branch nobody knows the behaviour of. This one
    must answer `None`, for the same reason as above: a port outside the matrix has not been
    measured either.
    """
    coordinator = loaded_entry.runtime_data.coordinator
    beyond = AvProSignalPresent(coordinator, coordinator.matrix.port_count + 1)
    assert beyond.is_on is None


# ---------------------------------------------------------------------------------------------
# The change gate, which is the thing that was actually broken
# ---------------------------------------------------------------------------------------------


async def test_the_entity_follows_the_matrix_rather_than_freezing(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """It did not, for the whole life of the entity.

    ``AvProEntity`` writes state only when ``_state_snapshot()`` changes, and the base
    implementation reads ``coordinator.optimistic(self._key)``. This entity's key is
    ``signal_present_N`` -- chosen to be distinct from the sensor's so the two have distinct
    unique ids, and a key no transport reports. The gate therefore compared ``None`` to ``None``
    every time and returned early every time.

    The entity was frozen at whatever it read when the platform was set up. Unplug a source and
    it kept saying *Connected* until Home Assistant restarted, which is the exact opposite of
    what a connectivity sensor is for.

    Nothing caught it because a change gate that never fires is indistinguishable from a value
    that never changes -- and until this file existed, nothing ever asked this entity to change.
    """
    await _enable_binary_sensors(hass, loaded_entry)
    coordinator = loaded_entry.runtime_data.coordinator
    assert hass.states.get(BINARY).state == STATE_ON

    fake.state.signals = ["", "", "", ""]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(BINARY).state != STATE_ON, "the entity ignored the matrix"

    fake.state.signals = ["1920X1080P@60HZ"] * 4
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(BINARY).state == STATE_ON, "and it could not come back"


def test_an_entity_whose_key_is_not_a_state_key_must_override_the_gate() -> None:
    """The structural version, so the next one is caught by construction.

    ``_key`` does two jobs -- unique id, and the state key the change gate watches. They agree for
    every entity that keys off something the device reports, and diverge silently for one that
    does not. Divergence is legitimate; leaving the gate on the base implementation afterwards is
    not.
    """
    from custom_components.ha_avpro_edge.binary_sensor import AvProSignalPresent
    from custom_components.ha_avpro_edge.entity import AvProEntity

    assert AvProSignalPresent._state_snapshot is not AvProEntity._state_snapshot


def test_every_other_entity_keys_off_something_the_device_reports(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """The other half: nothing else is quietly keyed off a name that does not exist.

    Read from the registry rather than from the source, so an entity added later is included
    without anybody remembering to list it here. ``button`` is exempt for a real reason -- it has
    no state, so there is nothing for a gate to suppress.
    """
    from custom_components.ha_avpro_edge.avpro.http_decode import HTTP_READABLE
    from custom_components.ha_avpro_edge.avpro.state import split_key
    from custom_components.ha_avpro_edge.avpro.telnet_client import TELNET_READABLE

    real_kinds = HTTP_READABLE | TELNET_READABLE
    gated_by_own_rendering = {"binary_sensor"}  # asserted above
    stateless = {"button"}

    offenders = []
    for entity in er.async_entries_for_config_entry(er.async_get(hass), loaded_entry.entry_id):
        if entity.domain in gated_by_own_rendering | stateless:
            continue
        key = entity.unique_id.removeprefix(f"{loaded_entry.entry_id}_")
        if split_key(key)[0] not in real_kinds:
            offenders.append(entity.entity_id)

    assert not offenders, f"keyed off something no transport reports, so frozen: {offenders}"


# ---------------------------------------------------------------------------------------------
# The capability gate
# ---------------------------------------------------------------------------------------------


async def test_no_signal_entity_is_created_when_the_endpoint_is_absent(
    hass: HomeAssistant,
) -> None:
    """On HTTP the capability really is withdrawn, so the entity must not be created.

    An entity that reads `unknown` for ever is worse than an absent one: it looks like a device
    that is not answering rather than a firmware that never had the tab.
    """
    from fake_avpro import FakeMatrix

    from custom_components.ha_avpro_edge.const import CONF_TRANSPORT, TRANSPORT_HTTP

    async with FakeMatrix(faults={"signal-absent"}) as fake:
        entry = make_entry(
            fake.host, telnet_port=fake.telnet_port, **{CONF_TRANSPORT: TRANSPORT_HTTP}
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert not entry.runtime_data.coordinator.supports("signal")
        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        assert not [e for e in entities if e.domain == "binary_sensor"]
