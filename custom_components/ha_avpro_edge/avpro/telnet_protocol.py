"""The AVPro Edge AUHD telnet line grammar.

Pure. No I/O, no Home Assistant imports, no clock. **Nothing here raises** -- an unrecognised line
yields nothing rather than an exception, because a single odd line from a firmware revision must
not be able to fail an update.

The protocol is line-oriented ASCII, ``\\r\\n``-terminated, and commands require the trailing
return. Every line is self-describing: ``OUT1 VS IN2`` names its own output and input, so lines
are matched by pattern and never read by position. That also means responses and unsolicited
pushes can share one stream without ambiguity, which they do -- the device appends routing dumps
to command replies, so no request/response correlation is possible or needed.

The grammar below was taken from a live ``GET STA`` on firmware 1.72 and cross-checked against the
unit's own ``H`` help output. Where a value also exists over HTTP, both decode to the same
canonical key and the same Python value -- ``IN1 EDID 30`` and HTTP's ``EDIDU1`` are the same
EDID, and a test asserts it.
"""

from __future__ import annotations

import re
from typing import Any, Final

from .models import (
    AUDIO_DELAY_BY_CODE,
    BIND_MODE_BY_CODE,
    IMAGE_ENHANCEMENT_BY_CODE,
    SCALER_MODE_BY_CODE,
)
from .report import DeviceReport

# ---------------------------------------------------------------------------------------------
# Canonical state keys this transport can produce
# ---------------------------------------------------------------------------------------------
# Shared with const.py by value rather than by import: this package must stay free of Home
# Assistant imports, and const.py is a Home Assistant module. A test asserts the two agree.

KEY_VIDEO_ROUTE: Final = "video_route"
KEY_AUDIO_ROUTE: Final = "audio_route"
KEY_EXTRACTED_AUDIO: Final = "extracted_audio"
KEY_AUDIO_DELAY: Final = "audio_delay"
KEY_SCALER: Final = "scaler"
KEY_IMAGE_ENHANCEMENT: Final = "image_enhancement"
KEY_TEST_PATTERN: Final = "test_pattern"
KEY_EDID: Final = "edid"
KEY_STREAM: Final = "stream"
KEY_INPUT_POWER: Final = "input_power"
KEY_BIND_MODE: Final = "bind_mode"
KEY_KEY_LOCK: Final = "key_lock"
KEY_LCD_TIMEOUT: Final = "lcd_timeout"
KEY_MODEL: Final = "model"
KEY_FIRMWARE: Final = "firmware"
KEY_MAC: Final = "mac"
KEY_ADDRESS: Final = "address"

#: Keys only this transport can supply. HTTP has no status endpoint for any of them, which is the
#: substantive reason telnet is the primary transport rather than an alternative to it.
TELNET_ONLY_KEYS: Final[frozenset[str]] = frozenset(
    {KEY_STREAM, KEY_INPUT_POWER, KEY_KEY_LOCK, KEY_LCD_TIMEOUT, KEY_ADDRESS}
)


def _port_key(kind: str, index: int) -> str:
    return f"{kind}_{index}"


# ---------------------------------------------------------------------------------------------
# Line patterns
# ---------------------------------------------------------------------------------------------
#
# Ordered most specific first where prefixes overlap: `OUT1 EXADL PH0` and `OUT1 EXA DIS` share a
# prefix, and `IMAGE ENH` must not be mistaken for anything else. Each pattern is anchored, so the
# set is unambiguous regardless of order -- the ordering is for the reader.

_ON_OFF = {"ON": True, "OFF": False}
_EN_DIS = {"EN": True, "DIS": False}


def _out(match: re.Match[str], kind: str, value: Any) -> tuple[str, Any]:
    return _port_key(kind, int(match[1])), value


_PATTERNS: Final[tuple[tuple[re.Pattern[str], Any], ...]] = (
    # --- routing -------------------------------------------------------------------------
    (re.compile(r"^OUT(\d+) VS IN(\d+)$"), lambda m: _out(m, KEY_VIDEO_ROUTE, int(m[2]))),
    (re.compile(r"^OUT(\d+) AS IN(\d+)$"), lambda m: _out(m, KEY_AUDIO_ROUTE, int(m[2]))),
    # --- extracted audio -----------------------------------------------------------------
    (
        re.compile(r"^OUT(\d+) EXADL PH(\d+)$"),
        lambda m: _out(m, KEY_AUDIO_DELAY, AUDIO_DELAY_BY_CODE.get(int(m[2]))),
    ),
    (
        re.compile(r"^OUT(\d+) EXA (EN|DIS)$"),
        lambda m: _out(m, KEY_EXTRACTED_AUDIO, _EN_DIS[m[2]]),
    ),
    (
        re.compile(r"^EXAMX MODE(\d+)$"),
        lambda m: (KEY_BIND_MODE, BIND_MODE_BY_CODE.get(int(m[1]))),
    ),
    # --- per-output video ----------------------------------------------------------------
    (
        re.compile(r"^OUT(\d+) IMAGE ENH (\d+)$"),
        lambda m: _out(m, KEY_IMAGE_ENHANCEMENT, IMAGE_ENHANCEMENT_BY_CODE.get(int(m[2]))),
    ),
    (
        re.compile(r"^OUT(\d+) VIDEO (\d+)$"),
        lambda m: _out(m, KEY_SCALER, SCALER_MODE_BY_CODE.get(int(m[2]))),
    ),
    (re.compile(r"^OUT(\d+) SGM (EN|DIS)$"), lambda m: _out(m, KEY_TEST_PATTERN, _EN_DIS[m[2]])),
    # --- the controls HTTP cannot reach ---------------------------------------------------
    (re.compile(r"^OUT(\d+) STREAM (ON|OFF)$"), lambda m: _out(m, KEY_STREAM, _ON_OFF[m[2]])),
    (re.compile(r"^IN(\d+) TMDS (ON|OFF)$"), lambda m: _out(m, KEY_INPUT_POWER, _ON_OFF[m[2]])),
    (re.compile(r"^KEY LOCK (ON|OFF)$"), lambda m: (KEY_KEY_LOCK, _ON_OFF[m[1]])),
    (re.compile(r"^LCD ON T(\d+)$"), lambda m: (KEY_LCD_TIMEOUT, int(m[1]))),
    # --- input EDID, as a numeric index ---------------------------------------------------
    (re.compile(r"^IN(\d+) EDID (\d+)$"), lambda m: _out(m, KEY_EDID, _edid_option(int(m[2])))),
    # --- identity --------------------------------------------------------------------------
    (re.compile(r"^ADDR (\d+)$"), lambda m: (KEY_ADDRESS, m[1])),
    (re.compile(r"^MAC ([0-9A-Fa-f.:-]+)$"), lambda m: (KEY_MAC, _normalise_mac(m[1]))),
)


