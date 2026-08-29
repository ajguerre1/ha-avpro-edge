"""The telnet line grammar (T-N1..T-N6) and DeviceReport (T-R1..T-R3).

The census fixture below is the shape of a real ``GET STA`` from firmware 1.72, with the address,
MAC and network values replaced. The line *forms* are the device's; the values are not.
"""

from __future__ import annotations

import pytest
from avpro import telnet_protocol as tp
from avpro.models import AudioDelay, BindMode, ImageEnhancement, ScalerMode
from avpro.report import EMPTY, DeviceReport

#: The shape of a real GET STA reply. Invented values.
CENSUS = """
ADDR 00
LCD ON T2
KEY LOCK OFF
OUT1 VS IN1
OUT2 VS IN2
OUT3 VS IN3
OUT4 VS IN4
OUT1 VIDEO 1
OUT2 VIDEO 0
OUT3 VIDEO 2
OUT4 VIDEO 4
OUT1 EXADL PH0
OUT2 EXADL PH1
OUT3 EXADL PH7
OUT4 EXADL PH0
OUT1 EXA DIS
OUT2 EXA EN
OUT3 EXA DIS
OUT4 EXA EN
EXAMX MODE2
OUT1 AS IN2
OUT2 AS IN2
OUT3 AS IN4
OUT4 AS IN4
OUT1 IMAGE ENH 0
OUT2 IMAGE ENH 3
OUT3 IMAGE ENH 0
OUT4 IMAGE ENH 0
OUT1 STREAM ON
OUT2 STREAM OFF
OUT3 STREAM ON
OUT4 STREAM ON
OUT1 SGM DIS
OUT2 SGM EN
OUT3 SGM DIS
OUT4 SGM DIS
IN1 TMDS ON
IN2 TMDS OFF
IN3 TMDS ON
IN4 TMDS ON
IN1 EDID 30
IN2 EDID 0
IN3 EDID 14
IN4 EDID 32
RIP 010.000.000.001
HIP 010.000.000.050
NMK 255.255.252.000
TIP 23
DHCP 0
MAC aa.bb.cc.dd.ee.ff
"""


# ---------------------------------------------------------------------------------------------
# T-N1 -- every line form in a real dump
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("OUT1 VS IN3", ("video_route_1", 3)),
        ("OUT4 AS IN2", ("audio_route_4", 2)),
        ("OUT2 EXA EN", ("extracted_audio_2", True)),
        ("OUT2 EXA DIS", ("extracted_audio_2", False)),
        ("OUT3 EXADL PH7", ("audio_delay_3", AudioDelay.MS_630)),
        ("OUT3 EXADL PH0", ("audio_delay_3", AudioDelay.BYPASS)),
        ("EXAMX MODE2", ("bind_mode", BindMode.MATRIX)),
        ("OUT1 VIDEO 2", ("scaler_1", ScalerMode.DOWNSCALE_4K_TO_2K)),
        ("OUT1 IMAGE ENH 3", ("image_enhancement_1", ImageEnhancement.STRONG)),
        ("OUT2 SGM EN", ("test_pattern_2", True)),
        ("OUT1 STREAM ON", ("stream_1", True)),
        ("OUT2 STREAM OFF", ("stream_2", False)),
        ("IN1 TMDS ON", ("input_power_1", True)),
        ("KEY LOCK OFF", ("key_lock", False)),
        ("LCD ON T2", ("lcd_timeout", 2)),
        ("ADDR 00", ("address", "00")),
    ],
)
def test_every_line_form_parses(line: str, expected: tuple[str, object]) -> None:
    assert tp.parse_line(line) == expected


#: What one ``GET STA`` supplies, enumerated rather than multiplied -- a count is easy to get
#: wrong and says nothing about *which* key went missing.
PER_OUTPUT_KEYS = (
    "video_route",
    "audio_route",
    "extracted_audio",
    "audio_delay",
    "scaler",
    "image_enhancement",
    "test_pattern",
    "stream",
)
PER_INPUT_KEYS = ("input_power", "edid")
DEVICE_KEYS = ("address", "lcd_timeout", "key_lock", "bind_mode", "mac")


