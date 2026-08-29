"""``MatrixState`` and ``apply`` (T-S1..T-S4).

These used to test seven ``fold_*`` functions, one per HTTP endpoint. There is now one ``apply``
that takes a transport-neutral report, so the endpoint-shaped cases moved to
``test_http_decode.py`` where they belong -- they are facts about the CGI interface, not about
the state model.

Every body here uses invented names. The shapes are the device's; the values are not.
"""

from __future__ import annotations

import pytest
from avpro.report import EMPTY, DeviceReport
from avpro.state import DEFAULT_PORT_COUNT, MatrixState, apply, split_key

ROUTES = DeviceReport.update(
    {"video_route_1": 1, "video_route_2": 2, "video_route_3": 3, "video_route_4": 4}
)
NAMES = DeviceReport.update(
    {
        "model": "AC-MX44-AUHD",
        "firmware": "V1.41",
        "output_name_1": "OutA",
        "output_name_2": "OutB",
        "output_name_3": "OutC",
        "output_name_4": "OutD",
        "input_name_1": "SrcA",
        "input_name_2": "SrcB",
        "input_name_3": "SrcC",
        "input_name_4": "SrcD",
    }
)


def _loaded() -> MatrixState:
    return apply(apply(MatrixState(), ROUTES), NAMES)


# ---------------------------------------------------------------------------------------------
# T-S1 / T-S2 -- value semantics, which is what makes always_update=False safe
# ---------------------------------------------------------------------------------------------


def test_applying_the_same_report_twice_yields_an_equal_state() -> None:
    assert _loaded() == _loaded()


def test_re_applying_an_identical_report_returns_the_same_object() -> None:
    """Cheap identity check, so the caller's `is` test is as good as its `==` test."""
    state = _loaded()
    assert apply(state, ROUTES) is state


def test_a_changed_value_makes_the_state_unequal() -> None:
    before = _loaded()
    after = apply(before, DeviceReport.update({"video_route_1": 3}))
    assert after != before
    assert after.get("video_route_1") == 3


def test_an_empty_report_changes_nothing() -> None:
    state = _loaded()
    assert apply(state, EMPTY) is state


def test_the_state_is_frozen() -> None:
    state = MatrixState()
    with pytest.raises((AttributeError, TypeError)):
        state.values = {}  # type: ignore[misc]


# ---------------------------------------------------------------------------------------------
# Applying never clears -- the rule that lets two transports share one state
# ---------------------------------------------------------------------------------------------


def test_a_report_never_clears_what_it_does_not_mention() -> None:
    """A telnet push naming one output must not blank the other three."""
    state = _loaded()
    after = apply(state, DeviceReport.update({"video_route_1": 4}))
    assert after.get("video_route_1") == 4
    assert after.get("video_route_2") == 2
    assert after.output_name(1) == "OutA"


def test_even_a_census_does_not_clear_another_transports_contribution() -> None:
    """T-R2. No single report is authoritative about the whole device.

    Telnet's GET STA knows nothing of the port names; the HTTP census knows nothing of the output
    stream state. If a census cleared what it omitted, each transport would erase the other's
    contribution on every cycle.
    """
    state = apply(_loaded(), DeviceReport.update({"stream_1": True}))
    after = apply(state, DeviceReport.census({"video_route_1": 2}))
    assert after.get("stream_1") is True
    assert after.output_name(1) == "OutA"


def test_a_later_report_wins_where_they_overlap() -> None:
    state = apply(_loaded(), DeviceReport.update({"video_route_1": 3}))
    assert state.get("video_route_1") == 3


# ---------------------------------------------------------------------------------------------
# T-S3 / T-S4 -- unknown keys, and what None means
# ---------------------------------------------------------------------------------------------


def test_an_unknown_key_is_stored_rather_than_raising() -> None:
    """A firmware reporting something new must not break the fold."""
    state = apply(MatrixState(), DeviceReport.update({"something_new_2": 7}))
    assert state.get("something_new_2") == 7
    assert state.has("something_new")


def test_a_key_never_reported_reads_as_none_not_as_a_default() -> None:
    """Reporting a plausible default would be indistinguishable from the device's real state."""
    state = _loaded()
    assert state.get("scaler_1") is None
    assert state.get("key_lock") is None
    assert state.get("anything_at_all") is None


