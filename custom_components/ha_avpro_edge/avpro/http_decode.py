"""Turn parsed CGI responses into transport-neutral reports.

Pure. No I/O, no Home Assistant imports.

This is the HTTP half of the seam. Where :mod:`.telnet_protocol` maps ``OUT1 VS IN2`` to
``{"video_route_1": 2}``, this maps a parsed ``VidSta`` body to the same thing. Everything above
the transports sees only the result, which is why adding telnet changed nothing in the state
model, the overlay, or the entities.

The endpoint-shaped knowledge that used to live in seven ``fold_*`` functions lives here instead,
where it belongs: it is a property of the HTTP interface, not of the matrix.
"""

from __future__ import annotations

from typing import Any, Final

from . import protocol as p
from .models import (
    AUDIO_DELAY_BY_CODE,
    BIND_MODE_BY_CODE,
    IMAGE_ENHANCEMENT_BY_CODE,
    SCALER_MODE_BY_CODE,
    decode_edid,
)
from .protocol import ParsedStatus, StatusEndpoint
from .report import EMPTY, DeviceReport

#: Number of leading fields in the web status before the port names begin: model, firmware.
_WEB_HEADER_FIELDS: Final = 2

#: Kinds this transport can read. Deliberately excludes the five telnet-only ones -- stream, input
#: power, key lock, LCD timeout and system address have no CGI status endpoint at all.
HTTP_READABLE: Final[frozenset[str]] = frozenset(
    {
        "model",
        "firmware",
        "mac",
        "output_name",
        "input_name",
        "video_route",
        "audio_route",
        "extracted_audio",
        "audio_delay",
        "bind_mode",
        "scaler",
        "image_enhancement",
        "test_pattern",
        "signal",
        "edid",
    }
)

#: Everything readable is also writable except the two that are observations rather than settings.
HTTP_WRITABLE: Final[frozenset[str]] = HTTP_READABLE - {"model", "firmware", "mac", "signal"}


def _keyed(kind: str, index: int, value: Any) -> tuple[str, Any]:
    return f"{kind}_{index}", value


def decode(endpoint: StatusEndpoint, parsed: ParsedStatus, *, port_count: int) -> DeviceReport:
    """One parsed status body to a partial report.

    Returns an empty report for anything unusable -- an absent endpoint, a ``NO SUPPORT``, a
    malformed body -- so a single odd endpoint cannot fail an update or blank a value.
    """
    if not parsed.ok:
        return EMPTY

    match endpoint:
        case StatusEndpoint.VIDEO:
            return _decode_video(parsed)
        case StatusEndpoint.WEB:
            return _decode_web(parsed, port_count)
        case StatusEndpoint.AUDIO:
            return _decode_audio(parsed)
        case StatusEndpoint.SYSTEM:
            return _decode_system(parsed)
        case StatusEndpoint.INFO:
            return _decode_info(parsed, port_count)
        case StatusEndpoint.EDID:
            return _decode_edid(parsed, port_count)
        case StatusEndpoint.NETWORK:
            return _decode_network(parsed)
        case _:
            return EMPTY


def _decode_video(parsed: ParsedStatus) -> DeviceReport:
    """``VidSta`` -- and the body that establishes how many ports this unit has."""
    values = {}
    for token in parsed.fields:
        if pair := p.parse_video_route(token):
            values.update([_keyed("video_route", *pair)])
    return DeviceReport.update(values)


def _decode_web(parsed: ParsedStatus, port_count: int) -> DeviceReport:
    """``WebSta`` -- model, firmware and all port names.

    Arity-guarded, because port names are free text and an embedded ``&`` shifts every field after
    it. A shifted response is rejected outright rather than applied one position out, since there
    is no way to tell which field was split.
    """
    guarded = p.expect_fields(parsed, _WEB_HEADER_FIELDS + 2 * port_count)
    if not guarded.ok:
        return EMPTY

    fields = guarded.fields
    names = fields[_WEB_HEADER_FIELDS:]
    values: dict[str, Any] = {"model": fields[0] or None, "firmware": fields[1] or None}
    for index, name in enumerate(names[:port_count], start=1):
        values[f"output_name_{index}"] = name or None
    for index, name in enumerate(names[port_count:], start=1):
        values[f"input_name_{index}"] = name or None
    return DeviceReport.update(values)


