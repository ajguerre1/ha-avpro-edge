"""The AVPro Edge AUHD CGI wire protocol: URL construction and response parsing.

Pure. No I/O, no Home Assistant imports, no clock. **Nothing in this module raises** -- every
failure is returned as a value, because a malformed reply from one endpoint must not be able to
take down the integration.

The protocol is the one the unit's own web UI speaks. Status is fetched with a plain ``GET`` and
comes back as ``Key=field&field&...``; commands are a ``GET`` with a ``button=`` query parameter.

Two properties of this firmware shape everything here.

**A missing endpoint answers 200, not 404.** The device replies ``HTTP/1.1 200 OK`` with an HTML
body reading "Sorry, the page you requested was not found." Verified against ``TMDSDivSta.CGI``
(absent on firmware V1.41) and two endpoints from a newer firmware's API. Status codes therefore
carry no information and are never consulted: a response is valid only if its body starts with the
``Key=`` prefix that endpoint is supposed to produce.

**Fields are ``&``-separated and the separator is not escaped.** Port names are user-settable and
are returned inline, so a name containing ``&`` shifts every subsequent field. The parser cannot
repair that, so it detects it by arity and refuses the whole response rather than assigning eight
names one position out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# ---------------------------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------------------------


class StatusEndpoint(StrEnum):
    """Read-only status endpoints. The value is the path, verbatim from the web UI."""

    WEB = "WEBDivSta.CGI"
    VIDEO = "VIDDivSta.CGI"
    AUDIO = "AUDDivSta.CGI"
    SYSTEM = "SYSDivSta.CGI"
    INFO = "INFDivSta.CGI"
    EDID = "EDIDDivSta.CGI"
    NETWORK = "NETDivSta.CGI"
    #: Absent on firmware V1.41. Requested only to establish the capability; its absence is
    #: expected and must never fail an update.
    TMDS = "TMDSDivSta.CGI"


#: The ``Key=`` prefix each status endpoint must produce. A body that does not start with its
#: endpoint's key is not that endpoint's data, whatever the status code said.
STATUS_KEY: Final[dict[StatusEndpoint, str]] = {
    StatusEndpoint.WEB: "WebSta",
    StatusEndpoint.VIDEO: "VidSta",
    StatusEndpoint.AUDIO: "AudSta",
    StatusEndpoint.SYSTEM: "SysSta",
    StatusEndpoint.INFO: "INFSta",
    StatusEndpoint.EDID: "EdidSta",
    StatusEndpoint.NETWORK: "NetSta",
    StatusEndpoint.TMDS: "TmdsSta",
}


class CommandEndpoint(StrEnum):
    """Write endpoints.

    Spelling is verbatim from the web UI and is deliberately inconsistent -- ``EdidsendCmd`` has a
    lowercase ``s`` where every sibling has a capital. Do not "correct" it.

    The network endpoints (``NetSendCmd.CGI``, ``NetDHCPSendCmd.CGI``) are **intentionally
    absent**. Nothing in this integration should be able to change the matrix's IP configuration,
    including the raw-command escape hatch.
    """

    VIDEO = "TimSendCmd.CGI"
    AUDIO = "AudSendCmd.CGI"
    SYSTEM = "SysSendCmd.CGI"
    TMDS = "TmdsSendCmd.CGI"
    EDID = "EdidsendCmd.CGI"
    NAME = "NameSendCmd.CGI"


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------


class ParseOutcome(StrEnum):
    """Why a status body was or was not usable."""

    OK = "ok"
    #: The device served its HTML "page not found" body. This endpoint does not exist on this
    #: firmware. A capability signal, not an error.
    NOT_FOUND = "not_found"
    #: The device answered ``NO SUPPORT`` -- the command or query is not valid for this model.
    UNSUPPORTED = "unsupported"
    #: A 200 with something we cannot interpret: truncated, empty, or the wrong endpoint's key.
    MALFORMED = "malformed"


#: Marker the firmware returns for a command or query it does not implement. Model- and
#: firmware-dependent, per the vendor's own driver changelog, so it is detected rather than
#: predicted.
NO_SUPPORT: Final = "NO SUPPORT"


@dataclass(frozen=True, slots=True)
class ParsedStatus:
    """The result of parsing one status body. Never an exception."""

    outcome: ParseOutcome
    fields: tuple[str, ...] = ()
    #: Short, non-sensitive description of why parsing failed. Never contains field values,
    #: because those are the owner's room and source names.
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True when ``fields`` is trustworthy."""
        return self.outcome is ParseOutcome.OK


