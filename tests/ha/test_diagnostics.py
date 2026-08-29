"""Diagnostics, and the one property that actually matters about them.

Found by the quality-scale audit: `diagnostics` was marked **done** at Gold with a comment about
carefully disclosing shapes rather than values -- and the module had **0% coverage**. Nothing
executed it, let alone checked its output.

That is the wrong thing to leave untested here. This repository's hardest rule is that no site
data escapes, and a diagnostics dump is written to be pasted into a public issue by someone who
will not read it first. The claim "port names, the host, the MAC and the network body are
deliberately absent" was a comment, not a test.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant

from custom_components.ha_avpro_edge.diagnostics import async_get_config_entry_diagnostics

pytestmark = pytest.mark.enable_socket

#: Everything the fake matrix reports that would be site data on the real one. The fake's values
#: are invented, but they occupy exactly the fields a real unit fills with room and source names.
SITE_DATA = ("OutA", "OutB", "OutC", "OutD", "SrcA", "SrcB", "SrcC", "SrcD", "AA:BB:CC:DD:EE:FF")


async def test_the_dump_contains_no_port_names_host_or_mac(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """The property the module exists for, asserted rather than commented.

    Serialised and searched as text rather than walked key by key: a leak that arrives through a
    field nobody thought of is exactly the kind this has to catch, and that is the one a
    key-by-key check would miss.
    """
    payload = await async_get_config_entry_diagnostics(hass, loaded_entry)
    text = json.dumps(payload, default=str)

    for secret in SITE_DATA:
        assert secret not in text, f"diagnostics disclosed {secret!r}"
    assert fake.host not in text, "diagnostics disclosed the matrix's address"
    # The host arrives in entry.data, so the key may be named but never its value.
    assert "host" in json.dumps(payload["entry"]["data_keys"])


async def test_the_dump_still_says_something_useful(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """A dump that disclosed nothing at all would also pass the test above.

    So this pins the other half: the things a bug report genuinely needs are present.
    """
    payload = await async_get_config_entry_diagnostics(hass, loaded_entry)
    matrix = payload["matrix"]

    assert matrix["transport"] == "telnet"
    assert matrix["pushes"] is True
    assert matrix["model"] == "AC-MX44-AUHD"
    assert matrix["port_count"] == 4
    assert matrix["census_done"] is True
    assert matrix["routing"]["video"] == [1, 2, 3, 4]
    # Counts, not names.
    assert matrix["named_outputs"] == 4
    assert matrix["named_inputs"] == 4
    assert matrix["has_mac"] is True


async def test_it_reports_the_shape_of_the_unique_id_not_the_id(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """A MAC-derived unique id *is* the MAC, so only which kind it is may be disclosed."""
    payload = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert payload["entry"]["unique_id_kind"] == "mac"
    assert "AA:BB:CC" not in json.dumps(payload, default=str)


async def test_an_unread_port_is_not_reported_as_having_no_signal(hass: HomeAssistant) -> None:
    """The `bool(None)` defect, second instance -- and this is the worse of the two places.

    Fixing it in the binary sensor left `[bool(s) for s in state.signals]` untouched here, where
    `None` becomes `False`. A diagnostics dump is written to be pasted into a bug report by
    someone who will not read it first, so "every port has no signal" is a confident wrong fact
    handed to whoever is trying to help -- and it points away from the actual problem, which is
    that signal was never read at all.
    """
    from fake_avpro import FakeMatrix

    from .conftest import make_entry

    async with FakeMatrix(faults={"signal-absent"}) as fake:
        entry = make_entry(fake.host, telnet_port=fake.telnet_port)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        payload = await async_get_config_entry_diagnostics(hass, entry)
        assert payload["matrix"]["signal_present"] == [None, None, None, None]


async def test_a_measured_port_still_reports_a_boolean(
    hass: HomeAssistant, fake, loaded_entry
) -> None:
    """Guards the fix above: returning `None` unconditionally would pass it too.

    Port 3 is `None` and that is the fake being faithful -- its default signal list leaves that
    port blank, and a blank field decodes to `None` rather than to "nothing here". So this row
    also happens to record the conflation documented in
    `tests/test_http_decode.py::test_a_blank_field_is_indistinguishable_from_an_unread_one`: in a
    dump meant to help somebody debug, port 3 reads the same as a port never polled.
    """
    payload = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert payload["matrix"]["signal_present"] == [True, True, None, True]


async def test_it_is_json_serialisable(hass: HomeAssistant, fake, loaded_entry) -> None:
    """Home Assistant serialises this to hand to the browser.

    Enums and frozensets round-trip through ``default=str`` in the tests above, which would hide a
    payload the real download endpoint chokes on.
    """
    payload = await async_get_config_entry_diagnostics(hass, loaded_entry)
    json.dumps(payload)
