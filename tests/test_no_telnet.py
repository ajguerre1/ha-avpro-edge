"""Static guarantee that nothing in this project ever opens a telnet session.

This is not a style rule. The matrix's telnet server accepts exactly one client at a time --
four simultaneous connection attempts produced one success and three timeouts -- and in a real
installation that single slot is held, persistently, by the control system the house runs on.
An integration that connected would take it away, and the failure would show up as somebody's
keypad no longer switching their television.

The CGI interface over HTTP has no such limit, so there is never a reason to reach for a socket
here. The integration talks to the device exclusively through aiohttp.

The companion runtime check is the tripwire in ``tools/fake_avpro.py``, which listens on a
stand-in port and records any connection. This test is the stronger of the two: it fails before
anything runs.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components"
TOOLS = ROOT / "tools"
SCRIPTS = ROOT / "scripts"

#: Never acceptable anywhere: the stdlib telnet client, and any low-level outbound connect.
FORBIDDEN_EVERYWHERE = (
    r"\btelnetlib\b",
    r"\bopen_connection\s*\(",
    r"\bcreate_connection\s*\(",
)

#: Additionally forbidden inside the integration itself, which needs no raw sockets at all.
FORBIDDEN_IN_COMPONENT = (
    r"\bsocket\.socket\s*\(",
    r"\basyncio\.start_server\s*\(",
    r"\bsocket\.create_server\s*\(",
)


def _python_files(*roots: Path) -> list[Path]:
    return sorted(path for root in roots if root.exists() for path in root.rglob("*.py"))


def _offending_lines(path: Path, patterns: tuple[str, ...]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern in patterns:
            if re.search(pattern, line):
                hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    return hits


def test_no_module_anywhere_opens_an_outbound_socket() -> None:
    offenders = [
        hit
        for path in _python_files(COMPONENT, TOOLS, SCRIPTS)
        for hit in _offending_lines(path, FORBIDDEN_EVERYWHERE)
    ]
    assert not offenders, "outbound socket API found:\n" + "\n".join(offenders)


def test_the_integration_uses_no_raw_sockets_at_all() -> None:
    """Only the fake device may listen, and it lives under tools/."""
    offenders = [
        hit
        for path in _python_files(COMPONENT)
        for hit in _offending_lines(path, FORBIDDEN_IN_COMPONENT)
    ]
    assert not offenders, "raw socket API inside the integration:\n" + "\n".join(offenders)


def _code_only(path: Path) -> str:
    """The file with comments and string literals removed.

    Necessary because this project *documents* why it does not use telnet, in prose that
    naturally contains the words a naive scan is looking for. Scanning raw text would flag the
    explanation of the rule as a violation of it. Tokenising means only executable code is
    examined.
    """
    source = path.read_text(encoding="utf-8")
    kept: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    except tokenize.TokenError:  # pragma: no cover - only on unparseable source
        return source
    return " ".join(kept)


def test_port_23_is_never_a_connection_target() -> None:
    """The number may appear as data -- the device reports its own control port -- but never as
    somewhere this code connects to."""
    pattern = re.compile(r"(connect|open_connection|create_connection)[^\n]{0,40}\b23\b")
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files(COMPONENT, TOOLS, SCRIPTS)
        if pattern.search(_code_only(path))
    ]
    assert not offenders, "port 23 used as a connection target:\n" + "\n".join(offenders)


def test_the_scanner_ignores_prose_but_still_sees_code() -> None:
    """A scanner that strips strings could strip everything and pass vacuously."""
    client = COMPONENT / "ha_avpro_edge" / "avpro" / "client.py"
    code = _code_only(client)
    assert "telnet" not in code.lower()  # the prose is gone
    assert "async def" in code  # the code is not


def test_the_client_explains_why_telnet_is_off_limits() -> None:
    """A future contributor will wonder why a push transport was not used. The answer has to be
    where they will look, not only in a commit message."""
    client = (COMPONENT / "ha_avpro_edge" / "avpro" / "client.py").read_text(encoding="utf-8")
    assert "telnet" in client.lower()
    assert "one client at a time" in client


@pytest.mark.parametrize("root", [COMPONENT, TOOLS])
def test_the_scan_actually_found_files(root: Path) -> None:
    """A guard that silently scans nothing passes forever."""
    assert _python_files(root), f"no Python files under {root}"