def _looks_like_not_found(body: str) -> bool:
    """Detect the firmware's HTML "page not found" body, which arrives with status 200."""
    lowered = body.lower()
    return "page you requested was not found" in lowered or lowered.startswith("<html")


def parse_status(endpoint: StatusEndpoint, body: str) -> ParsedStatus:
    """Parse a status body into its fields.

    ``body`` is the decoded response text. The HTTP status code is deliberately not a parameter:
    this firmware returns 200 for absent endpoints, so the code carries no information.
    """
    text = body.strip()

    if not text:
        return ParsedStatus(ParseOutcome.MALFORMED, detail="empty body")

    if NO_SUPPORT in text.upper():
        return ParsedStatus(ParseOutcome.UNSUPPORTED, detail="device reported NO SUPPORT")

    if _looks_like_not_found(text):
        return ParsedStatus(ParseOutcome.NOT_FOUND, detail="endpoint absent on this firmware")

    prefix = f"{STATUS_KEY[endpoint]}="
    if not text.startswith(prefix):
        # Do not echo the body: on this device an unexpected body may still be site data.
        return ParsedStatus(
            ParseOutcome.MALFORMED,
            detail=f"expected {prefix!r} prefix, got {len(text)} bytes of something else",
        )

    payload = text[len(prefix) :]
    parts = payload.split("&")

    # INFDivSta.CGI ends with a trailing '&', producing one empty tail field. Exactly one is
    # dropped -- dropping every empty field would silently swallow a genuinely blank port name.
    if parts and parts[-1] == "":
        parts.pop()

    return ParsedStatus(ParseOutcome.OK, fields=tuple(parts))


def expect_fields(parsed: ParsedStatus, count: int) -> ParsedStatus:
    """Demote a parsed status to MALFORMED unless it has exactly ``count`` fields.

    This is the arity guard. A port name containing ``&`` splits into two fields and shifts
    everything after it; there is no way to tell which field was split, so the only safe response
    is to reject the response and keep the previous values. The detail records the counts only --
    never the fields, which are room and source names.
    """
    if not parsed.ok:
        return parsed
    if len(parsed.fields) != count:
        return ParsedStatus(
            ParseOutcome.MALFORMED,
            detail=(
                f"expected {count} fields, got {len(parsed.fields)} "
                "(a port name probably contains '&')"
            ),
        )
    return parsed


# ---------------------------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------------------------


def status_path(endpoint: StatusEndpoint, cache_buster: str) -> str:
    """Build the request path for a status read.

    The web UI appends a random query string to defeat caching, and the device stamps status
    responses ``Cache-control: private``. The same shape is reproduced here: it costs nothing and
    it is the form this hardware is known to serve.

    ``cache_buster`` is supplied by the caller rather than generated here so this module stays
    pure and every URL is reproducible in a test.
    """
    return f"/{endpoint.value}?{cache_buster}"


def command_path(endpoint: CommandEndpoint, button: str, cache_buster: str) -> str:
    """Build the request path for a command.

    The trailing ``+<random>`` is not decoration. The web UI sends
    ``?button=<code>+<random>`` and that is the only form this firmware is *observed* to accept;
    the device evidently matches the code as a prefix and ignores the tail. Sending a bare
    ``?button=<code>`` has never been tested against the hardware, so the proven form is used.
    """
    return f"/{endpoint.value}?button={button}+{cache_buster}"


