"""Settle the LCD timeout and key lock vocabularies on real hardware. **Writes, and restores.**

Two questions the fake cannot answer, because the fake only knows what I told it.

**Which ``T`` values does ``SET LCD ON T{n}`` accept?** The Control4 driver lists four options --
Always ON, 15sec, 30sec, 60sec -- so ``T0``-``T3`` is a reasonable inference from its LIST order.
An inference is not a measurement, and these become user-facing labels in a select. If ``T4`` is
accepted the list is wrong; if ``T3`` is rejected it is also wrong.

**Does key lock round-trip?** A boolean, but the one control that can make the front panel stop
responding, so it is worth knowing it comes back.

**Blast radius.** The LCD timeout changes how long the matrix's own front-panel backlight stays
lit. It moves no video, blanks no display, and changes nothing any room can see. Key lock disables
the front-panel buttons, which is why this restores it in a ``finally`` and verifies the restore
before exiting.

Every original value is read first and written back last. If the restore itself fails, the script
says so loudly rather than exiting quietly, because a matrix left with its buttons locked is a
thing somebody discovers at the worst possible moment.

Usage::

    python scripts/probe_lcd_keylock.py 10.0.0.1
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import sys

CONNECT_TIMEOUT = 10.0
QUIET = 1.5

#: How long to let the device settle before reading back. Route changes measured 25-404 ms, and
#: these are cheaper operations; a second is generous.
SETTLE = 1.0

#: The only commands this script may send. Anything touching routing, streams or the network is
#: not merely undesirable here, it is out of scope for the whole integration.
ALLOWED = re.compile(r"^\s*(GET STA|SET LCD ON T\d+|SET KEY LOCK (ON|OFF))\s*$", re.IGNORECASE)


class UnsafeCommand(RuntimeError):
    """A command outside this probe's remit was about to be sent."""


class Session:
    """One telnet session, with the read-back-and-restore discipline this needs."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def send(self, command: str) -> str:
        if not ALLOWED.match(command):
            raise UnsafeCommand(f"refusing to send {command!r}")
        self._writer.write(command.encode("ascii") + b"\r\n")
        await self._writer.drain()

        chunks: list[str] = []
        while True:
            try:
                line = await asyncio.wait_for(self._reader.readline(), timeout=QUIET)
            except TimeoutError:
                break
            if not line:
                break
            chunks.append(line.decode("ascii", errors="replace"))
        return "".join(chunks)

    async def read_state(self) -> dict[str, str]:
        """The two values this probe touches, from a full census.

        Read from ``GET STA`` rather than a targeted query because the targeted forms were never
        confirmed to exist, and a census is the one command known to answer.
        """
        dump = await self.send("GET STA")
        state = {}
        for line in dump.splitlines():
            if match := re.fullmatch(r"LCD ON T(\d+)", line.strip(), re.I):
                state["lcd"] = match[1]
            elif match := re.fullmatch(r"KEY LOCK (ON|OFF)", line.strip(), re.I):
                state["key_lock"] = match[1].upper()
        return state


async def probe_lcd(session: Session, original: str) -> None:
    print(f"\n=== SET LCD ON T{{n}} — original is T{original} ===")
    accepted, rejected = [], []

    # 0-5 rather than 0-3: the point is to find the boundary, which means asking past where the
    # Control4 driver's four options say it should be.
    for value in range(6):
        await session.send(f"SET LCD ON T{value}")
        await asyncio.sleep(SETTLE)
        read_back = (await session.read_state()).get("lcd")
        if read_back == str(value):
            accepted.append(value)
            print(f"  T{value}  accepted, reads back T{read_back}")
        else:
            rejected.append(value)
            print(f"  T{value}  REJECTED, still reads T{read_back}")

    print(f"\n  accepted: {accepted}")
    print(f"  rejected: {rejected}")
    expected = [0, 1, 2, 3]
    if accepted == expected:
        print("  -> matches the Control4 driver's four options (Always ON / 15s / 30s / 60s)")
    else:
        print(f"  -> DOES NOT match the inferred {expected}. The select's options are wrong.")


async def probe_key_lock(session: Session, original: str) -> None:
    print(f"\n=== SET KEY LOCK — original is {original} ===")
    for value in ("ON", "OFF"):
        await session.send(f"SET KEY LOCK {value}")
        await asyncio.sleep(SETTLE)
        read_back = (await session.read_state()).get("key_lock")
        verdict = "round-trips" if read_back == value else f"MISMATCH, reads {read_back}"
        print(f"  {value:3}  {verdict}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=23)
    args = parser.parse_args()

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(args.host, args.port), timeout=CONNECT_TIMEOUT
        )
    except (TimeoutError, OSError) as err:
        print(f"{args.host}:{args.port}: {err}")
        return 2

    session = Session(reader, writer)
    before = await session.read_state()
    if "lcd" not in before or "key_lock" not in before:
        print(f"could not read the starting state ({before}); refusing to change anything")
        writer.close()
        return 2

    print(f"before: LCD T{before['lcd']}, KEY LOCK {before['key_lock']}")

    try:
        await probe_lcd(session, before["lcd"])
        await probe_key_lock(session, before["key_lock"])
    finally:
        print("\n=== restoring ===")
        await session.send(f"SET LCD ON T{before['lcd']}")
        await session.send(f"SET KEY LOCK {before['key_lock']}")
        await asyncio.sleep(SETTLE)
        after = await session.read_state()

        ok = after.get("lcd") == before["lcd"] and after.get("key_lock") == before["key_lock"]
        print(f"after:  LCD T{after.get('lcd')}, KEY LOCK {after.get('key_lock')}")
        if ok:
            print("restored exactly.")
        else:
            # Loud on purpose. A matrix left with its front panel locked is discovered by a person
            # standing in front of it, at the worst possible moment.
            print("*** RESTORE FAILED -- the matrix is NOT as it was found. ***")
            print(f"*** expected LCD T{before['lcd']}, KEY LOCK {before['key_lock']} ***")

        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
