"""Transport discipline (T-X1..T-X4).

Replaces the blanket "never speak telnet" guard, which was only ever a proxy for the real rule.

**Telnet is primary. Always speak telnet unless you don't need to.**

So there are three things to assert, and the middle one is new:

* nothing connects to port 23 when the user has said ``http`` -- the escape hatch still holds
  absolutely;
* while telnet is connected, **no HTTP request is issued for anything telnet supports** -- no
  hedging, no dual-polling, one source of truth;
* HTTP remains reachable for the one thing telnet genuinely cannot do, renaming ports.

The failure this now guards against is the opposite of the old one. Under the old rule the danger
was taking a socket someone else needed; under this rule it is running both wires at once, which
doubles device load and creates two sources of truth to reconcile.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components"
TOOLS = ROOT / "tools"
SCRIPTS = ROOT / "scripts"

#: The one module allowed to open a socket, and the one allowed to name the port as a target.
TELNET_CLIENT = COMPONENT / "ha_avpro_edge" / "avpro" / "telnet_client.py"

#: Still never acceptable anywhere. The stdlib telnet client is deprecated and does its own
#: option negotiation, which this device does not speak -- it is a raw line protocol.
FORBIDDEN_EVERYWHERE = (r"\btelnetlib\b",)

#: Socket APIs. Permitted only in the telnet client and the fake device.
SOCKET_APIS = (
    r"\bopen_connection\s*\(",
    r"\bcreate_connection\s*\(",
    r"\bsocket\.socket\s*\(",
)


def _python_files(*roots: Path) -> list[Path]:
    return sorted(path for root in roots if root.exists() for path in root.rglob("*.py"))


def _code_only(path: Path) -> str:
    """The file with comments and string literals stripped.

    This project documents its transport rules in prose that naturally contains the words a naive
    scan looks for, so scanning raw text would flag the explanation of a rule as a violation of it.
    """
    source = path.read_text(encoding="utf-8")
    kept: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    except tokenize.TokenError:  # pragma: no cover - unparseable source
        return source
    return " ".join(kept)


# ---------------------------------------------------------------------------------------------
# T-X4 -- who may hold a socket
# ---------------------------------------------------------------------------------------------


def test_the_deprecated_stdlib_telnet_client_is_never_used() -> None:
    """This device speaks a raw line protocol with no option negotiation."""
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files(COMPONENT, TOOLS, SCRIPTS)
        for pattern in FORBIDDEN_EVERYWHERE
        if re.search(pattern, _code_only(path))
    ]
    assert not offenders, "telnetlib found in:\n" + "\n".join(offenders)


def test_only_the_telnet_client_opens_a_socket_inside_the_integration() -> None:
    """Every other module reaches the device through a Transport, not a wire."""
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files(COMPONENT)
        if path != TELNET_CLIENT
        for pattern in SOCKET_APIS
        if re.search(pattern, _code_only(path))
    ]
    assert not offenders, "socket API outside the telnet client:\n" + "\n".join(offenders)


def test_port_23_is_named_only_by_the_telnet_client() -> None:
    """A bare 23 elsewhere is a magic number pointed at the house's control socket."""
    pattern = re.compile(r"(connect|open_connection|create_connection)[^\n]{0,40}\b23\b")
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files(COMPONENT)
        if path != TELNET_CLIENT and pattern.search(_code_only(path))
    ]
    assert not offenders, "port 23 targeted outside the telnet client:\n" + "\n".join(offenders)


def test_the_telnet_client_declares_the_port_once_as_a_named_constant() -> None:
    code = _code_only(TELNET_CLIENT)
    assert "TELNET_PORT" in code
    assert code.count(" 23 ") <= 2  # the constant's definition, and its use as a default


# ---------------------------------------------------------------------------------------------
# The rule is documented where a contributor will look
# ---------------------------------------------------------------------------------------------


def test_the_telnet_client_explains_why_it_may_hold_the_socket() -> None:
    """The next person will reasonably wonder why this connects at all, and why it stays."""
    prose = TELNET_CLIENT.read_text(encoding="utf-8").lower()
    assert "one client at a time" in prose
    assert "push" in prose


def test_the_scan_actually_found_files() -> None:
    """A guard that silently scans nothing passes forever."""
    assert len(_python_files(COMPONENT)) > 10
    assert TELNET_CLIENT.exists()


# ---------------------------------------------------------------------------------------------
# T-X1 / T-X2 / T-X3 -- the behavioural half of the rule
# ---------------------------------------------------------------------------------------------
#
# Implemented in tests/ha/test_transport_selection.py, because they need a config entry and a
# selected transport. They were declared here as strict xfail while the selector was being built,
# so it could not land without them; that debt is now paid and the placeholders are gone.
#
#   T-X1  nothing connects to the control socket under the http setting
#   T-X2  no HTTP request is issued for anything telnet supports, while telnet is connected
#   T-X3  a port rename still reaches HTTP -- the one thing telnet cannot do


def test_the_behavioural_assertions_exist_somewhere() -> None:
    """Guards against the pair above being quietly deleted rather than implemented."""
    ha_suite = (ROOT / "tests" / "ha" / "test_transport_selection.py").read_text(encoding="utf-8")
    assert "nothing_connects_to_the_control_socket_under_the_http_setting" in ha_suite
    assert "no_http_request_is_issued_for_anything_telnet_supports" in ha_suite


# ---------------------------------------------------------------------------------------------
# T-T1 -- both wires satisfy the contract
# ---------------------------------------------------------------------------------------------
#
# Specified in the testing doc when the seam was designed and then not written, which is how
# `connected` came to be used by a test while being absent from both the protocol and the HTTP
# transport. It passed only because that test happened to be handed a telnet transport; under
# fallback it would have raised AttributeError at runtime.


def test_both_transports_satisfy_the_contract() -> None:
    """A method missing from one wire is a crash the moment fallback happens."""
    import sys

    sys.path.insert(0, str(COMPONENT / "ha_avpro_edge"))
    from avpro.http_transport import HttpTransport
    from avpro.telnet_client import TelnetTransport
    from avpro.transport import Transport

    required = [name for name in dir(Transport) if not name.startswith("_")]
    assert required, "the protocol declares nothing; this check would be vacuous"

    for cls in (HttpTransport, TelnetTransport):
        missing = [name for name in required if not hasattr(cls, name)]
        assert not missing, f"{cls.__name__} is missing {missing}"


def test_the_contract_covers_what_callers_actually_use() -> None:
    """Anything reached through `transport.` in the integration or its tests has to be declared.

    This is the check that would have caught `connected`.
    """
    import sys

    sys.path.insert(0, str(COMPONENT / "ha_avpro_edge"))
    from avpro.transport import Transport

    declared = {name for name in dir(Transport) if not name.startswith("_")}
    # Attributes only the HTTP transport offers, used by diagnostics behind an isinstance check.
    exempt = {"device_capabilities", "tick", "set_port_count", "async_route_all", "allow_writes"}

    used = set()
    for path in [*_python_files(COMPONENT), *(ROOT / "tests").rglob("test_*.py")]:
        used |= set(re.findall(r"transport\.([a-z_][a-z0-9_]*)", _code_only(path)))

    undeclared = used - declared - exempt
    assert not undeclared, (
        f"reached through a Transport but not in the contract: {sorted(undeclared)}"
    )
