"""CGI responses to transport-neutral reports.

These are the cases that used to live in ``test_state.py`` as tests of ``fold_*``. They are facts
about the HTTP interface rather than about the state model, which is why they moved here when the
seam landed.

The decisive test in this file is the last one: the same fact, read over HTTP and over telnet,
must produce byte-identical values. If that ever fails, the two transports have diverged and the
entities are at the mercy of which wire happened to be in use.
"""

from __future__ import annotations

from avpro import protocol as p
from avpro.http_decode import HTTP_READABLE, HTTP_WRITABLE, decode
from avpro.models import AudioDelay, BindMode, ImageEnhancement, ScalerMode
from avpro.protocol import StatusEndpoint

PORTS = 4

VIDEO = "VidSta=O1I1&O2I2&O3I3&O4I4"
WEB = "WebSta=AC-MX44-AUHD&V1.41&OutA&OutB&OutC&OutD&SrcA&SrcB&SrcC&SrcD"
AUDIO = "AudSta=O1D0&O2D1&O3D0&O4D7&O1AOFF&O2AON&O3AOFF&O4AON&AMB2&AO1I2&AO2I2&AO3I4&AO4I4"
SYSTEM = "SysSta=O1E0&O2E1&O3E2&O4E3&O1V1&O2V0&O3V2&O4V4&O1SGMOFF&O2SGMON&O3SGMOFF&O4SGMOFF"
INFO = "INFSta=3840X2160P@60HZ YUV420&1920X1080P@60HZ&&3840X2160P@60HZ YUV420&"
EDID = "EdidSta=EDIDU1&EDIDD1&EDIDD15&EDIDU3"
NETWORK = "NetSta=AA:BB:CC:DD:EE:FF&10.0.0.1&255.255.255.0&10.0.0.254&23&STATICIP&A&B&C&D&E&F&G&H"


def _decode(endpoint: StatusEndpoint, body: str, ports: int = PORTS):
    return decode(endpoint, p.parse_status(endpoint, body), port_count=ports)


# ---------------------------------------------------------------------------------------------
# Each endpoint
# ---------------------------------------------------------------------------------------------


def test_video_routing() -> None:
    assert _decode(StatusEndpoint.VIDEO, "VidSta=O1I3&O2I1&O3I4&O4I2").values == {
        "video_route_1": 3,
        "video_route_2": 1,
        "video_route_3": 4,
        "video_route_4": 2,
    }


def test_identity_and_port_names() -> None:
    values = _decode(StatusEndpoint.WEB, WEB).values
    assert values["model"] == "AC-MX44-AUHD"
    assert values["firmware"] == "V1.41"
    assert values["output_name_1"] == "OutA"
    assert values["input_name_4"] == "SrcD"


def test_audio_classifies_all_four_token_groups() -> None:
    values = _decode(StatusEndpoint.AUDIO, AUDIO).values
    assert values["audio_route_1"] == 2
    assert values["extracted_audio_2"] is True
    assert values["extracted_audio_1"] is False
    assert values["audio_delay_4"] is AudioDelay.MS_630
    assert values["audio_delay_1"] is AudioDelay.BYPASS
    assert values["bind_mode"] is BindMode.MATRIX


def test_audio_tokens_may_arrive_in_any_order() -> None:
    """Classification is by pattern, so a reordered body gives the same result."""
    fields = AUDIO.removeprefix("AudSta=").split("&")
    shuffled = "AudSta=" + "&".join(reversed(fields))
    assert _decode(StatusEndpoint.AUDIO, shuffled) == _decode(StatusEndpoint.AUDIO, AUDIO)


def test_an_unrecognised_audio_field_is_ignored_not_shifted() -> None:
    values = _decode(StatusEndpoint.AUDIO, AUDIO + "&SOMETHINGNEW9").values
    assert values["audio_route_1"] == 2


def test_system_classifies_all_three_token_groups() -> None:
    values = _decode(StatusEndpoint.SYSTEM, SYSTEM).values
    assert values["image_enhancement_4"] is ImageEnhancement.STRONG
    assert values["scaler_1"] is ScalerMode.BYPASS
    assert values["scaler_3"] is ScalerMode.DOWNSCALE_4K_TO_2K
    assert values["test_pattern_2"] is True


def test_signal_info_keeps_free_text_and_maps_blank_to_unknown() -> None:
    values = _decode(StatusEndpoint.INFO, INFO).values
    assert values["signal_1"] == "3840X2160P@60HZ YUV420"
    assert values["signal_3"] is None  # nothing connected
    assert len([k for k in values if k.startswith("signal_")]) == PORTS


def test_a_blank_field_is_indistinguishable_from_an_unread_one() -> None:
    """Pinned because it is a **conflation**, and one with a visible consequence.

    A blank field and a port the device never reported both become ``None``, so the state layer
    cannot tell "the matrix looked and there is nothing" from "nobody has asked yet". The two
    bodies below differ -- one reports four ports, the other two -- and decode to the same thing
    for ports 3 and 4.

    The consequence lands on ``binary_sensor.is_on``, which returns ``None`` for ``None`` and
    ``bool(text)`` otherwise. A non-empty string is always truthy, so **the entity can return
    True or None and never False**: a CONNECTIVITY binary sensor whose "Disconnected" state is
    unreachable, while its own docstring offers "is the Apple TV awake" as the automation it
    exists for.

    Not changed here, deliberately. Preserving the distinction is a one-line change with no blast
    radius -- media_player already tests truthiness, and the sensor maps blank to None itself --
    but whether a real AC-MX44-AUHD returns blank for an unplugged input or for a port it does
    not measure is **not established**, and "Disconnected" would be a false claim under the second
    reading. It is a hardware observation, not a code decision, and it is on the live checklist.
    Inverting it on a guess is exactly how the fake came to serve a TMDS tab V1.41 does not have.
    """
    four_ports_one_blank = _decode(StatusEndpoint.INFO, "INFSta=a&b&&d&").values
    only_two_reported = _decode(StatusEndpoint.INFO, "INFSta=a&b").values

    assert four_ports_one_blank["signal_3"] is None, "the device said this port has nothing"
    assert only_two_reported["signal_3"] is None, "the device said nothing about this port"


