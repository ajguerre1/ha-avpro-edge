"""Wire-protocol parsing and URL construction.

Every body in this file is either a shape captured from the real device with the values replaced,
or a deliberately broken variant. No real port name, address or MAC appears here.
"""

from __future__ import annotations

import pytest
from avpro import protocol as p
from avpro.protocol import ParseOutcome, StatusEndpoint

# ---------------------------------------------------------------------------------------------
# Happy paths -- one per endpoint, using the field *shapes* the real device produces
# ---------------------------------------------------------------------------------------------


def test_video_status_parses_four_routes() -> None:
    parsed = p.parse_status(StatusEndpoint.VIDEO, "VidSta=O1I1&O2I2&O3I3&O4I4")
    assert parsed.ok
    assert parsed.fields == ("O1I1", "O2I2", "O3I3", "O4I4")


def test_web_status_parses_model_firmware_and_eight_names() -> None:
    body = "WebSta=AC-MX44-AUHD&V1.41&OutA&OutB&OutC&OutD&SrcA&SrcB&SrcC&SrcD"
    parsed = p.expect_fields(p.parse_status(StatusEndpoint.WEB, body), 10)
    assert parsed.ok
    assert parsed.fields[0] == "AC-MX44-AUHD"
    assert parsed.fields[1] == "V1.41"
    assert parsed.fields[2:6] == ("OutA", "OutB", "OutC", "OutD")
    assert parsed.fields[6:10] == ("SrcA", "SrcB", "SrcC", "SrcD")


def test_audio_status_parses_all_thirteen_fields() -> None:
    body = "AudSta=O1D0&O2D0&O3D0&O4D0&O1AOFF&O2AON&O3AOFF&O4AON&AMB2&AO1I2&AO2I2&AO3I4&AO4I4"
    parsed = p.expect_fields(p.parse_status(StatusEndpoint.AUDIO, body), 13)
    assert parsed.ok
    assert parsed.fields[8] == "AMB2"


def test_system_status_parses_twelve_fields() -> None:
    body = "SysSta=O1E0&O2E0&O3E0&O4E0&O1V1&O2V1&O3V1&O4V1&O1SGMOFF&O2SGMOFF&O3SGMOFF&O4SGMOFF"
    parsed = p.expect_fields(p.parse_status(StatusEndpoint.SYSTEM, body), 12)
    assert parsed.ok


# ---------------------------------------------------------------------------------------------
# The trailing '&' -- INFDivSta.CGI really does end with one
# ---------------------------------------------------------------------------------------------


def test_info_status_drops_exactly_one_trailing_separator() -> None:
    body = "INFSta=3840X2160P@60HZ YUV420&1920X1080P@60HZ&&3840X2160P@60HZ YUV420&"
    parsed = p.parse_status(StatusEndpoint.INFO, body)
    assert parsed.ok
    # Four fields, and the genuinely empty third one survives -- an unconnected port reports
    # nothing, and collapsing it would shift every field after it.
    assert len(parsed.fields) == 4
    assert parsed.fields[2] == ""


def test_info_status_without_trailing_separator_also_parses() -> None:
    """Not every firmware need emit the trailing '&'; both shapes must give four fields."""
    body = "INFSta=a&b&c&d"
    parsed = p.parse_status(StatusEndpoint.INFO, body)
    assert parsed.ok
    assert parsed.fields == ("a", "b", "c", "d")


def test_only_one_trailing_separator_is_dropped() -> None:
    parsed = p.parse_status(StatusEndpoint.INFO, "INFSta=a&b&&")
    assert parsed.fields == ("a", "b", "")


# ---------------------------------------------------------------------------------------------
# The HTTP-200 trap -- the single most likely source of a silent bug
# ---------------------------------------------------------------------------------------------


