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
# LCD backlight timeout -- wire codes T{0-3}, telnet only
# ---------------------------------------------------------------------------------------------


class LcdTimeout(StrEnum):
    """How long the matrix's own front-panel backlight stays lit.

    Telnet only: the CGI interface has no status endpoint for it.

    **Both claims here are now measured**, which was not true when this shipped.

    *That there are exactly four*: ``scripts/probe_lcd_keylock.py`` wrote ``SET LCD ON T{n}`` for
    n in 0-5 against the live matrix and read each back from ``GET STA`` -- 0-3 accepted, 4 and 5
    refused with the value staying at 3.

    *What each one means* was **inferred** until 2026-08-29, from the manufacturer's own
    control-system driver listing exactly these four in this order. Good corroboration, and still
    a guess: nothing on either wire can report a backlight, so an off-by-one in the list order
    would have produced four confidently wrong user-facing labels with no way to notice.

    Settled by a person standing at the matrix (T-L6). ``T1`` went dark at ~15 s and ``T3`` at
    ~60 s, pinning both ends; ``T0`` stayed lit well past 90 s -- longer than the longest timeout
    in the set, so it is not a timeout. ``T2`` = 30 s follows by elimination, four values against
    four labels with both ends fixed.
    """

    ALWAYS_ON = "always_on"
    SECONDS_15 = "seconds_15"
    SECONDS_30 = "seconds_30"
    SECONDS_60 = "seconds_60"


LCD_TIMEOUT_BY_CODE: Final[dict[int, LcdTimeout]] = {
    0: LcdTimeout.ALWAYS_ON,
    1: LcdTimeout.SECONDS_15,
    2: LcdTimeout.SECONDS_30,
    3: LcdTimeout.SECONDS_60,
}
CODE_BY_LCD_TIMEOUT: Final[dict[LcdTimeout, int]] = {v: k for k, v in LCD_TIMEOUT_BY_CODE.items()}


# ---------------------------------------------------------------------------------------------
# EDID -- wire tokens are the same in both directions
# ---------------------------------------------------------------------------------------------
#
# Unlike every other setting here, EDID is not a small integer. The status endpoint returns a
# token such as ``EDIDU1`` and a write sends ``<token>IN<input>``, so the read and write
# vocabularies are identical -- the device's own web UI stores the token as the option value and
# echoes it straight back. That is why these are carried verbatim rather than decoded into an
# index: there is nothing to decode.
#
# Three families:
#   EDIDD1..EDIDD30  fixed presets, resolution + audio channels + optional HDR
#   EDIDU1..EDIDU3   the three user EDID buffers
#   EDIDO1..EDIDO4   copy the EDID of the display on that output
#
# The telnet reference numbers the presets 0-32 (30 fixed + 3 user) and treats copy-from-output as
# a separate command; the totals agree, which is the cross-check that these 37 are the whole set.

#: What the device calls each EDID, keyed by its wire token. Verbatim from the unit's own web UI.
EDID_LABEL_BY_TOKEN: Final[dict[str, str]] = {
    "EDIDD1": "1080P 2CH",
    "EDIDD2": "1080P 6CH",
    "EDIDD3": "1080P 8CH",
    "EDIDD4": "1080P 3D 2CH",
    "EDIDD5": "1080P 3D 6CH",
    "EDIDD6": "1080P 3D 8CH",
    "EDIDD7": "4K30HZ 3D 2CH",
    "EDIDD8": "4K30HZ 3D 6CH",
    "EDIDD9": "4K30HZ 3D 8CH",
    "EDIDD10": "4K60HZ(Y420) 3D 2CH",
    "EDIDD11": "4K60HZ(Y420) 3D 6CH",
    "EDIDD12": "4K60HZ(Y420) 3D 8CH",
    "EDIDD13": "4K60HZ 3D 2CH",
    "EDIDD14": "4K60HZ 3D 6CH",
    "EDIDD15": "4K60HZ 3D 8CH",
    "EDIDD16": "1080P 2CH HDR",
    "EDIDD17": "1080P 6CH HDR",
    "EDIDD18": "1080P 8CH HDR",
    "EDIDD19": "1080P 3D 2CH HDR",
    "EDIDD20": "1080P 3D 6CH HDR",
    "EDIDD21": "1080P 3D 8CH HDR",
    "EDIDD22": "4K30HZ 3D 2CH HDR",
    "EDIDD23": "4K30HZ 3D 6CH HDR",
    "EDIDD24": "4K30HZ 3D 8CH HDR",
    "EDIDD25": "4K60HZ(Y420) 3D 2CH HDR",
    "EDIDD26": "4K60HZ(Y420) 3D 6CH HDR",
    "EDIDD27": "4K60HZ(Y420) 3D 8CH HDR",
    "EDIDD28": "4K60HZ 3D 2CH HDR",
    "EDIDD29": "4K60HZ 3D 6CH HDR",
    "EDIDD30": "4K60HZ 3D 8CH HDR",
    "EDIDU1": "User1 EDID",
    "EDIDU2": "User2 EDID",
    "EDIDU3": "User3 EDID",
    "EDIDO1": "Copy From Out1",
    "EDIDO2": "Copy From Out2",
    "EDIDO3": "Copy From Out3",
    "EDIDO4": "Copy From Out4",
}