# ---------------------------------------------------------------------------------------------
# EDID: telnet's index vs HTTP's token
# ---------------------------------------------------------------------------------------------
#
# Telnet numbers EDIDs 0-32; HTTP names them EDIDD1..30 / EDIDU1..3 (and offers copy-from-output
# as a separate concept). The two describe the same presets -- telnet's 30 is USER1_EDID and
# HTTP's EDIDU1 is "User1 EDID" -- so both decode to the same option key and a user switching
# transports sees no change.
#
# Telnet's index is 0-based over the 30 fixed presets, then 30/31/32 for the user buffers; HTTP's
# EDIDD tokens are 1-based. Hence the off-by-one, which is the device's, not ours.

_EDID_USER_BASE: Final = 30


def _edid_option(index: int) -> str | None:
    """Telnet EDID index -> the option key shared with the HTTP transport."""
    if 0 <= index < _EDID_USER_BASE:
        return f"preset_{index + 1}"
    if _EDID_USER_BASE <= index <= 32:
        return f"user_{index - _EDID_USER_BASE + 1}"
    return None


def edid_index(option: str) -> int | None:
    """The inverse: option key -> the index this transport writes.

    ``copy_output_N`` has no index -- it is a separate command (``SET INx EDID CY OUTy``) -- so it
    returns ``None`` and the caller uses that form instead.
    """
    if option.startswith("preset_"):
        number = int(option.removeprefix("preset_"))
        return number - 1 if 1 <= number <= _EDID_USER_BASE else None
    if option.startswith("user_"):
        number = int(option.removeprefix("user_"))
        return _EDID_USER_BASE + number - 1 if 1 <= number <= 3 else None
    return None


def _normalise_mac(raw: str) -> str:
    """``aa.bb.cc.dd.ee.ff`` -> ``aa:bb:cc:dd:ee:ff``.

    Telnet separates MAC octets with dots and HTTP with colons. Normalising here means the config
    flow gets one format whichever transport identified the device, so an entry created over
    telnet matches one created over HTTP rather than appearing as a second unit.
    """
    return raw.replace(".", ":").replace("-", ":").lower()


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------


def parse_line(line: str) -> tuple[str, Any] | None:
    """One line to one ``(state_key, value)``, or ``None`` if it says nothing we model.

    Returning ``None`` covers three cases deliberately treated alike: a line from a newer
    firmware, a line we do not care about (``RIP``, ``NMK``, ``DHCP``), and a garbled line. None
    of them should stop the rest of a dump being read.
    """
    text = line.strip()
    if not text:
        return None

    for pattern, build in _PATTERNS:
        if match := pattern.match(text):
            key, value = build(match)
            # An unrecognised enum code decodes to None. Drop it rather than recording a null,
            # so a firmware with a new scaler mode reads as "not reported" and not as "off".
            return None if value is None else (key, value)
    return None


def parse_lines(text: str, *, complete: bool = False) -> DeviceReport:
    """Fold a block of telnet output into one report.

    ``complete`` is the caller's assertion, not something inferable from the text: only the caller
    knows whether this block is the answer to ``GET STA`` or an unsolicited push.
    """
    values: dict[str, Any] = {}
    for line in text.replace("\r", "\n").split("\n"):
        if pair := parse_line(line):
            values[pair[0]] = pair[1]
    return DeviceReport(values=values, complete=complete and bool(values))


#: A ``GET STA`` reply is recognisable by carrying the fields only it includes. Used to decide
#: whether an arriving block completes the census, since the device frames nothing.
CENSUS_MARKERS: Final[frozenset[str]] = frozenset({KEY_ADDRESS, KEY_KEY_LOCK, KEY_MAC})


def looks_like_census(report: DeviceReport) -> bool:
    """Whether a report carries the fields only a full ``GET STA`` produces."""
    return bool(CENSUS_MARKERS & set(report.values))