def test_the_whole_census_parses() -> None:
    report = tp.parse_lines(CENSUS, complete=True)
    assert report.complete

    expected = {
        *(f"{k}_{n}" for k in PER_OUTPUT_KEYS for n in range(1, 5)),
        *(f"{k}_{n}" for k in PER_INPUT_KEYS for n in range(1, 5)),
        *DEVICE_KEYS,
    }
    assert set(report.values) == expected


def test_one_command_reads_the_entire_device() -> None:
    """R18, and the reason setup is a single round trip instead of six."""
    report = tp.parse_lines(CENSUS, complete=True)
    assert len(report.values) == 45


def test_the_census_is_recognisable_as_one() -> None:
    """The device frames nothing, so a full reply is identified by what only it contains."""
    assert tp.looks_like_census(tp.parse_lines(CENSUS, complete=True))
    assert not tp.looks_like_census(tp.parse_lines("OUT1 VS IN2"))


# ---------------------------------------------------------------------------------------------
# T-N2 -- unrecognised lines
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "RIP 010.000.000.001",  # modelled deliberately: network config is out of scope
        "NMK 255.255.252.000",
        "DHCP 0",
        "TIP 23",
        "SOMETHING NEW 5",  # a future firmware
        "OUT1 VS",  # truncated
        "OUT1 VS INx",  # garbled
    ],
)
def test_a_line_we_do_not_model_yields_nothing(line: str) -> None:
    assert tp.parse_line(line) is None


def test_an_unknown_enum_code_is_dropped_not_recorded_as_null() -> None:
    """A firmware with a new scaler mode must read as "not reported", never as "off"."""
    assert tp.parse_line("OUT1 VIDEO 9") is None
    assert tp.parse_line("OUT1 IMAGE ENH 9") is None
    assert tp.parse_line("OUT1 EXADL PH9") is None
    assert tp.parse_line("EXAMX MODE9") is None


def test_network_lines_are_ignored_so_site_data_never_enters_the_report() -> None:
    report = tp.parse_lines(CENSUS, complete=True)
    stored = repr(report)
    for leaked in ("010.000.000.001", "255.255.252.000", "010.000.000.050"):
        assert leaked not in stored


# ---------------------------------------------------------------------------------------------
# T-N3 -- the two EDID vocabularies must agree
# ---------------------------------------------------------------------------------------------


def test_telnet_edid_index_30_is_the_same_as_http_edidu1() -> None:
    """Telnet says 30, HTTP says EDIDU1, the manual says USER1_EDID. All the same EDID."""
    from avpro.models import EDID_OPTION_BY_TOKEN

    assert tp.parse_line("IN1 EDID 30") == ("edid_1", "user_1")
    assert EDID_OPTION_BY_TOKEN["EDIDU1"] == "user_1"


def test_the_preset_off_by_one_is_the_devices_not_ours() -> None:
    """Telnet numbers presets from 0; HTTP's EDIDD tokens start at 1."""
    from avpro.models import EDID_OPTION_BY_TOKEN

    assert tp.parse_line("IN1 EDID 0") == ("edid_1", "preset_1")
    assert EDID_OPTION_BY_TOKEN["EDIDD1"] == "preset_1"
    assert tp.parse_line("IN1 EDID 29") == ("edid_1", "preset_30")
    assert EDID_OPTION_BY_TOKEN["EDIDD30"] == "preset_30"


def test_all_three_user_buffers_line_up() -> None:
    for index, number in ((30, 1), (31, 2), (32, 3)):
        assert tp.parse_line(f"IN1 EDID {index}") == ("edid_1", f"user_{number}")