# ---------------------------------------------------------------------------------------------
# Command codes
# ---------------------------------------------------------------------------------------------

#: Output index meaning "every output" in the video-routing command. The web UI exposes this as a
#: fifth row of buttons; it routes all outputs in a single request.
ALL_OUTPUTS: Final = 5


def video_route(output: int, source: int) -> str:
    """``O{output}I{source}`` -- route a video output to an input."""
    return f"O{output}I{source}"


def video_route_all(source: int) -> str:
    """``O5I{source}`` -- route every output to one input in a single request."""
    return f"O{ALL_OUTPUTS}I{source}"


def audio_route(output: int, source: int) -> str:
    """``AO{output}I{source}`` -- route an extracted-audio output to an input."""
    return f"AO{output}I{source}"


def extracted_audio(output: int, enabled: bool) -> str:
    """``O{output}A{ON|OFF}`` -- enable or disable an extracted-audio output."""
    return f"O{output}A{'ON' if enabled else 'OFF'}"


def audio_delay(output: int, code: int) -> str:
    """``O{output}D{code}`` -- set the extracted-audio delay."""
    return f"O{output}D{code}"


def bind_mode(code: int) -> str:
    """``AMB{code}`` -- set the device-level extracted-audio matrix mode."""
    return f"AMB{code}"


def scaler_mode(output: int, code: int) -> str:
    """``O{output}V{code}`` -- set the video scaler mode."""
    return f"O{output}V{code}"


def image_enhancement(output: int, code: int) -> str:
    """``O{output}E{code}`` -- set the image enhancement strength."""
    return f"O{output}E{code}"


def test_pattern(output: int, enabled: bool) -> str:
    """``O{output}SGM{ON|OFF}`` -- enable or disable the built-in signal generator."""
    return f"O{output}SGM{'ON' if enabled else 'OFF'}"


def tmds_stream(output: int, enabled: bool) -> str:
    """``T{output}A{ON|OFF}`` -- the TMDS tab's control.

    Write-only on firmware V1.41: ``TMDSDivSta.CGI`` is absent, so there is nothing to read this
    back from. Whether it gates the output stream or an input port's power is not established
    from the CGI interface alone -- the telnet reference distinguishes ``SET OUTx STREAM`` from
    ``SET INx TMDS`` -- which is why the entity built on it declares ``assumed_state``.
    """
    return f"T{output}A{'ON' if enabled else 'OFF'}"


# ---------------------------------------------------------------------------------------------
# Token parsing
# ---------------------------------------------------------------------------------------------
#
# The video, audio and system status bodies carry *self-describing* tokens: ``O2I4`` states its
# own output and input. Those are matched by pattern wherever they appear rather than read by
# position, so a firmware that reorders or adds fields still yields correct values instead of
# quietly shifted ones.
#
# The remaining bodies (names, signal descriptions, EDID, network) carry opaque free text that
# cannot be classified, so those are read positionally and defended by the arity guard instead.
#
# Every pattern is anchored and the set is mutually exclusive -- ``AO1I2`` cannot match the video
# route pattern, and ``O1SGMON`` cannot match the scaler pattern.

_RE_VIDEO_ROUTE: Final = re.compile(r"^O(\d+)I(\d+)$")
_RE_AUDIO_ROUTE: Final = re.compile(r"^AO(\d+)I(\d+)$")
_RE_EXTRACTED_AUDIO: Final = re.compile(r"^O(\d+)A(ON|OFF)$")
_RE_AUDIO_DELAY: Final = re.compile(r"^O(\d+)D(\d+)$")
_RE_BIND_MODE: Final = re.compile(r"^AMB(\d+)$")
_RE_SCALER: Final = re.compile(r"^O(\d+)V(\d+)$")
_RE_IMAGE_ENHANCEMENT: Final = re.compile(r"^O(\d+)E(\d+)$")
_RE_TEST_PATTERN: Final = re.compile(r"^O(\d+)SGM(ON|OFF)$")