def test_a_fallback_is_returned_when_asked_for() -> None:
    assert MatrixState().get("video_route_1", 99) == 99


# ---------------------------------------------------------------------------------------------
# Key parsing
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("video_route_2", ("video_route", 2)),
        ("edid_10", ("edid", 10)),
        ("bind_mode", ("bind_mode", None)),
        ("mac", ("mac", None)),
        ("key_lock", ("key_lock", None)),
    ],
)
def test_keys_split_into_kind_and_index(key: str, expected: tuple[str, int | None]) -> None:
    assert split_key(key) == expected


def test_a_device_level_key_ending_in_a_word_is_not_mistaken_for_an_index() -> None:
    """`bind_mode` must not parse as kind `bind` index `mode`."""
    kind, index = split_key("bind_mode")
    assert index is None
    assert kind == "bind_mode"


# ---------------------------------------------------------------------------------------------
# Derived views
# ---------------------------------------------------------------------------------------------


def test_series_returns_a_dense_tuple_indexed_from_zero() -> None:
    assert _loaded().series("video_route") == (1, 2, 3, 4)


def test_series_pads_missing_entries_with_none() -> None:
    state = apply(MatrixState(), DeviceReport.update({"video_route_1": 1, "video_route_4": 4}))
    assert state.series("video_route") == (1, None, None, 4)


def test_port_count_is_derived_from_the_device_not_assumed() -> None:
    """An eight-output unit must size itself from what it reports."""
    wide = DeviceReport.update({f"video_route_{n}": 1 for n in range(1, 9)})
    state = apply(MatrixState(), wide)
    assert state.port_count == 8
    assert len(state.video_routes) == 8


def test_port_count_falls_back_before_anything_is_known() -> None:
    assert MatrixState().port_count == DEFAULT_PORT_COUNT


def test_names_are_exposed_one_based_and_bounds_safe() -> None:
    state = _loaded()
    assert state.output_name(1) == "OutA"
    assert state.input_name(4) == "SrcD"
    assert state.output_name(0) is None
    assert state.output_name(99) is None


def test_identity_accessors() -> None:
    state = _loaded()
    assert state.model == "AC-MX44-AUHD"
    assert state.firmware == "V1.41"
    assert state.mac is None  # not reported yet


# ---------------------------------------------------------------------------------------------
# The census flag -- gating entity creation, and nothing else
# ---------------------------------------------------------------------------------------------


def test_the_census_flag_starts_false_and_latches_true() -> None:
    state = MatrixState()
    assert not state.census_done
    state = apply(state, ROUTES)
    assert not state.census_done
    state = apply(state, DeviceReport.census({"video_route_1": 1}))
    assert state.census_done


def test_a_later_partial_report_does_not_un_set_the_census_flag() -> None:
    state = apply(MatrixState(), DeviceReport.census({"video_route_1": 1}))
    assert apply(state, DeviceReport.update({"video_route_2": 2})).census_done


def test_an_empty_census_still_completes_the_census() -> None:
    """A device that reports nothing has still been read; entity creation must not hang."""
    assert apply(MatrixState(), DeviceReport(values={}, complete=True)).census_done


# ---------------------------------------------------------------------------------------------
# What has been seen -- drives entity creation
# ---------------------------------------------------------------------------------------------


def test_seen_accumulates_kinds_not_keys() -> None:
    state = _loaded()
    assert "video_route" in state.seen
    assert "output_name" in state.seen
    assert "video_route_1" not in state.seen


def test_has_reports_whether_a_kind_was_ever_seen() -> None:
    state = _loaded()
    assert state.has("video_route")
    assert not state.has("stream")


# ---------------------------------------------------------------------------------------------
# Site data
# ---------------------------------------------------------------------------------------------


def test_only_the_mac_is_kept_from_the_network_body() -> None:
    """The address, netmask and gateway are site data with no use here."""
    state = apply(MatrixState(), DeviceReport.update({"mac": "aa:bb:cc:dd:ee:ff"}))
    stored = repr(state)
    assert "aa:bb:cc:dd:ee:ff" in stored
    for absent in ("10.0.0.1", "255.255.255.0", "STATICIP"):
        assert absent not in stored
