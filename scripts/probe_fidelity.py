"""Does the fake device actually resemble the matrix? **Read-only.**

Every test in this repository runs against ``tools/fake_avpro.py``. The fake encodes *my model* of
the hardware, so a green suite proves the integration agrees with that model -- not with the
device. Those are different claims, and the difference has already cost something: 455 tests passed
while the integration was blind on its default transport, because the fake reproduced the same
wrong assumption about signal detection that the code did.

This compares the two directly. The directions are **not** symmetric:

* **device only** is a fault. The matrix produces something the fake cannot, so no test can reach
  it. This is the direction that hides bugs, and it is what sets the exit code.
* **fake only** is usually deliberate. The fake models an input as unplugged, which the real matrix
  does not happen to be doing; that extra coverage is the point of having a fake. Reported for the
  reader, not counted against it.
* **endpoint presence** is the exception, checked in both directions. A fake serving a tab the
  device lacks means every default test runs against hardware that does not exist -- which is
  exactly what this found for ``TMDSDivSta.CGI``.

**Comparison is on shape, not value.** ``OUT1 VS IN3`` and ``OUT2 VS IN1`` are the same fact about
the protocol, so digits, addresses and hex are normalised away before anything is compared. That is
both the right comparison and the reason nothing sensitive reaches a terminal or a file.

Port names get a stronger rule than normalisation, **by position rather than by pattern**. The
first attempt keyed on case -- protocol tokens are upper case, room names are not -- and it leaked:
an all-caps output name passed through untouched, and one ending in a digit came out with the
digit replaced. The protocol is
positional and documented, so which fields hold names is knowable rather than guessable, and those
fields are replaced whatever they contain. Guessing is the wrong mode for a rule that only has to
fail once, against a repository that is public.

Usage::

    python scripts/probe_fidelity.py 10.0.0.1
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from fake_avpro import FakeMatrix

#: Only reads may reach the wire. Same guard as the signal probe.
SAFE = re.compile(r"^\s*(GET\b|HELP\b|\?)", re.IGNORECASE)

STATUS_ENDPOINTS = (
    "WEBDivSta.CGI",
    "VIDDivSta.CGI",
    "AUDDivSta.CGI",
    "SYSDivSta.CGI",
    "INFDivSta.CGI",
    "EDIDDivSta.CGI",
    "NETDivSta.CGI",
    "TMDSDivSta.CGI",
)

CONNECT_TIMEOUT = 10.0
QUIET = 2.0


class UnsafeCommand(RuntimeError):
    """A command that is not a read was about to be sent."""


#: A token that is safe to print once digits have been normalised away.
#:
#: Every value in this protocol's vocabulary is upper case -- ``OUT``, ``VS``, ``IN``, ``STREAM``,
#: ``ON``, ``OFF``, ``EXA``, ``DIS``, ``SGM``, ``TMDS``, ``EDID``, ``STATICIP``, ``AC-MX#-AUHD``,
#: ``V#.#``. Configured port names are not: ``Kitchen``, ``AppleTV``, ``Den``. So a lower-case
#: letter surviving normalisation is the signal that a token is free text rather than grammar.
#:
#: This is an allowlist rather than a denylist on purpose. A denylist has to anticipate what a room
#: might be called; this only has to know what the protocol looks like, which is fixed and small.
SAFE_TOKEN = re.compile(r"^[A-Z0-9#<>_.\-]+$")


def scrub(token: str) -> str:
    """Replace anything that is not recognisable protocol grammar."""
    return token if SAFE_TOKEN.match(token) else "<text>"


def shape(text: str) -> str:
    """Reduce a line to its grammar, discarding every value.

    ``OUT1 VS IN3`` and ``OUT2 VS IN1`` both become ``OUT# VS IN#``: the same fact about the
    protocol, which is what is being compared.

    Order matters. Hex runs go before words so a MAC is not mistaken for one, dotted quads before
    bare digits so an address does not decompose into four numbers, and :func:`scrub` last, once
    the tokens that legitimately contain lower case have been replaced by placeholders.
    """
    text = text.strip()
    text = re.sub(r"\b(?:[0-9a-f]{2}[:.-]){5}[0-9a-f]{2}\b", "<MAC>", text, flags=re.I)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>", text)
    text = re.sub(r"\d+", "#", text)
    return " ".join(scrub(token) for token in text.split())


#: Where the configured port names begin in each body that carries them, by field index.
#:
#: ``WebSta`` is ``<model>&<firmware>&<4 output names>&<4 input names>``; ``NetSta`` is
#: ``<mac>&<ip>&<mask>&<gateway>&<port>&STATICIP&<8 names>``. Everything from these indices on is
#: free text and is replaced unconditionally.
#:
#: Position, not pattern. :func:`scrub` alone is not enough here: it keys on case, and an output
#: in all caps would survive it untouched, and one ending in a digit would keep its letters. The
#: protocol is positional and documented, so which fields hold names is knowable rather than
#: guessable -- and guessing is the wrong mode for a rule that only has to fail once.
NAME_FIELDS_FROM = {"WEBSTA": 2, "NETSTA": 6}


def body_fields(body: str) -> tuple[str, int, set[str]]:
    """An HTTP status body split into its key, field count, and the set of field shapes.

    Compared per field rather than as one aggregate string. The aggregate form made deliberate
    variety look like a fault: the fake models one input as unplugged, so its set of field shapes
    is a superset of the device's, and comparing the two joined strings reported that as the
    matrix producing something the fake could not. It is the reverse.
    """
    key, separator, payload = body.partition("=")
    if not separator:
        return "<no key=>", 0, {shape(body[:40])}
    if not SAFE_TOKEN.match(key.upper()):
        key = "<odd key>"

    fields = payload.split("&")
    names_from = NAME_FIELDS_FROM.get(key.upper(), len(fields))
    shapes = {
        shape(field) if index < names_from else "<name>"
        for index, field in enumerate(fields)
        if field
    }
    return key, len(fields), shapes


async def ask_telnet(host: str, port: int, command: str) -> str:
    if not SAFE.match(command):
        raise UnsafeCommand(f"refusing to send {command!r}: this probe is read-only")

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT
    )
    try:
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
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


async def fetch(session: aiohttp.ClientSession, base: str, path: str) -> str:
    try:
        async with session.get(f"http://{base}/{path}", headers={"Connection": "close"}) as resp:
            return (await resp.read()).decode("ascii", errors="replace")
    except aiohttp.ClientError as err:
        return f"<unreachable: {type(err).__name__}>"


def report(title: str, device: set[str], fake: set[str]) -> bool:
    """Print both directions of divergence. Returns True only for the direction that is a fault.

    The two directions are **not** symmetric, and treating them as such made the first run noisy.

    * **device only** is always a fault: the matrix produces something the fake cannot, so no test
      can reach it. This is the direction that hides bugs.
    * **fake only** is usually deliberate. The fake models one input as unplugged, which the real
      matrix does not currently do -- and that extra coverage is the entire point of having a fake.
      Reported for the reader, not counted against it.

    Endpoint *presence* is the exception and is checked separately by the caller, because a fake
    serving a tab the device lacks means every default test runs against hardware that does not
    exist. That is what happened with TMDS.
    """
    only_device = sorted(device - fake)
    only_fake = sorted(fake - device)

    print(f"\n=== {title} ===")
    if not only_device and not only_fake:
        print("  identical")
        return False

    for item in only_device:
        print(f"  DEVICE ONLY  {item}   <- the fake cannot produce this")
    for item in only_fake:
        print(f"  fake only    {item}   (extra coverage, not a fault)")
    return bool(only_device)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--telnet-port", type=int, default=23)
    args = parser.parse_args()

    diverged = False

    async with FakeMatrix() as fake, aiohttp.ClientSession() as session:
        # -- telnet ----------------------------------------------------------------------
        try:
            real_dump = await ask_telnet(args.host, args.telnet_port, "GET STA")
        except (TimeoutError, OSError) as err:
            print(f"telnet unavailable: {err}")
            return 2

        fake_dump = await ask_telnet("127.0.0.1", fake.telnet_port, "GET STA")

        real_lines = {shape(line) for line in real_dump.splitlines() if line.strip()}
        fake_lines = {shape(line) for line in fake_dump.splitlines() if line.strip()}
        print(f"GET STA: device {len(real_lines)} distinct shapes, fake {len(fake_lines)}")
        diverged |= report("telnet GET STA line shapes", real_lines, fake_lines)

        # -- http ------------------------------------------------------------------------
        for path in STATUS_ENDPOINTS:
            real = await fetch(session, args.host, path)
            mock = await fetch(session, fake.host, path)

            real_missing = real.startswith("<unreachable") or "not found" in real.lower()
            fake_missing = mock.startswith("<unreachable") or "not found" in mock.lower()
            if real_missing or fake_missing:
                verdict = "both absent" if real_missing and fake_missing else "MISMATCH"
                print(f"\n=== {path} ===")
                print(f"  {verdict}: on device={not real_missing}, on fake={not fake_missing}")
                diverged |= verdict == "MISMATCH"
                continue

            real_key, real_count, real_shapes = body_fields(real)
            fake_key, fake_count, fake_shapes = body_fields(mock)

            if (real_key, real_count) != (fake_key, fake_count):
                # Field count is not variety, it is grammar. The arity guard is one of the few
                # defences against a port name containing '&', and a fake with the wrong count
                # would let that go untested.
                print(f"\n=== {path} ===")
                print(
                    f"  MISMATCH  device {real_key}=[{real_count}]  fake {fake_key}=[{fake_count}]"
                )
                diverged = True
                continue

            diverged |= report(
                f"{path}  ({real_key}, {real_count} fields)", real_shapes, fake_shapes
            )

    print(
        "\n" + ("Divergences found -- see above." if diverged else "The fake matches the device.")
    )
    return 1 if diverged else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