def _index_and_int(pattern: re.Pattern[str], token: str) -> tuple[int, int] | None:
    match = pattern.match(token.strip())
    return (int(match[1]), int(match[2])) if match else None


def _index_and_flag(pattern: re.Pattern[str], token: str) -> tuple[int, bool] | None:
    match = pattern.match(token.strip().upper())
    return (int(match[1]), match[2] == "ON") if match else None


def parse_video_route(token: str) -> tuple[int, int] | None:
    """``O2I4`` -> ``(2, 4)``: output 2 is fed by input 4."""
    return _index_and_int(_RE_VIDEO_ROUTE, token)


def parse_audio_route(token: str) -> tuple[int, int] | None:
    """``AO2I4`` -> ``(2, 4)``: extracted-audio output 2 is fed by input 4."""
    return _index_and_int(_RE_AUDIO_ROUTE, token)


def parse_extracted_audio(token: str) -> tuple[int, bool] | None:
    """``O2AON`` -> ``(2, True)``."""
    return _index_and_flag(_RE_EXTRACTED_AUDIO, token)


def parse_audio_delay(token: str) -> tuple[int, int] | None:
    """``O2D3`` -> ``(2, 3)``: output 2, delay code 3."""
    return _index_and_int(_RE_AUDIO_DELAY, token)


def parse_bind_mode(token: str) -> int | None:
    """``AMB2`` -> ``2``."""
    match = _RE_BIND_MODE.match(token.strip())
    return int(match[1]) if match else None


def parse_scaler_mode(token: str) -> tuple[int, int] | None:
    """``O2V4`` -> ``(2, 4)``: output 2, scaler code 4."""
    return _index_and_int(_RE_SCALER, token)


def parse_image_enhancement(token: str) -> tuple[int, int] | None:
    """``O2E1`` -> ``(2, 1)``: output 2, enhancement code 1."""
    return _index_and_int(_RE_IMAGE_ENHANCEMENT, token)


def parse_test_pattern(token: str) -> tuple[int, bool] | None:
    """``O2SGMON`` -> ``(2, True)``."""
    return _index_and_flag(_RE_TEST_PATTERN, token)


# ---------------------------------------------------------------------------------------------
# Port names
# ---------------------------------------------------------------------------------------------

#: Characters that must never appear in a port name.
#:
#: ``&`` is the field separator in both directions -- it would corrupt the read *and* split the
#: write. The rest are query-string metacharacters: whether this firmware percent-decodes at all
#: is unverified, so anything with a reserved meaning in a URL is refused rather than guessed at.
FORBIDDEN_NAME_CHARS: Final = frozenset("&+%#?=")


def validate_port_name(name: str, *, max_length: int = 16) -> str | None:
    """Return a human-readable reason the name is unusable, or ``None`` if it is fine."""
    if not name:
        return "name must not be empty"
    if len(name) > max_length:
        return f"name must be at most {max_length} characters"
    bad = sorted(FORBIDDEN_NAME_CHARS & set(name))
    if bad:
        return f"name must not contain {' '.join(bad)}"
    if any(ord(c) < 0x20 or ord(c) > 0x7E for c in name):
        return "name must be printable ASCII"
    return None


def set_names(outputs: list[str], inputs: list[str]) -> str:
    """Build the ``button=`` value for a rename.

    The device rewrites **all** port names in one request, so this is inherently a
    read-modify-write: callers must supply the current names for every port they are not
    changing, and must hold the transport lock across both halves or a concurrent poll can
    interleave and produce a write derived from a stale read.
    """
    return "".join(f"{name}&" for name in (*outputs, *inputs))
