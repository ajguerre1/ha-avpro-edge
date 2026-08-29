"""Ask a live matrix whether its control port can report signal detection. **Read-only.**

Telnet is the primary transport but cannot currently see signal, which is what
``media_player.state`` and the eight signal entities are built on. The telnet grammar was
reverse-engineered from an observed ``GET STA`` dump, and a dump is not a command reference: a
per-input signal query could exist and simply never have been tried.

This finds out, without writing anything.

**Why it cannot write.** Every command is checked against :data:`SAFE` before it reaches the
socket, and anything that is not a ``GET``/``HELP``/``?`` raises rather than being sent. That is a
guard in the send path rather than a convention about which strings appear in the list below --
enforced by ``tests/test_transport_discipline.py``.

**Site data.** A real matrix answers with configured port names, the unit's MAC and its address.
Output is redacted before printing and written under ``local/``, which is gitignored.

Usage::

    python scripts/probe_telnet_signal.py 10.0.0.1
    python scripts/probe_telnet_signal.py 10.0.0.1 --port 23
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import sys
from pathlib import Path

#: The only shapes that may reach the wire. Anything else raises.
SAFE = re.compile(r"^\s*(GET\b|HELP\b|\?)", re.IGNORECASE)

#: Candidate spellings for a signal query, plus the two that establish a baseline.
#:
#: The device answers an unrecognised command in some consistent way -- silence, an error line, or
#: an echo. ``GET STA`` and a deliberately absurd command bracket that behaviour, so a real answer
#: is distinguishable from the device's way of saying no.
CANDIDATES: tuple[str, ...] = (
    "HELP",
    "?",
    "GET STA",
    "GET NONSENSE XYZZY",  # the control: whatever this does is what "unknown" looks like
    "GET IN1 SIG",
    "GET IN1 SIGNAL",
    "GET IN1 STA",
    "GET IN1 HDMI",
    "GET IN1 RES",
    "GET IN1 TMDS",
    "GET OUT1 SIG",
    "GET OUT1 RES",
    "GET OUT1 STA",
    "GET SIG",
    "GET SIGNAL",
    "GET INPUT1 SIG",
)

CONNECT_TIMEOUT = 10.0
#: How long to wait for a reply before deciding the device said nothing.
QUIET = 1.5


class UnsafeCommand(RuntimeError):
    """A command that is not a read was about to be sent."""


def redact(text: str) -> str:
    """Remove the things a public repository must never see.

    MAC addresses in either notation, dotted-quad addresses, and the port-name payloads. Port
    names arrive as free text, so the safe move is to drop the whole line rather than try to
    recognise a room name.
    """
    text = re.sub(r"\b(?:[0-9a-f]{2}[:.-]){5}[0-9a-f]{2}\b", "<mac>", text, flags=re.I)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", text)
    lines = []
    for line in text.splitlines():
        if re.search(r"\b(NAME|WEBSTA|HOSTNAME)\b", line, re.I):
            lines.append("<name line redacted>")
        else:
            lines.append(line)
    return "\n".join(lines)


async def ask(writer, reader, command: str) -> str:
    """Send one read command and gather whatever comes back before the wire goes quiet."""
    if not SAFE.match(command):
        raise UnsafeCommand(f"refusing to send {command!r}: this probe is read-only")

    writer.write(command.encode("ascii") + b"\r\n")
    await writer.drain()

    chunks: list[str] = []
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=QUIET)
        except TimeoutError:
            break
        if not line:
            break
        chunks.append(line.decode("ascii", errors="replace"))
    return "".join(chunks)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=23)
    parser.add_argument("--out", default="local/telnet-signal-probe.txt")
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="An extra command to try. Still checked against SAFE, so it cannot be a write.",
    )
    parser.add_argument(
        "--only",
        action="store_true",
        help="Try only --command entries, plus the two controls that make an answer readable.",
    )
    args = parser.parse_args()

    commands: tuple[str, ...]
    if args.only:
        # Without the controls a reply is uninterpretable: "CMD ERR" only means "unknown" once
        # you have seen what a known-good command and a nonsense one each look like.
        commands = ("GET STA", "GET NONSENSE XYZZY", *args.command)
    else:
        commands = (*CANDIDATES, *args.command)

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(args.host, args.port), timeout=CONNECT_TIMEOUT
        )
    except TimeoutError:
        print(f"{args.host}:{args.port} accepted nothing within {CONNECT_TIMEOUT}s.")
        print("The single control slot is most likely held by another controller.")
        return 2
    except OSError as err:
        print(f"{args.host}:{args.port}: {err}")
        return 2

    report: list[str] = []
    try:
        for command in commands:
            answer = await ask(writer, reader, command)
            block = redact(answer).strip()
            report.append(f"--- {command} ---\n{block or '(no reply)'}\n")
            print(f"{command:24} -> {len(answer):5d} bytes, {len(block.splitlines()):3d} lines")
    finally:
        writer.close()
        # Hand the socket back promptly; something else on the network may want it.
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"\nRedacted transcript written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