def test_edid_index_round_trips() -> None:
    for index in range(33):
        option = tp._edid_option(index)
        assert option is not None
        assert tp.edid_index(option) == index


def test_copy_from_output_has_no_index_because_it_is_a_different_command() -> None:
    assert tp.edid_index("copy_output_1") is None


def test_an_out_of_range_edid_index_yields_nothing() -> None:
    assert tp.parse_line("IN1 EDID 33") is None


# ---------------------------------------------------------------------------------------------
# T-N4 -- EN/DIS and ON/OFF must mean the same as HTTP's booleans
# ---------------------------------------------------------------------------------------------


def test_en_dis_and_on_off_agree_with_the_http_transport() -> None:
    from avpro import protocol as http
    from avpro.models import decode_on_off

    assert tp.parse_line("OUT2 EXA EN")[1] is decode_on_off("ON")
    assert tp.parse_line("OUT2 SGM DIS")[1] is decode_on_off("OFF")
    # The same fact over HTTP.
    assert http.parse_extracted_audio("O2AON") == (2, True)


# ---------------------------------------------------------------------------------------------
# T-N5 / T-R -- complete versus partial
# ---------------------------------------------------------------------------------------------


def test_a_push_is_partial_and_names_only_what_it_carries() -> None:
    report = tp.parse_lines("OUT1 VS IN2")
    assert not report.complete
    assert set(report.values) == {"video_route_1"}


def test_a_block_of_nothing_is_not_a_census_even_if_asked_for() -> None:
    """Claiming completeness for an empty read would blank the device."""
    assert not tp.parse_lines("garbage\nmore garbage", complete=True).complete


def test_a_partial_report_merges_without_clearing_what_it_omits() -> None:
    """T-R1. A push naming one output must not blank the rest of the device."""
    census = tp.parse_lines(CENSUS, complete=True)
    push = tp.parse_lines("OUT1 VS IN4")
    merged = census.merge(push)
    assert merged.get("video_route_1") == 4
    assert merged.get("video_route_2") == 2  # untouched
    assert merged.complete


def test_merging_two_partials_does_not_add_up_to_a_census() -> None:
    a = tp.parse_lines("OUT1 VS IN1")
    b = tp.parse_lines("OUT2 VS IN2")
    assert not a.merge(b).complete


def test_merge_lets_the_later_report_win() -> None:
    a = tp.parse_lines("OUT1 VS IN1")
    b = tp.parse_lines("OUT1 VS IN3")
    assert a.merge(b).get("video_route_1") == 3


def test_merging_is_associative() -> None:
    """T-R3. How reports are grouped while folding cannot change the result.

    The transports assemble a report in whatever grouping is convenient -- HTTP merges one
    endpoint at a time, telnet folds a whole block of lines at once -- so if grouping mattered,
    the same three facts would land differently depending on which wire delivered them.
    """
    a = tp.parse_lines("OUT1 VS IN1")
    b = tp.parse_lines("OUT2 VS IN2")
    c = tp.parse_lines("OUT3 VS IN3")
    assert a.merge(b).merge(c).values == a.merge(b.merge(c)).values


def test_the_arrival_order_of_two_independent_pushes_does_not_matter() -> None:
    """T-R3. Two pushes about different outputs commute.

    This is the property the testing doc was reaching for with "the order two pushes arrive in
    cannot change the result". It holds for disjoint keys, which is the case that actually
    happens: the device announces one output at a time.
    """
    a = tp.parse_lines("OUT1 VS IN1")
    b = tp.parse_lines("OUT2 VS IN4")
    assert a.merge(b).values == b.merge(a).values


def test_two_reports_about_the_same_output_are_deliberately_order_dependent() -> None:
    """T-R3, the boundary. Overlapping keys do **not** commute, and must not.

    "Later wins" is what lets a fresh reading supersede a stale one. A merge that tried to be
    commutative here would have to choose a winner by something other than recency, and there is
    nothing else to choose by.
    """
    a = tp.parse_lines("OUT1 VS IN1")
    b = tp.parse_lines("OUT1 VS IN3")
    assert a.merge(b).values != b.merge(a).values
    assert a.merge(b).get("video_route_1") == 3


