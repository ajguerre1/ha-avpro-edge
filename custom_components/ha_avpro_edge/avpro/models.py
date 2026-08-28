"""Value semantics for the AVPro Edge AUHD protocol.

Pure. No I/O, no Home Assistant imports, no clock.

This module owns the mapping between the numeric codes the matrix speaks and the named values
the rest of the integration uses. Two rules govern every codec here:

1. **An unrecognised code decodes to ``None``, never to a wrong value.** Reporting "Auto" for a
   scaler mode this firmware invented is worse than reporting nothing: it is indistinguishable
   from the truth and it silently misleads automations.
2. **The names are the vendor's, not a reinterpretation.** ``V2``/``V3`` are "4K -> 2K" and
   "2K -> 4K", which is what the telnet reference calls them. The web UI's shorter labels ("2K",
   "4K") describe the *output* resolution and read as the opposite of each other, so they are not
   used.

The code-to-meaning table was decoded from the unit's own web UI and then independently confirmed
against the AUHD telnet command reference. Both sources agree, which is why these values can be
trusted without a live probe.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# ---------------------------------------------------------------------------------------------
# Scaler mode -- wire codes O{n}V{0-4}
# ---------------------------------------------------------------------------------------------


class ScalerMode(StrEnum):
    """Per-output video scaler mode.

    Values are the Home Assistant option keys; the wire code is the index in
    ``SCALER_MODE_BY_CODE``.
    """

    AUTO = "auto"
    BYPASS = "bypass"
    DOWNSCALE_4K_TO_2K = "downscale_4k_to_2k"
    UPSCALE_2K_TO_4K = "upscale_2k_to_4k"
    HDBT_C_MODE = "hdbt_c_mode"


SCALER_MODE_BY_CODE: Final[dict[int, ScalerMode]] = {
    0: ScalerMode.AUTO,
    1: ScalerMode.BYPASS,
    2: ScalerMode.DOWNSCALE_4K_TO_2K,
    3: ScalerMode.UPSCALE_2K_TO_4K,
    4: ScalerMode.HDBT_C_MODE,
}
CODE_BY_SCALER_MODE: Final[dict[ScalerMode, int]] = {v: k for k, v in SCALER_MODE_BY_CODE.items()}


# ---------------------------------------------------------------------------------------------
# Image enhancement -- wire codes O{n}E{0-3}
# ---------------------------------------------------------------------------------------------


class ImageEnhancement(StrEnum):
    """Per-output image enhancement strength."""

    OFF = "off"
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"


IMAGE_ENHANCEMENT_BY_CODE: Final[dict[int, ImageEnhancement]] = {
    0: ImageEnhancement.OFF,
    1: ImageEnhancement.WEAK,
    2: ImageEnhancement.MEDIUM,
    3: ImageEnhancement.STRONG,
}
CODE_BY_IMAGE_ENHANCEMENT: Final[dict[ImageEnhancement, int]] = {
    v: k for k, v in IMAGE_ENHANCEMENT_BY_CODE.items()
}


# ---------------------------------------------------------------------------------------------
# Extracted-audio delay -- wire codes O{n}D{0-7}
# ---------------------------------------------------------------------------------------------


class AudioDelay(StrEnum):
    """Per-output extracted-audio delay.

    Code 0 is **Bypass**, not "0 ms". That distinction is why this is modelled as an enumeration
    rather than a number entity with a 90 ms step: bypassing the delay line is a different thing
    from delaying by zero, and a number entity would render it as "0 ms" and lose the difference.
    """

    BYPASS = "bypass"
    MS_90 = "ms_90"
    MS_180 = "ms_180"
    MS_270 = "ms_270"
    MS_360 = "ms_360"
    MS_450 = "ms_450"
    MS_540 = "ms_540"
    MS_630 = "ms_630"


AUDIO_DELAY_BY_CODE: Final[dict[int, AudioDelay]] = {
    0: AudioDelay.BYPASS,
    1: AudioDelay.MS_90,
    2: AudioDelay.MS_180,
    3: AudioDelay.MS_270,
    4: AudioDelay.MS_360,
    5: AudioDelay.MS_450,
    6: AudioDelay.MS_540,
    7: AudioDelay.MS_630,
}
CODE_BY_AUDIO_DELAY: Final[dict[AudioDelay, int]] = {v: k for k, v in AUDIO_DELAY_BY_CODE.items()}

#: Milliseconds of delay for each member, or ``None`` for bypass. Exposed as an entity attribute
#: so automations can reason numerically without re-deriving it from the option key.
AUDIO_DELAY_MS: Final[dict[AudioDelay, int | None]] = {
    AudioDelay.BYPASS: None,
    AudioDelay.MS_90: 90,
    AudioDelay.MS_180: 180,
    AudioDelay.MS_270: 270,
    AudioDelay.MS_360: 360,
    AudioDelay.MS_450: 450,
    AudioDelay.MS_540: 540,
    AudioDelay.MS_630: 630,
}


# ---------------------------------------------------------------------------------------------
# Extracted-audio matrix bind mode -- wire codes AMB{0-2}
# ---------------------------------------------------------------------------------------------


class BindMode(StrEnum):
    """How extracted audio is associated with video.

    This is device-level, and it changes the meaning of the per-output audio route: in
    ``BIND_TO_OUTPUT`` and ``BIND_TO_INPUT`` the audio route follows the video route and the
    per-output audio selection is not independently meaningful. Read this before interpreting
    anything else in the audio status.
    """

    BIND_TO_OUTPUT = "bind_to_output"
    BIND_TO_INPUT = "bind_to_input"
    MATRIX = "matrix"


BIND_MODE_BY_CODE: Final[dict[int, BindMode]] = {
    0: BindMode.BIND_TO_OUTPUT,
    1: BindMode.BIND_TO_INPUT,
    2: BindMode.MATRIX,
}
CODE_BY_BIND_MODE: Final[dict[BindMode, int]] = {v: k for k, v in BIND_MODE_BY_CODE.items()}


# ---------------------------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------------------------


def decode_on_off(token: str) -> bool | None:
    """Decode an ``ON``/``OFF`` suffix.

    Returns ``None`` for anything else, so a firmware that answers ``ENABLE`` does not silently
    read as off.
    """
    match token.upper():
        case "ON":
            return True
        case "OFF":
            return False
        case _:
            return None


def encode_on_off(value: bool) -> str:
    """Encode a boolean as the ``ON``/``OFF`` token the matrix expects."""
    return "ON" if value else "OFF"
