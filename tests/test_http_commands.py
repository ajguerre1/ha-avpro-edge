"""Canonical state key to CGI button code, for every kind the web interface can set.

Pure: no socket, no device, no clock. `_command_for` is a lookup, which is exactly why it drifted
out of coverage -- it moved from the coordinator into the HTTP transport, telnet became primary,
and the whole mapping stopped being reached by anything except the fallback path. Twenty-three
statements that only run when the control socket is unavailable, and nothing executed them.

That is the same shape as every other defect found in this repository lately: not code that looks
wrong, code that nothing looks at. The mapping matters most in the situation nobody tests, because
falling back to HTTP is what happens when the matrix is already having a bad day.

The expected button codes are written out literally rather than built from the same helpers the
implementation uses. Deriving them would make this a test of nothing: it would agree with any
mapping, including a wrong one.
"""

from __future__ import annotations

import pytest
from avpro.http_transport import HttpTransport, UnsupportedCommand
from avpro.models import AudioDelay, BindMode, ImageEnhancement, ScalerMode
from avpro.protocol import CommandEndpoint


@pytest.fixture
def transport() -> HttpTransport:
    """A transport with no client behind it.

    `_command_for` never touches one -- it turns a key and a value into an endpoint and a string.
    Passing `None` is what makes that structural rather than merely true today: a future version
    that reached for the network here would fail loudly in this test.
    """
    return HttpTransport(None)  # type: ignore[arg-type]


#: key, value -> endpoint, button. One row per branch of the match statement.
CASES = [
    ("video_route_2", 3, CommandEndpoint.VIDEO, "O2I3"),
    ("audio_route_4", 1, CommandEndpoint.AUDIO, "AO4I1"),
    ("extracted_audio_1", True, CommandEndpoint.AUDIO, "O1AON"),
    ("extracted_audio_3", False, CommandEndpoint.AUDIO, "O3AOFF"),
    ("audio_delay_2", AudioDelay.BYPASS, CommandEndpoint.AUDIO, "O2D0"),
    ("audio_delay_2", AudioDelay.MS_630, CommandEndpoint.AUDIO, "O2D7"),
    ("scaler_1", ScalerMode.AUTO, CommandEndpoint.SYSTEM, "O1V0"),
    ("scaler_4", ScalerMode.BYPASS, CommandEndpoint.SYSTEM, "O4V1"),
    ("image_enhancement_3", ImageEnhancement.OFF, CommandEndpoint.SYSTEM, "O3E0"),
    ("image_enhancement_3", ImageEnhancement.STRONG, CommandEndpoint.SYSTEM, "O3E3"),
    ("test_pattern_2", True, CommandEndpoint.SYSTEM, "O2SGMON"),
    ("test_pattern_2", False, CommandEndpoint.SYSTEM, "O2SGMOFF"),
]


@pytest.mark.parametrize(("key", "value", "endpoint", "button"), CASES)
def test_each_kind_maps_to_its_endpoint_and_code(
    transport: HttpTransport, key: str, value: object, endpoint: CommandEndpoint, button: str
) -> None:
    assert transport._command_for(key, value) == (endpoint, button)


def test_the_case_table_covers_every_branch_of_the_mapping(transport: HttpTransport) -> None:
    """Guards the table above: adding a branch without a row here would go unnoticed.

    Read off the implementation's own match statement rather than restated, so a new `case` that
    nobody exercises fails this instead of quietly shipping untested.
    """
    import inspect
    import re

    source = inspect.getsource(HttpTransport._command_for)
    branches = set(re.findall(r'case "([a-z_]+)"', source))
    covered = {key.rsplit("_", 1)[0] for key, *_ in CASES} | {"edid"}
    assert branches <= covered, f"unmapped branches: {sorted(branches - covered)}"


def test_edid_is_selected_by_option_key(transport: HttpTransport) -> None:
    """EDID is the one kind whose value is an option key rather than an enum member.

    The index lands on the **input**, not the output, which is the opposite of every other row in
    the table: an EDID describes what a source is told the display can accept.
    """
    assert transport._command_for("edid_3", "user_1") == (CommandEndpoint.EDID, "EDIDU1IN3")


def test_bind_mode_is_the_one_setting_with_no_port(transport: HttpTransport) -> None:
    """Device-wide, so it is answered before the index check rather than by it."""
    assert transport._command_for("bind_mode", BindMode.MATRIX) == (
        CommandEndpoint.AUDIO,
        "AMB2",
    )


def test_a_key_needing_an_index_and_lacking_one_is_refused(transport: HttpTransport) -> None:
    """Not defaulted to port 1. Guessing which output to change is the worst available answer."""
    with pytest.raises(UnsupportedCommand, match="no HTTP command is defined"):
        transport._command_for("video_route", 1)


@pytest.mark.parametrize("kind", ["stream", "input_power", "key_lock", "lcd_timeout"])
def test_the_telnet_only_kinds_are_refused_rather_than_approximated(
    transport: HttpTransport, kind: str
) -> None:
    """The CGI interface has no endpoint for any of these.

    Refusing is the whole point: these four are why telnet is primary, and an HTTP transport that
    silently accepted them would report success for a command that never reached the matrix.
    """
    with pytest.raises(UnsupportedCommand, match="cannot set"):
        transport._command_for(f"{kind}_1", True)


def test_an_unknown_kind_is_refused_too(transport: HttpTransport) -> None:
    with pytest.raises(UnsupportedCommand):
        transport._command_for("something_invented_1", 1)


def test_http_holds_nothing_so_it_is_always_connected(transport: HttpTransport) -> None:
    """`connected` exists on this transport at all because it once did not.

    Its absence raised `AttributeError` on the HTTP fallback path -- the path that only runs when
    something has already gone wrong -- and survived seven commits because the run that revealed
    it was read as one failure with one cause.
    """
    assert transport.connected is True


def test_the_port_count_is_told_to_it_rather_than_assumed(transport: HttpTransport) -> None:
    """An MX88 has eight, and nothing here may hardcode four."""
    transport.set_port_count(8)
    assert transport._command_for("video_route_8", 8) == (CommandEndpoint.VIDEO, "O8I8")