def _decode_audio(parsed: ParsedStatus) -> DeviceReport:
    """``AudSta`` -- four token groups, classified by pattern rather than read by position."""
    values: dict[str, Any] = {}
    for token in parsed.fields:
        if pair := p.parse_audio_route(token):
            values.update([_keyed("audio_route", *pair)])
        elif flag := p.parse_extracted_audio(token):
            values.update([_keyed("extracted_audio", *flag)])
        elif (delay := p.parse_audio_delay(token)) and (
            # An unknown code is dropped rather than mapped to a neighbouring value.
            (member := AUDIO_DELAY_BY_CODE.get(delay[1])) is not None
        ):
            values.update([_keyed("audio_delay", delay[0], member)])
        elif ((code := p.parse_bind_mode(token)) is not None) and (
            (mode := BIND_MODE_BY_CODE.get(code)) is not None
        ):
            values["bind_mode"] = mode
    return DeviceReport.update(values)


def _decode_system(parsed: ParsedStatus) -> DeviceReport:
    """``SysSta`` -- scaler, image enhancement and the built-in test pattern."""
    values: dict[str, Any] = {}
    for token in parsed.fields:
        # An unknown enum code is dropped rather than mapped to a neighbouring value: a firmware
        # with a new scaler mode must read as "not reported", never as "auto".
        if (scaler := p.parse_scaler_mode(token)) and (
            (mode := SCALER_MODE_BY_CODE.get(scaler[1])) is not None
        ):
            values.update([_keyed("scaler", scaler[0], mode)])
        elif (enhancement := p.parse_image_enhancement(token)) and (
            (level := IMAGE_ENHANCEMENT_BY_CODE.get(enhancement[1])) is not None
        ):
            values.update([_keyed("image_enhancement", enhancement[0], level)])
        elif pattern := p.parse_test_pattern(token):
            values.update([_keyed("test_pattern", *pattern)])
    return DeviceReport.update(values)


def _decode_info(parsed: ParsedStatus, port_count: int) -> DeviceReport:
    """``INFSta`` -- detected signal per port, as free text.

    Deliberately not parsed into an enumeration: any set of values written today is a bug on
    tomorrow's firmware, and this device's vocabulary is not documented anywhere.
    """
    values: dict[str, Any] = {}
    for index in range(1, port_count + 1):
        raw = parsed.fields[index - 1] if index <= len(parsed.fields) else ""
        values[f"signal_{index}"] = raw or None
    return DeviceReport.update(values)


def _decode_edid(parsed: ParsedStatus, port_count: int) -> DeviceReport:
    """``EdidSta`` -- per-input EDID token, decoded to the option key telnet also produces."""
    values: dict[str, Any] = {}
    for index in range(1, port_count + 1):
        raw = parsed.fields[index - 1] if index <= len(parsed.fields) else ""
        values[f"edid_{index}"] = decode_edid(raw) if raw else None
    return DeviceReport.update(values)


def _decode_network(parsed: ParsedStatus) -> DeviceReport:
    """``NetSta`` -- the MAC, and nothing else.

    The body also carries the IP, netmask, gateway and a second copy of every port name. None of
    it is kept: the MAC is the one field with a use, and the rest is site data that would only
    create another way for it to leak.
    """
    if not parsed.fields:
        return EMPTY
    mac = parsed.fields[0].strip().replace(".", ":").replace("-", ":").lower()
    return DeviceReport.update({"mac": mac or None})
