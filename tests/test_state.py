"""The immutable state fold.

Every body here uses invented names. The shapes are the device's; the values are not.
"""

from __future__ import annotations

from avpro import protocol as p
from avpro import state as s
from avpro.models import AudioDelay, BindMode, ImageEnhancement, ScalerMode
from avpro.protocol import StatusEndpoint
from avpro.state import MatrixState

PORTS = 4

VIDEO_BODY = "VidSta=O1I1&O2I2&O3I3&O4I4"
WEB_BODY = "WebSta=AC-MX44-AUHD&V1.41&OutA&OutB&OutC&OutD&SrcA&SrcB&SrcC&SrcD"
AUDIO_BODY = "AudSta=O1D0&O2D1&O3D0&O4D7&O1AOFF&O2AON&O3AOFF&O4AON&AMB2&AO1I2&AO2I2&AO3I4&AO4I4"
SYSTEM_BODY = "SysSta=O1E0&O2E1&O3E2&O4E3&O1V1&O2V0&O3V2&O4V4&O1SGMOFF&O2SGMON&O3SGMOFF&O4SGMOFF"
INFO_BODY = "INFSta=3840X2160P@60HZ YUV420&1920X1080P@60HZ&&3840X2160P@60HZ YUV420&"


def _parse(endpoint: StatusEndpoint, body: str) -> p.ParsedStatus:
    return p.parse_status(endpoint, body)


def _full() -> MatrixState:
    st = MatrixState()
    st = s.fold_video(st, _parse(StatusEndpoint.VIDEO, VIDEO_BODY))
    st = s.fold_web(st, _parse(StatusEndpoint.WEB, WEB_BODY), port_count=PORTS)
    st = s.fold_audio(st, _parse(StatusEndpoint.AUDIO, AUDIO_BODY), port_count=PORTS)
    st = s.fold_system(st, _parse(StatusEndpoint.SYSTEM, SYSTEM_BODY), port_count=PORTS)
    st = s.fold_info(st, _parse(StatusEndpoint.INFO, INFO_BODY), port_count=PORTS)
    return st


# ---------------------------------------------------------------------------------------------
# Value semantics -- what makes always_update=False safe
# ---------------------------------------------------------------------------------------------


def test_identical_bodies_fold_to_equal_states() -> None:
    """The property the whole panel-fan-out defence rests on."""
    assert _full() == _full()


def test_a_changed_route_makes_the_state_unequal() -> None:
    before = _full()
    after = s.fold_video(before, _parse(StatusEndpoint.VIDEO, "VidSta=O1I3&O2I2&O3I3&O4I4"))
    assert after != before


def test_refolding_the_same_body_changes_nothing() -> None:
    once = _full()
    twice = s.fold_video(once, _parse(StatusEndpoint.VIDEO, VIDEO_BODY))
    assert twice == once


def test_state_is_frozen() -> None:
    st = MatrixState()
    try:
        st.model = "nope"  # type: ignore[misc]
    except (AttributeError, TypeError):
        return
    raise AssertionError("MatrixState must be immutable")


# ---------------------------------------------------------------------------------------------
# Each fold owns only its own fields
# ---------------------------------------------------------------------------------------------


def test_a_partial_fold_leaves_other_fields_untouched() -> None:
    st = _full()
    after = s.fold_video(st, _parse(StatusEndpoint.VIDEO, "VidSta=O1I4&O2I2&O3I3&O4I4"))
    assert after.video_routes[0] == 4
    assert after.output_names == st.output_names
    assert after.bind_mode == st.bind_mode
    assert after.scaler_modes == st.scaler_modes


def test_a_failed_body_keeps_the_previous_values() -> None:
    """A cold endpoint that fails must not blank the entities built from it."""
    st = _full()
    for parsed in (
        _parse(StatusEndpoint.SYSTEM, ""),
        _parse(StatusEndpoint.SYSTEM, "<HTML>Sorry, the page you requested was not found.</HTML>"),
        _parse(StatusEndpoint.SYSTEM, "NO SUPPORT"),
    ):
        assert s.fold_system(st, parsed, port_count=PORTS) == st