def test_html_not_found_body_is_a_capability_signal_not_an_error() -> None:
    """The device serves this with status 200. It means "no such endpoint", not "failure"."""
    body = "<HTML>\n<BODY>\nSorry, the page you requested was not found.\n</BODY>\n</HTML>"
    parsed = p.parse_status(StatusEndpoint.TMDS, body)
    assert parsed.outcome is ParseOutcome.NOT_FOUND
    assert not parsed.ok


def test_bare_html_body_is_treated_as_not_found() -> None:
    parsed = p.parse_status(StatusEndpoint.TMDS, "<html><body>anything</body></html>")
    assert parsed.outcome is ParseOutcome.NOT_FOUND


def test_no_support_is_distinguished_from_not_found() -> None:
    parsed = p.parse_status(StatusEndpoint.SYSTEM, "NO SUPPORT")
    assert parsed.outcome is ParseOutcome.UNSUPPORTED


def test_empty_body_is_malformed() -> None:
    parsed = p.parse_status(StatusEndpoint.VIDEO, "")
    assert parsed.outcome is ParseOutcome.MALFORMED


def test_whitespace_only_body_is_malformed() -> None:
    parsed = p.parse_status(StatusEndpoint.VIDEO, "   \r\n  ")
    assert parsed.outcome is ParseOutcome.MALFORMED


def test_another_endpoints_key_is_malformed() -> None:
    """A 200 carrying the wrong endpoint's data must not be accepted as this endpoint's."""
    parsed = p.parse_status(StatusEndpoint.VIDEO, "AudSta=O1D0&O2D0")
    assert parsed.outcome is ParseOutcome.MALFORMED


def test_truncated_body_is_malformed() -> None:
    parsed = p.parse_status(StatusEndpoint.VIDEO, "VidSt")
    assert parsed.outcome is ParseOutcome.MALFORMED


def test_failure_detail_never_echoes_the_body() -> None:
    """Detail strings reach the log. On this device an unexpected body may still be site data."""
    secret = "SomeRoomName"
    parsed = p.parse_status(StatusEndpoint.VIDEO, f"Whatever={secret}")
    assert secret not in parsed.detail


# ---------------------------------------------------------------------------------------------
# The arity guard -- a port name containing '&'
# ---------------------------------------------------------------------------------------------


def test_ampersand_in_a_port_name_is_refused_not_misaligned() -> None:
    """A name like "Kitchen & Bar" splits in two and shifts every field after it.

    The parser cannot tell which field was split, so it must reject the whole response rather
    than assign eight names one position out.
    """
    body = "WebSta=AC-MX44-AUHD&V1.41&Bar & Grill&OutB&OutC&OutD&SrcA&SrcB&SrcC&SrcD"
    parsed = p.expect_fields(p.parse_status(StatusEndpoint.WEB, body), 10)
    assert parsed.outcome is ParseOutcome.MALFORMED
    assert "&" in parsed.detail  # explains the likely cause


def test_arity_guard_reports_counts_only_never_values() -> None:
    body = "WebSta=AC-MX44-AUHD&V1.41&Bar & Grill&B&C&D&E&F&G&H"
    parsed = p.expect_fields(p.parse_status(StatusEndpoint.WEB, body), 10)
    assert "Grill" not in parsed.detail
    assert "11" in parsed.detail and "10" in parsed.detail


def test_arity_guard_passes_a_correct_response_through_unchanged() -> None:
    parsed = p.parse_status(StatusEndpoint.VIDEO, "VidSta=O1I1&O2I2&O3I3&O4I4")
    assert p.expect_fields(parsed, 4) is parsed


def test_arity_guard_does_not_upgrade_a_failure() -> None:
    parsed = p.parse_status(StatusEndpoint.VIDEO, "")
    assert p.expect_fields(parsed, 0).outcome is ParseOutcome.MALFORMED


# ---------------------------------------------------------------------------------------------
# Every status endpoint has a key, and no two share one
# ---------------------------------------------------------------------------------------------


def test_every_status_endpoint_declares_a_key() -> None:
    assert set(p.STATUS_KEY) == set(StatusEndpoint)


