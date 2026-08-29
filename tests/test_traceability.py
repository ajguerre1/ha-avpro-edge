"""Every scenario the testing doc declares is either implemented or explicitly deferred.

This exists because of a specific failure. **T-T1** -- "both clients satisfy ``Transport``" -- was
written into ``docs/ai/testing/`` when the transport seam was designed, and then never
implemented. Nothing noticed. The gap surfaced only when ``connected`` turned out to be missing
from ``HttpTransport``: a latent ``AttributeError`` on the fallback path, which is the path that
runs when something has *already* gone wrong.

The root cause was not the missing method. It was that the link between the testing doc and the
suite was **prose**. A scenario could be declared, agreed, and quietly never written, and every
signal available -- green CI, a passing suite, a growing test count -- would look exactly the same
as if it had been.

So the link is mechanical now. A declared ID must be one of:

* **named in a test file**, next to the test that implements it; or
* **listed in** :data:`DEFERRED` **with a reason**, which is visible in review and in a diff.

Silence is no longer an option. An unimplemented scenario either fails this test or appears as a
deliberate, reviewable line of code.

:data:`DEFERRED` is designed to shrink. Implementing a deferred scenario without removing its
entry fails :func:`test_the_deferral_list_does_not_rot`, so the list cannot quietly outlive the
work it describes.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTING_DOC = ROOT / "docs" / "ai" / "testing" / "2026-08-28-feature-avpro-matrix.md"
TESTS = ROOT / "tests"

#: ``T-`` followed by a category and a number: ``T-R1``, ``T-X4``, ``T-N12``.
ID = re.compile(r"\bT-[A-Z]{1,2}[0-9]+\b")

#: A range, as module docstrings like to write them: ``T-N1..T-N6``, ``T-X1 .. T-X4``.
#:
#: Stripped before counting what a test file covers. A range is a summary of a file's subject, not
#: a claim that each endpoint has an assertion -- and taking it as one is a real hole, because the
#: cheapest way to satisfy this check would then be to widen a docstring. Five IDs were passing on
#: exactly that basis when this was added.
RANGE = re.compile(r"\bT-[A-Z]{1,2}[0-9]+\s*\.\.+\s*T?-?[A-Z]{0,2}[0-9]+\b")

#: Scenarios that are declared but not yet implemented, each with the reason it is not.
#:
#: Every entry here is a promise, not an excuse -- and it is a promise the suite can see. Two
#: categories, and the distinction matters:
#:
#: * **M-E** is ordinary outstanding work. These become tests when the entities land.
#: * **T-L** is the live tier. These cannot run in CI by construction: they need the real matrix,
#:   and several of them are disruptive enough to need someone present (T-L2 blanks a display;
#:   T-L4 pulls power). They are checked off by hand in the doc, with evidence.
DEFERRED: dict[str, str] = {
    "T-L2": "live tier: toggling OUT1 STREAM blanks a display -- someone must be watching it",
    "T-L4": "live tier: pulling power to the matrix cannot be done from CI",
    "T-L6": "live tier: the LCD backlight timeout is only observable by a person at the matrix",
    "T-L7": "live tier: only a real source can show whether it noticed the hot-plug drop",
}

#: Live scenarios that have been run against real hardware, and what was observed.
#:
#: The live tier used to sit outside this mechanism entirely. A scenario could be *implemented* or
#: *deferred* and nothing else, so running one by hand left it looking undone for ever -- and
#: ticking it by hand would have made the box a claim nothing checked, which is the failure this
#: module exists to prevent.
#:
#: Dates and figures are the point. "Verified" with no number is exactly the sort of assurance the
#: testing doc carried about T-T1 through the whole life of a feature in which it did not exist.
VERIFIED_LIVE: dict[str, str] = {
    "T-L1": (
        "2026-08-29: routed output 1 to input 3 from Home Assistant and back. The matrix agreed "
        "both times, exactly one state change per write, routing restored bit for bit."
    ),
    "T-L3": (
        "2026-08-29: routed an output over the CGI interface so Home Assistant learned of it only "
        "by push. Reflected in 0.538 s, and 0.549 s on the restore, against a 2 s budget (S2)."
    ),
    "T-L5": (
        "2026-08-29: installed from HACS as a custom repository on the live instance. Entry "
        "loaded, 55 entities registered, 12 enabled, identified as AC-MX44-AUHD V1.41 (S3)."
    ),
}


#: A checklist line: ``- [x] T-N10 ...`` or ``- [ ] T-E1 ...``.
CHECKBOX = re.compile(r"^- \[([ xX])\]\s*(T-[A-Z]{1,2}[0-9]+)\b", re.MULTILINE)


def _declared() -> set[str]:
    return set(ID.findall(TESTING_DOC.read_text(encoding="utf-8")))


def _ticked() -> set[str]:
    """IDs whose checkbox in the doc is ticked."""
    text = TESTING_DOC.read_text(encoding="utf-8")
    return {key for mark, key in CHECKBOX.findall(text) if mark.lower() == "x"}


def _referenced() -> set[str]:
    """Every scenario ID named anywhere in the suite.

    Source files only, and never this one.

    Excluding this file is load-bearing twice over. It lists every deferred ID in
    :data:`DEFERRED`, so counting itself would make every deferred scenario look implemented --
    the check would certify precisely the gaps it exists to find. It also discusses IDs in prose,
    which would manufacture orphans.

    A stale ``.pyc`` is skipped for a related reason: it still holds the docstrings of tests that
    have since been renamed or deleted, so a removed test would go on satisfying this check.
    """
    found: set[str] = set()
    for path in TESTS.rglob("*.py"):
        if "__pycache__" in path.parts or path.resolve() == Path(__file__).resolve():
            continue
        text = RANGE.sub(" ", path.read_text(encoding="utf-8"))
        found |= set(ID.findall(text))
    return found


def test_every_declared_scenario_is_accounted_for() -> None:
    """The check that would have caught T-T1.

    A scenario that is declared, not implemented, and not deferred is invisible without this --
    which is exactly how a specified test came to not exist for the entire life of a feature.

    Three ways to be accounted for, and no fourth: a test names it, :data:`DEFERRED` explains why
    not yet, or :data:`VERIFIED_LIVE` records what was seen on the hardware.
    """
    missing = _declared() - _referenced() - set(DEFERRED) - set(VERIFIED_LIVE)
    assert not missing, (
        "declared in the testing doc and accounted for nowhere:\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nWrite the test and name the ID in it, add it to DEFERRED with a reason, or record"
        " the live observation in VERIFIED_LIVE."
    )


def test_the_deferral_list_does_not_rot() -> None:
    """A deferred scenario that has since been implemented must leave the list.

    Without this the list would accumulate entries describing work that is long done, and would
    stop meaning anything -- at which point it is documentation of the worst kind: the sort that
    is confidently wrong.
    """
    done = set(DEFERRED) & _referenced()
    assert not done, (
        "implemented but still listed as deferred -- remove from DEFERRED:\n  "
        + "\n  ".join(sorted(done))
    )


def test_nothing_is_deferred_that_was_never_declared() -> None:
    """DEFERRED cannot invent scenarios, only postpone declared ones."""
    unknown = set(DEFERRED) - _declared()
    assert not unknown, "in DEFERRED but not declared in the testing doc:\n  " + "\n  ".join(
        sorted(unknown)
    )


def _cited() -> set[str]:
    """Scenario IDs named outside the suite -- in the integration or the other phase docs.

    The orphan check below originally scanned only ``tests/``, and something slipped straight
    through the gap: ``T-L7`` was invented while documenting an unmeasured constant, cited in
    ``const.py`` and the deployment checklist, and never declared in the testing doc. So it was
    tracked by nothing -- which is the exact failure this whole module exists to prevent, created
    a few hours after the module was written.

    A citation is a claim that a scenario exists. It does not matter which file makes it.
    """
    roots = [ROOT / "custom_components", ROOT / "docs"]
    found: set[str] = set()
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".md"} or "__pycache__" in path.parts:
                continue
            if path.resolve() == TESTING_DOC.resolve():
                continue
            found |= set(ID.findall(RANGE.sub(" ", path.read_text(encoding="utf-8"))))
    return found


def test_nothing_outside_the_suite_cites_an_undeclared_scenario() -> None:
    """A scenario named in the code or a doc has to exist in the testing doc."""
    orphans = _cited() - _declared()
    assert not orphans, (
        "cited outside tests/ but not declared in the testing doc:\n  "
        + "\n  ".join(sorted(orphans))
    )


def test_no_test_claims_a_scenario_the_doc_does_not_declare() -> None:
    """The other direction: a test naming ``T-Q7`` is citing something that does not exist.

    Usually a typo, occasionally a scenario someone meant to add to the doc and did not. Either
    way the citation is a dead link, and dead links are how a document stops being trusted.
    """
    orphans = _referenced() - _declared()
    assert not orphans, "named in a test but not declared in the testing doc:\n  " + "\n  ".join(
        sorted(orphans)
    )


def test_a_ticked_box_means_a_test_exists() -> None:
    """The checkbox is a claim, so it has to be a checkable one.

    A ticked box that nothing implements is worse than an unticked one: it is the doc actively
    asserting coverage that is not there, which is what makes a reader stop looking.

    Backed by a test, or by a recorded live observation. Nothing else counts.
    """
    lying = _ticked() - _referenced() - set(VERIFIED_LIVE)
    assert not lying, (
        "ticked, but backed by neither a test nor a live observation:\n  "
        + "\n  ".join(sorted(lying))
    )


def test_a_live_observation_is_recorded_as_ticked() -> None:
    """The other direction: having run a scenario and not ticked it is the same silence."""
    unticked = set(VERIFIED_LIVE) - _ticked()
    assert not unticked, "verified live but still unticked in the testing doc:\n  " + "\n  ".join(
        sorted(unticked)
    )


def test_nothing_is_both_deferred_and_verified() -> None:
    """The two records of live status cannot disagree."""
    both = set(DEFERRED) & set(VERIFIED_LIVE)
    assert not both, "both deferred and verified live:\n  " + "\n  ".join(sorted(both))


def test_every_live_observation_cites_something_checkable() -> None:
    """A date and a detail, not the word "verified".

    An observation with no number is the same kind of assurance the testing doc gave about T-T1
    while that test did not exist.
    """
    vague = [key for key, note in VERIFIED_LIVE.items() if "2026-" not in note or len(note) < 60]
    assert not vague, f"live observations with no date or no detail: {sorted(vague)}"


def test_a_deferred_scenario_is_not_ticked() -> None:
    """The two records of "not done yet" must agree with each other."""
    both = _ticked() & set(DEFERRED)
    assert not both, "ticked in the doc while still listed as deferred:\n  " + "\n  ".join(
        sorted(both)
    )


def test_every_declared_scenario_has_a_checkbox() -> None:
    """An ID mentioned only in prose escapes the checklist entirely.

    Without this, moving a scenario into a paragraph would silently remove it from the tracked
    set while leaving it looking documented.
    """
    unlisted = _declared() - {
        key for _mark, key in CHECKBOX.findall(TESTING_DOC.read_text(encoding="utf-8"))
    }
    assert not unlisted, "declared in the doc but not as a checklist item:\n  " + "\n  ".join(
        sorted(unlisted)
    )


def test_every_deferral_gives_a_reason() -> None:
    """ "Not yet" is not a reason. The next person needs to know what unblocks it."""
    empty = [key for key, reason in DEFERRED.items() if len(reason.strip()) < 15]
    assert not empty, f"deferred without a usable reason: {sorted(empty)}"


def test_the_scan_actually_found_something() -> None:
    """A traceability check that silently parses nothing passes forever.

    The failure mode this guards against is a renamed or moved testing doc: ``_declared()`` would
    return an empty set, every other assertion here would pass trivially, and the mechanism would
    be dead without a single red test to say so.
    """
    assert TESTING_DOC.exists(), f"the testing doc moved; this check is now vacuous ({TESTING_DOC})"
    declared = _declared()
    assert len(declared) > 30, f"only {len(declared)} IDs parsed; the doc format likely changed"
    assert len(_referenced()) > 20, "almost nothing in the suite cites a scenario ID"