#: Wire-token family letter -> the option-key prefix it becomes.
_EDID_FAMILIES: Final[dict[str, str]] = {"D": "preset", "U": "user", "O": "copy_output"}


def _edid_option_key(token: str) -> str:
    """``"EDIDD12"`` -> ``"preset_12"``.

    Home Assistant requires translation keys to match ``[a-z0-9-_]+``, so the device's uppercase
    tokens cannot be used as option values directly. Rather than lowercasing them into
    ``edidd12`` -- which is unreadable and encodes nothing -- the family letter is expanded, so an
    automation reads ``user_1`` and ``copy_output_3`` instead of vendor shorthand.
    """
    family, number = token[4], token[5:]
    return f"{_EDID_FAMILIES[family]}_{number}"


#: Wire token <-> Home Assistant option key. ``MatrixState`` stores the *option key*, so the
#: pending overlay, the poll and the entity all speak one vocabulary and confirmation stays a
#: plain equality check. Only :func:`edid_command` converts back to the wire.
EDID_OPTION_BY_TOKEN: Final[dict[str, str]] = {
    token: _edid_option_key(token) for token in EDID_LABEL_BY_TOKEN
}
EDID_TOKEN_BY_OPTION: Final[dict[str, str]] = {
    option: token for token, option in EDID_OPTION_BY_TOKEN.items()
}

#: Option key -> the device's own label for it.
EDID_OPTIONS: Final[dict[str, str]] = {
    option: EDID_LABEL_BY_TOKEN[token] for token, option in EDID_OPTION_BY_TOKEN.items()
}


def decode_edid(token: str) -> str | None:
    """A wire token as an option key, or ``None`` for one this firmware invented."""
    return EDID_OPTION_BY_TOKEN.get(token.strip().upper())


def edid_command(option: str, source: int) -> str:
    """``("user_1", 3)`` -> ``"EDIDU1IN3"`` -- assign an EDID to an input."""
    return f"{EDID_TOKEN_BY_OPTION[option]}IN{source}"


# ---------------------------------------------------------------------------------------------
# Detected signal -- free text, with one token that means "nothing"
# ---------------------------------------------------------------------------------------------


#: What the matrix puts in a signal field for a port carrying nothing.
#:
#: **Measured, not inferred.** Unplugging a source from the live AC-MX44-AUHD on V1.41 produced the
#: literal ``NO SIGNAL``, and plugging it back produced the format string again (T-L8, 2026-08-29).
#:
#: Two guesses preceded that measurement and both were wrong. The question had been framed as
#: whether a blank field meant "nothing connected" or "not measured" -- a false dichotomy, because
#: the device blanks nothing. It says so in words.
#:
#: A set rather than a constant because this is one firmware on one model, and the AUHD family does
#: not share a vocabulary. A spelling nobody has seen must read as *some signal* rather than as
#: none: reporting a port live when it is dark is a smaller lie than reporting it dark when it is
#: carrying a picture, because only the second makes an automation act.
NO_SIGNAL_TOKENS: Final[frozenset[str]] = frozenset({"NO SIGNAL"})


def signal_present(raw: str | None) -> bool | None:
    """Whether a port is carrying a picture. Three answers, and all three are distinct.

    * ``None`` -- never read. Absence of a reading, not a reading of absence.
    * ``False`` -- the matrix looked and reported nothing.
    * ``True`` -- a real format string.

    Every caller used to ask ``bool(raw)`` instead, which is right for exactly two of those and
    catastrophically wrong for the third: ``NO SIGNAL`` is a non-empty string, so **the one case
    this distinction exists for reported "signal present"**. The signal binary sensor said
    *Connected* for a port with a cable hanging out of it, and ``media_player.state`` said ``on``
    for an output whose source was unplugged -- the exact case its own docstring describes as
    ``IDLE``.

    Nothing caught it because the fake device modelled "no signal" as an empty string, which is not
    what this hardware does. The suite agreed with the model perfectly.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        # Not observed on this firmware -- it says NO SIGNAL rather than sending an empty field --
        # so an empty one is a body we do not understand, and that is not the same as darkness.
        return None
    return text.upper() not in NO_SIGNAL_TOKENS


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