def test_status_keys_are_unique() -> None:
    keys = list(p.STATUS_KEY.values())
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------------------------


def test_status_path_carries_a_cache_buster() -> None:
    assert p.status_path(StatusEndpoint.VIDEO, "0.42") == "/VIDDivSta.CGI?0.42"


def test_command_path_reproduces_the_web_uis_proven_form() -> None:
    """``?button=<code>+<random>`` is the only form observed to work on this hardware."""
    assert p.command_path(p.CommandEndpoint.VIDEO, "O1I3", "0.42") == (
        "/TimSendCmd.CGI?button=O1I3+0.42"
    )


def test_network_write_endpoints_are_not_reachable() -> None:
    """Nothing in this integration may reconfigure the matrix's IP, escape hatch included."""
    values = {e.value for e in p.CommandEndpoint}
    assert "NetSendCmd.CGI" not in values
    assert "NetDHCPSendCmd.CGI" not in values


def test_edid_endpoint_keeps_the_vendors_inconsistent_spelling() -> None:
    assert p.CommandEndpoint.EDID.value == "EdidsendCmd.CGI"


# ---------------------------------------------------------------------------------------------
# Command codes
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda: p.video_route(1, 3), "O1I3"),
        (lambda: p.video_route_all(2), "O5I2"),
        (lambda: p.audio_route(4, 1), "AO4I1"),
        (lambda: p.extracted_audio(2, True), "O2AON"),
        (lambda: p.extracted_audio(2, False), "O2AOFF"),
        (lambda: p.audio_delay(3, 7), "O3D7"),
        (lambda: p.bind_mode(2), "AMB2"),
        (lambda: p.scaler_mode(1, 4), "O1V4"),
        (lambda: p.image_enhancement(4, 0), "O4E0"),
        (lambda: p.test_pattern(1, True), "O1SGMON"),
        (lambda: p.test_pattern(1, False), "O1SGMOFF"),
        (lambda: p.tmds_stream(2, True), "T2AON"),
    ],
)
def test_command_codes(call, expected: str) -> None:
    assert call() == expected


def test_route_all_uses_the_documented_all_outputs_index() -> None:
    assert p.video_route_all(1) == f"O{p.ALL_OUTPUTS}I1"


# ---------------------------------------------------------------------------------------------
# Port names
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Kitchen", "Out 1", "Media-Room", "TV_2"])
def test_acceptable_port_names(name: str) -> None:
    assert p.validate_port_name(name) is None


@pytest.mark.parametrize(
    ("name", "because"),
    [
        ("", "empty"),
        ("Bar & Grill", "the field separator"),
        ("a+b", "query metacharacter"),
        ("100%", "query metacharacter"),
        ("a#b", "query metacharacter"),
        ("a?b", "query metacharacter"),
        ("a=b", "query metacharacter"),
        ("Café", "non-ASCII"),
        ("a\tb", "control character"),
        ("x" * 17, "too long"),
    ],
)
def test_rejected_port_names(name: str, because: str) -> None:
    assert p.validate_port_name(name) is not None, because


def test_set_names_emits_eight_trailing_delimited_fields() -> None:
    button = p.set_names(["A", "B", "C", "D"], ["E", "F", "G", "H"])
    assert button == "A&B&C&D&E&F&G&H&"


def test_set_names_round_trips_through_the_parser() -> None:
    """What a rename writes must be readable back as the same eight names."""
    outputs, inputs = ["OutA", "OutB", "OutC", "OutD"], ["SrcA", "SrcB", "SrcC", "SrcD"]
    body = f"WebSta=AC-MX44-AUHD&V1.41&{p.set_names(outputs, inputs)}"
    parsed = p.expect_fields(p.parse_status(StatusEndpoint.WEB, body), 10)
    assert parsed.ok
    assert list(parsed.fields[2:6]) == outputs
    assert list(parsed.fields[6:10]) == inputs