def test_an_absent_endpoint_never_marks_itself_seen() -> None:
    st = s.fold_system(
        MatrixState(),
        _parse(StatusEndpoint.SYSTEM, "<HTML>Sorry, the page you requested was not found.</HTML>"),
        port_count=PORTS,
    )
    assert StatusEndpoint.SYSTEM not in st.seen


def test_seen_accumulates_across_folds() -> None:
    st = _full()
    assert {
        StatusEndpoint.VIDEO,
        StatusEndpoint.WEB,
        StatusEndpoint.AUDIO,
        StatusEndpoint.SYSTEM,
        StatusEndpoint.INFO,
    } <= st.seen
    assert StatusEndpoint.TMDS not in st.seen


# ---------------------------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------------------------


def test_video_routes_are_indexed_by_output() -> None:
    st = s.fold_video(MatrixState(), _parse(StatusEndpoint.VIDEO, "VidSta=O1I3&O2I1&O3I4&O4I2"))
    assert st.video_routes == (3, 1, 4, 2)


def test_port_count_is_derived_from_the_device_not_assumed() -> None:
    """An eight-output unit must size itself from what it reports."""
    body = "VidSta=" + "&".join(f"O{n}I1" for n in range(1, 9))
    st = s.fold_video(MatrixState(), _parse(StatusEndpoint.VIDEO, body))
    assert st.port_count == 8
    assert len(st.video_routes) == 8


def test_port_count_falls_back_before_anything_is_known() -> None:
    assert MatrixState().port_count == s.DEFAULT_PORT_COUNT


def test_video_body_with_no_recognisable_route_is_ignored() -> None:
    st = _full()
    assert s.fold_video(st, _parse(StatusEndpoint.VIDEO, "VidSta=junk&more")) == st


# ---------------------------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------------------------


def test_names_split_into_outputs_then_inputs() -> None:
    st = s.fold_web(MatrixState(), _parse(StatusEndpoint.WEB, WEB_BODY), port_count=PORTS)
    assert st.output_names == ("OutA", "OutB", "OutC", "OutD")
    assert st.input_names == ("SrcA", "SrcB", "SrcC", "SrcD")
    assert st.model == "AC-MX44-AUHD"
    assert st.firmware == "V1.41"


def test_name_lookups_are_one_based_and_bounds_safe() -> None:
    st = s.fold_web(MatrixState(), _parse(StatusEndpoint.WEB, WEB_BODY), port_count=PORTS)
    assert st.output_name(1) == "OutA"
    assert st.input_name(4) == "SrcD"
    assert st.output_name(0) is None
    assert st.output_name(99) is None


def test_an_ampersand_in_a_name_leaves_every_name_unchanged() -> None:
    """The shifted response must be rejected, not applied one position out."""
    st = s.fold_web(MatrixState(), _parse(StatusEndpoint.WEB, WEB_BODY), port_count=PORTS)
    bad = "WebSta=AC-MX44-AUHD&V1.41&Bar & Grill&OutB&OutC&OutD&SrcA&SrcB&SrcC&SrcD"
    after = s.fold_web(st, _parse(StatusEndpoint.WEB, bad), port_count=PORTS)
    assert after == st


# ---------------------------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------------------------


def test_audio_fold_classifies_all_four_token_groups() -> None:
    st = s.fold_audio(MatrixState(), _parse(StatusEndpoint.AUDIO, AUDIO_BODY), port_count=PORTS)
    assert st.audio_routes == (2, 2, 4, 4)
    assert st.extracted_audio == (False, True, False, True)
    assert st.audio_delays == (
        AudioDelay.BYPASS,
        AudioDelay.MS_90,
        AudioDelay.BYPASS,
        AudioDelay.MS_630,
    )
    assert st.bind_mode is BindMode.MATRIX


def test_audio_tokens_may_arrive_in_any_order() -> None:
    """Classification is by pattern, so a reordered body must give the same result."""
    ordered = s.fold_audio(
        MatrixState(), _parse(StatusEndpoint.AUDIO, AUDIO_BODY), port_count=PORTS
    )
    fields = AUDIO_BODY.removeprefix("AudSta=").split("&")
    shuffled = "AudSta=" + "&".join(reversed(fields))
    assert s.fold_audio(
        MatrixState(), _parse(StatusEndpoint.AUDIO, shuffled), port_count=PORTS
    ) == (ordered)