def test_the_empty_report_is_falsy_and_safe_to_merge() -> None:
    assert not EMPTY
    census = tp.parse_lines(CENSUS, complete=True)
    assert census.merge(EMPTY) == census


def test_reports_are_value_comparable() -> None:
    """The state fold relies on this to decide whether anything moved."""
    assert tp.parse_lines(CENSUS, complete=True) == tp.parse_lines(CENSUS, complete=True)


def test_census_and_update_constructors_say_what_they_mean() -> None:
    assert DeviceReport.census({"a": 1}).complete
    assert not DeviceReport.update({"a": 1}).complete


# ---------------------------------------------------------------------------------------------
# T-N6 -- a garbled line cannot corrupt its neighbours
# ---------------------------------------------------------------------------------------------


def test_a_garbled_line_in_the_middle_does_not_lose_the_rest() -> None:
    report = tp.parse_lines("OUT1 VS IN2\n@@@ nonsense @@@\nOUT3 VS IN4")
    assert report.get("video_route_1") == 2
    assert report.get("video_route_3") == 4


def test_both_line_endings_are_accepted() -> None:
    assert tp.parse_lines("OUT1 VS IN2\r\nOUT2 VS IN3").values == {
        "video_route_1": 2,
        "video_route_2": 3,
    }


def test_leading_and_trailing_whitespace_is_tolerated() -> None:
    assert tp.parse_line("  OUT1 VS IN2  ") == ("video_route_1", 2)


# ---------------------------------------------------------------------------------------------
# MAC normalisation -- so an entry created over either transport matches
# ---------------------------------------------------------------------------------------------


def test_the_dotted_mac_normalises_to_the_http_format() -> None:
    assert tp.parse_line("MAC aa.bb.cc.dd.ee.ff") == ("mac", "aa:bb:cc:dd:ee:ff")


def test_a_colon_mac_is_left_alone_apart_from_case() -> None:
    assert tp.parse_line("MAC AA:BB:CC:DD:EE:FF") == ("mac", "aa:bb:cc:dd:ee:ff")


# ---------------------------------------------------------------------------------------------
# The keys only this transport can supply
# ---------------------------------------------------------------------------------------------


def test_the_telnet_only_keys_are_exactly_what_http_cannot_read() -> None:
    """These five are the substantive reason telnet is primary rather than an alternative."""
    assert {
        "stream",
        "input_power",
        "key_lock",
        "lcd_timeout",
        "address",
    } == tp.TELNET_ONLY_KEYS


def test_the_census_supplies_every_telnet_only_key() -> None:
    report = tp.parse_lines(CENSUS, complete=True)
    present = {key.rsplit("_", 1)[0] if key[-1].isdigit() else key for key in report.values}
    assert present >= tp.TELNET_ONLY_KEYS


def test_state_keys_match_the_home_assistant_layer() -> None:
    """This package cannot import const.py -- it must stay free of Home Assistant -- so the two
    vocabularies are kept in step by assertion instead."""
    import json
    from pathlib import Path

    const = (
        Path(__file__).resolve().parents[1] / "custom_components" / "ha_avpro_edge" / "const.py"
    ).read_text(encoding="utf-8")
    for key in (
        tp.KEY_VIDEO_ROUTE,
        tp.KEY_AUDIO_ROUTE,
        tp.KEY_EXTRACTED_AUDIO,
        tp.KEY_AUDIO_DELAY,
        tp.KEY_SCALER,
        tp.KEY_IMAGE_ENHANCEMENT,
        tp.KEY_TEST_PATTERN,
        tp.KEY_EDID,
        tp.KEY_BIND_MODE,
    ):
        assert json.dumps(key) in const, f"{key} is not declared in const.py"