def test_a_short_signal_body_is_padded_not_truncated() -> None:
    values = _decode(StatusEndpoint.INFO, "INFSta=a&b").values
    assert values == {"signal_1": "a", "signal_2": "b", "signal_3": None, "signal_4": None}


def test_edid_tokens_decode_to_option_keys() -> None:
    values = _decode(StatusEndpoint.EDID, EDID).values
    assert values == {
        "edid_1": "user_1",
        "edid_2": "preset_1",
        "edid_3": "preset_15",
        "edid_4": "user_3",
    }


def test_network_keeps_the_mac_and_discards_everything_else() -> None:
    report = _decode(StatusEndpoint.NETWORK, NETWORK)
    assert report.values == {"mac": "aa:bb:cc:dd:ee:ff"}
    stored = repr(report)
    for leaked in ("10.0.0.1", "255.255.255.0", "10.0.0.254", "STATICIP"):
        assert leaked not in stored


# ---------------------------------------------------------------------------------------------
# Unusable bodies produce nothing rather than failing
# ---------------------------------------------------------------------------------------------


def test_an_unusable_body_decodes_to_an_empty_report() -> None:
    """One odd endpoint must never blank a value or fail an update."""
    for body in ("", "NO SUPPORT", "<HTML>Sorry, the page you requested was not found.</HTML>"):
        assert not _decode(StatusEndpoint.SYSTEM, body).values


def test_an_ampersand_in_a_port_name_yields_nothing_rather_than_shifted_names() -> None:
    """There is no way to tell which field was split, so the whole response is refused."""
    bad = "WebSta=AC-MX44-AUHD&V1.41&Bar & Grill&OutB&OutC&OutD&SrcA&SrcB&SrcC&SrcD"
    assert not _decode(StatusEndpoint.WEB, bad).values


def test_an_unknown_enum_code_is_dropped_not_approximated() -> None:
    assert "scaler_1" not in _decode(StatusEndpoint.SYSTEM, "SysSta=O1V9").values
    assert "audio_delay_1" not in _decode(StatusEndpoint.AUDIO, "AudSta=O1D9").values


def test_every_decoded_report_is_partial() -> None:
    """A single endpoint never describes the whole device, so none of these is a census."""
    for endpoint, body in (
        (StatusEndpoint.VIDEO, VIDEO),
        (StatusEndpoint.WEB, WEB),
        (StatusEndpoint.AUDIO, AUDIO),
    ):
        assert not _decode(endpoint, body).complete


# ---------------------------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------------------------


def test_http_cannot_read_the_five_telnet_only_kinds() -> None:
    """This is the substantive reason telnet is the primary transport."""
    from avpro.telnet_protocol import TELNET_ONLY_KEYS

    assert not (HTTP_READABLE & TELNET_ONLY_KEYS)


def test_observations_are_readable_but_not_writable() -> None:
    for kind in ("model", "firmware", "mac", "signal"):
        assert kind in HTTP_READABLE
        assert kind not in HTTP_WRITABLE


# ---------------------------------------------------------------------------------------------
# The two transports must agree -- the test that protects the whole seam
# ---------------------------------------------------------------------------------------------


def test_the_same_fact_decodes_identically_over_both_wires() -> None:
    """If this fails, the transports have diverged and entities depend on which wire is in use."""
    from avpro import telnet_protocol as tp

    cases = [
        # (telnet line, HTTP endpoint, HTTP body)
        ("OUT1 VS IN3", StatusEndpoint.VIDEO, "VidSta=O1I3&O2I2&O3I3&O4I4"),
        ("OUT2 EXA EN", StatusEndpoint.AUDIO, "AudSta=O2AON"),
        ("OUT1 EXADL PH7", StatusEndpoint.AUDIO, "AudSta=O1D7"),
        ("EXAMX MODE2", StatusEndpoint.AUDIO, "AudSta=AMB2"),
        ("OUT1 VIDEO 2", StatusEndpoint.SYSTEM, "SysSta=O1V2"),
        ("OUT1 IMAGE ENH 3", StatusEndpoint.SYSTEM, "SysSta=O1E3"),
        ("OUT2 SGM EN", StatusEndpoint.SYSTEM, "SysSta=O2SGMON"),
        ("IN1 EDID 30", StatusEndpoint.EDID, "EdidSta=EDIDU1"),
    ]

    for line, endpoint, body in cases:
        key, telnet_value = tp.parse_line(line)
        http_values = _decode(endpoint, body, ports=1 if endpoint is StatusEndpoint.EDID else 4)
        assert key in http_values.values, f"{line!r} has no HTTP counterpart in {body!r}"
        assert http_values.values[key] == telnet_value, (
            f"{line!r} and {body!r} disagree about {key}: "
            f"telnet says {telnet_value!r}, HTTP says {http_values.values[key]!r}"
        )
