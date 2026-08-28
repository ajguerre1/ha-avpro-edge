"""Enum codecs.

The rule under test throughout: an unrecognised code decodes to nothing, never to a wrong value.
"""

from __future__ import annotations

import pytest
from avpro import models as m
from avpro.models import AudioDelay, BindMode, ImageEnhancement, ScalerMode

# ---------------------------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("by_code", "to_code"),
    [
        (m.SCALER_MODE_BY_CODE, m.CODE_BY_SCALER_MODE),
        (m.IMAGE_ENHANCEMENT_BY_CODE, m.CODE_BY_IMAGE_ENHANCEMENT),
        (m.AUDIO_DELAY_BY_CODE, m.CODE_BY_AUDIO_DELAY),
        (m.BIND_MODE_BY_CODE, m.CODE_BY_BIND_MODE),
    ],
)
def test_every_code_round_trips(by_code: dict, to_code: dict) -> None:
    for code, value in by_code.items():
        assert to_code[value] == code


@pytest.mark.parametrize(
    ("enum_cls", "by_code"),
    [
        (ScalerMode, m.SCALER_MODE_BY_CODE),
        (ImageEnhancement, m.IMAGE_ENHANCEMENT_BY_CODE),
        (AudioDelay, m.AUDIO_DELAY_BY_CODE),
        (BindMode, m.BIND_MODE_BY_CODE),
    ],
)
def test_every_member_is_reachable_from_a_code(enum_cls, by_code: dict) -> None:
    """A member with no code could never be read back from the device."""
    assert set(by_code.values()) == set(enum_cls)


@pytest.mark.parametrize(
    "by_code",
    [
        m.SCALER_MODE_BY_CODE,
        m.IMAGE_ENHANCEMENT_BY_CODE,
        m.AUDIO_DELAY_BY_CODE,
        m.BIND_MODE_BY_CODE,
    ],
)
def test_codes_are_contiguous_from_zero(by_code: dict) -> None:
    """The device numbers these from zero with no gaps; a gap means a transcription slip."""
    assert sorted(by_code) == list(range(len(by_code)))


# ---------------------------------------------------------------------------------------------
# Unknown codes
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "by_code",
    [
        m.SCALER_MODE_BY_CODE,
        m.IMAGE_ENHANCEMENT_BY_CODE,
        m.AUDIO_DELAY_BY_CODE,
        m.BIND_MODE_BY_CODE,
    ],
)
def test_an_unknown_code_yields_nothing(by_code: dict) -> None:
    assert by_code.get(99) is None
    assert by_code.get(-1) is None


# ---------------------------------------------------------------------------------------------
# The distinctions that are easy to lose
# ---------------------------------------------------------------------------------------------


def test_audio_delay_zero_is_bypass_not_zero_milliseconds() -> None:
    """Bypassing the delay line is not the same as delaying by zero.

    This is the whole reason audio delay is a select and not a number entity: a number would
    render code 0 as "0 ms" and silently lose the distinction.
    """
    assert m.AUDIO_DELAY_BY_CODE[0] is AudioDelay.BYPASS
    assert m.AUDIO_DELAY_MS[AudioDelay.BYPASS] is None


def test_audio_delay_steps_are_ninety_milliseconds() -> None:
    for code, member in m.AUDIO_DELAY_BY_CODE.items():
        if code:
            assert m.AUDIO_DELAY_MS[member] == 90 * code


def test_every_delay_member_has_a_millisecond_mapping() -> None:
    assert set(m.AUDIO_DELAY_MS) == set(AudioDelay)


def test_scaler_directions_are_not_transposed() -> None:
    """The web UI labels these "2K" and "4K", which read as each other's opposite.

    The vendor's telnet reference is explicit: code 2 is 4K->2K and code 3 is 2K->4K. Getting
    these backwards would downscale a display that asked to be upscaled.
    """
    assert m.SCALER_MODE_BY_CODE[2] is ScalerMode.DOWNSCALE_4K_TO_2K
    assert m.SCALER_MODE_BY_CODE[3] is ScalerMode.UPSCALE_2K_TO_4K


def test_bind_mode_matrix_is_code_two() -> None:
    assert m.BIND_MODE_BY_CODE[2] is BindMode.MATRIX


# ---------------------------------------------------------------------------------------------
# ON/OFF
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("token", "expected"), [("ON", True), ("OFF", False), ("on", True)])
def test_on_off_decodes(token: str, expected: bool) -> None:
    assert m.decode_on_off(token) is expected


@pytest.mark.parametrize("token", ["", "ENABLE", "1", "TRUE", "OF"])
def test_unknown_on_off_token_is_not_silently_false(token: str) -> None:
    assert m.decode_on_off(token) is None


def test_on_off_round_trips() -> None:
    for value in (True, False):
        assert m.decode_on_off(m.encode_on_off(value)) is value


# ---------------------------------------------------------------------------------------------
# Option keys are stable identifiers
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("enum_cls", [ScalerMode, ImageEnhancement, AudioDelay, BindMode])
def test_option_keys_are_lowercase_snake_case(enum_cls) -> None:
    """These strings become Home Assistant select options and translation keys.

    They are part of the integration's public surface: renaming one silently breaks every
    automation that referenced it.
    """
    for member in enum_cls:
        assert member.value == member.value.lower()
        assert member.value.replace("_", "").isalnum()