def test_an_unrecognised_audio_field_is_ignored_not_shifted() -> None:
    body = AUDIO_BODY + "&SOMETHINGNEW9"
    st = s.fold_audio(MatrixState(), _parse(StatusEndpoint.AUDIO, body), port_count=PORTS)
    assert st.audio_routes == (2, 2, 4, 4)


def test_an_unknown_delay_code_is_dropped_not_approximated() -> None:
    body = "AudSta=O1D9&AMB2"
    st = s.fold_audio(MatrixState(), _parse(StatusEndpoint.AUDIO, body), port_count=PORTS)
    assert st.audio_delays[0] is None


def test_bind_mode_survives_a_body_that_omits_it() -> None:
    st = s.fold_audio(MatrixState(), _parse(StatusEndpoint.AUDIO, AUDIO_BODY), port_count=PORTS)
    after = s.fold_audio(st, _parse(StatusEndpoint.AUDIO, "AudSta=AO1I1"), port_count=PORTS)
    assert after.bind_mode is BindMode.MATRIX


# ---------------------------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------------------------


def test_system_fold_classifies_all_three_token_groups() -> None:
    st = s.fold_system(MatrixState(), _parse(StatusEndpoint.SYSTEM, SYSTEM_BODY), port_count=PORTS)
    assert st.image_enhancements == (
        ImageEnhancement.OFF,
        ImageEnhancement.WEAK,
        ImageEnhancement.MEDIUM,
        ImageEnhancement.STRONG,
    )
    assert st.scaler_modes == (
        ScalerMode.BYPASS,
        ScalerMode.AUTO,
        ScalerMode.DOWNSCALE_4K_TO_2K,
        ScalerMode.HDBT_C_MODE,
    )
    assert st.test_patterns == (False, True, False, False)


def test_an_unknown_scaler_code_is_dropped_not_approximated() -> None:
    st = s.fold_system(
        MatrixState(), _parse(StatusEndpoint.SYSTEM, "SysSta=O1V9"), port_count=PORTS
    )
    assert st.scaler_modes[0] is None


# ---------------------------------------------------------------------------------------------
# Signal info
# ---------------------------------------------------------------------------------------------


def test_signal_info_keeps_free_text_and_maps_blank_to_unknown() -> None:
    st = s.fold_info(MatrixState(), _parse(StatusEndpoint.INFO, INFO_BODY), port_count=PORTS)
    assert st.signals[0] == "3840X2160P@60HZ YUV420"
    assert st.signals[1] == "1920X1080P@60HZ"
    assert st.signals[2] is None  # nothing connected
    assert len(st.signals) == PORTS


def test_a_short_signal_body_is_padded_not_truncated() -> None:
    st = s.fold_info(MatrixState(), _parse(StatusEndpoint.INFO, "INFSta=a&b"), port_count=PORTS)
    assert st.signals == ("a", "b", None, None)


# ---------------------------------------------------------------------------------------------
# Network -- the fold that must throw site data away
# ---------------------------------------------------------------------------------------------


def test_network_fold_keeps_the_mac_and_discards_everything_else() -> None:
    body = "NetSta=AA:BB:CC:DD:EE:FF&10.0.0.1&255.255.255.0&10.0.0.254&23&STATICIP&A&B&C&D&E&F&G&H"
    st = s.fold_network(MatrixState(), _parse(StatusEndpoint.NETWORK, body))
    assert st.mac == "AA:BB:CC:DD:EE:FF"
    # The address, gateway and the second copy of the port names must not be retained anywhere.
    stored = repr(st)
    for leaked in ("10.0.0.1", "255.255.255.0", "10.0.0.254", "STATICIP"):
        assert leaked not in stored


def test_network_fold_tolerates_a_missing_mac() -> None:
    st = s.fold_network(MatrixState(), _parse(StatusEndpoint.NETWORK, "NetSta=&10.0.0.1"))
    assert st.mac is None
