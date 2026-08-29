"""The quality-scale declaration.

Keeps the file from rotting into a list of aspirations. Every Bronze rule is accounted for,
nothing claims an exemption without saying why, and -- the part added last -- the ``docs-*`` rules
agree with what the README actually contains.

**A status can say anything, and for a while several of them said the wrong thing.** The audit
that produced ``tests/ha/test_diagnostics.py`` found ``diagnostics`` marked *done* with the module
at 0% coverage: a claim that was false. The mirror image turned up next. Rewriting the README for
the people installing this added a full troubleshooting section and worked examples, satisfying
two Gold rules -- and nothing told this file, so both stayed ``todo``. Same defect, opposite sign,
and neither was catchable because the only thing enforced here was that exemptions carry a comment.

So the check below runs in **both directions**. A ``done`` without its evidence is a false claim.
Evidence present while the rule says ``todo`` is a stale one. The second is the case that actually
happened, and the one a naive check would have waved through.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "custom_components" / "ha_avpro_edge" / "quality_scale.yaml"
TEXT = PATH.read_text(encoding="utf-8")

README = (ROOT / "README.md").read_text(encoding="utf-8")

#: ``docs-*`` rule -> the string in README.md that constitutes its evidence.
#:
#: Headings where the rule maps to a section, because a heading is what a reader actually
#: navigates by. The marker has to be something a person would have to *mean* to write -- a rule
#: satisfied by a word appearing anywhere would be checkable and worthless.
DOCS_EVIDENCE: dict[str, str] = {
    "docs-actions": "## Actions",
    "docs-configuration-parameters": "## Settings",
    "docs-installation-instructions": "## Installing",
    "docs-installation-parameters": "### Adding your matrix",
    "docs-removal-instructions": "## Removing it",
    "docs-supported-devices": "AC-MX44-AUHD",
    "docs-supported-functions": "## What you get",
    "docs-troubleshooting": "## Troubleshooting",
    "docs-use-cases": "## What people use it for",
    "docs-examples": "```yaml",
}

#: ``docs-*`` rules whose evidence is prose with no heading of its own, and where it lives.
#:
#: Listed rather than left out. An unchecked rule has to say that it is unchecked, for the same
#: reason an exemption has to carry a reason -- otherwise "not in the mapping" and "nobody got
#: round to it" look identical.
DOCS_PROSE: dict[str, str] = {
    "docs-high-level-description": (
        "the opening paragraphs, above the first heading -- a description with a heading saying "
        "'Description' is not how anybody reads a README"
    ),
    "docs-data-update": (
        "'About the connection setting', which explains that the control connection reports "
        "changes as they happen and the web interface has to be asked"
    ),
    "docs-known-limitations": (
        "spread across three places on purpose, because a limitation matters where it bites: the "
        "fixed-IP note under 'Before you start', the controls the web interface cannot offer "
        "under the connection setting, and 'Some controls are missing' under Troubleshooting"
    ),
    "docs-conditions": "exempt -- the integration provides none",
    "docs-triggers": "exempt -- the integration provides none",
}

#: The Bronze checklist, from the Home Assistant developer documentation. Bronze is the baseline
#: every new integration is expected to meet, so all of these must appear.
BRONZE = {
    "action-setup",
    "appropriate-polling",
    "brands",
    "common-modules",
    "config-flow",
    "config-flow-test-coverage",
    "dependency-transparency",
    "docs-actions",
    "docs-conditions",
    "docs-high-level-description",
    "docs-installation-instructions",
    "docs-removal-instructions",
    "docs-triggers",
    "entity-event-setup",
    "entity-unique-id",
    "has-entity-name",
    "runtime-data",
    "test-before-configure",
    "test-before-setup",
    "unique-config-entry",
}

VALID_STATUSES = {"done", "todo", "exempt"}


def _parse() -> tuple[dict[str, str], dict[str, str]]:
    """Rule name -> status, and rule name -> its indented block, without a YAML dependency.

    Two shapes appear: ``name: status`` on one line, and ``name:`` followed by an indented
    ``status:`` and ``comment:``. A rule's block runs until the next line indented by exactly two
    spaces, which is what makes the two shapes distinguishable.
    """
    statuses: dict[str, str] = {}
    blocks: dict[str, list[str]] = {}
    current: str | None = None

    for raw in TEXT.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        if match := re.match(r"^  ([a-z0-9-]+):\s*([a-z]*)\s*$", line):
            current = match.group(1)
            blocks[current] = []
            if status := match.group(2):
                statuses[current] = status
            continue

        # Anything more deeply indented belongs to the rule above it. Comment lines at column 0
        # are file-level prose and end the current block.
        if current and line.startswith("    "):
            blocks[current].append(line.strip())
            if match := re.match(r"^status:\s*([a-z]+)\s*$", line.strip()):
                statuses[current] = match.group(1)
        elif not line.startswith(" "):
            current = None

    return statuses, {name: "\n".join(lines) for name, lines in blocks.items()}


RULES, BLOCKS = _parse()


def test_the_parser_found_a_plausible_number_of_rules() -> None:
    """A parser that silently matched nothing would make every other check vacuous."""
    assert len(RULES) > 40


def test_every_bronze_rule_is_accounted_for() -> None:
    missing = BRONZE - set(RULES)
    assert not missing, f"Bronze rules with no entry: {sorted(missing)}"


def test_every_status_is_one_of_the_three_valid_values() -> None:
    invalid = {name: status for name, status in RULES.items() if status not in VALID_STATUSES}
    assert not invalid, f"invalid statuses: {invalid}"


def test_every_bronze_rule_is_done_or_explicitly_exempt() -> None:
    """Bronze is the baseline. A `todo` here means the integration is below it."""
    unmet = sorted(name for name in BRONZE if RULES[name] == "todo")
    assert not unmet, f"Bronze rules still outstanding: {unmet}"


def test_nothing_is_exempt_without_a_reason() -> None:
    """An exemption with no comment is an unfinished item wearing a different label."""
    offenders = [
        name
        for name, status in RULES.items()
        if status == "exempt" and "comment:" not in BLOCKS.get(name, "")
    ]
    assert not offenders, f"exempt with no reason given: {sorted(offenders)}"


def test_the_block_parser_captured_the_comments() -> None:
    """Guards the check above: a parser returning empty blocks would pass it vacuously."""
    exempt = [name for name, status in RULES.items() if status == "exempt"]
    assert exempt
    assert all(BLOCKS[name].strip() for name in exempt)


def test_the_manifest_does_not_duplicate_the_declaration() -> None:
    """`quality_scale` in manifest.json is a core-only key; hassfest objects on a custom one."""
    import json

    manifest = json.loads(
        (ROOT / "custom_components" / "ha_avpro_edge" / "manifest.json").read_text(encoding="utf-8")
    )
    assert "quality_scale" not in manifest


# ---------------------------------------------------------------------------------------------
# The docs rules, checked against the README rather than asserted
# ---------------------------------------------------------------------------------------------


def _docs_rules() -> set[str]:
    return {name for name in RULES if name.startswith("docs-")}


def test_every_docs_rule_is_either_checkable_or_says_why_not() -> None:
    """A new docs rule cannot be added without a decision about how it is verified."""
    accounted = set(DOCS_EVIDENCE) | set(DOCS_PROSE)
    missing = _docs_rules() - accounted
    assert not missing, f"docs rules with no evidence and no reason: {sorted(missing)}"

    stale = accounted - _docs_rules()
    assert not stale, f"evidence recorded for rules that no longer exist: {sorted(stale)}"


def test_a_done_docs_rule_has_the_documentation_it_claims() -> None:
    """The direction the diagnostics audit needed: a `done` that is not true."""
    liars = [
        name
        for name, marker in DOCS_EVIDENCE.items()
        if RULES.get(name) == "done" and marker not in README
    ]
    assert not liars, f"marked done, but the README has no such section: {sorted(liars)}"


def test_a_todo_docs_rule_has_not_quietly_been_satisfied() -> None:
    """The direction that actually caught something.

    Rewriting the README added a troubleshooting section and worked examples. Two Gold rules
    became true and both stayed ``todo`` for a week, because writing documentation and updating
    the file that describes it are separate acts and only one of them is anybody's habit.
    """
    stale = [
        name
        for name, marker in DOCS_EVIDENCE.items()
        if RULES.get(name) == "todo" and marker in README
    ]
    assert not stale, f"the README already satisfies these, still marked todo: {sorted(stale)}"


def test_the_readme_was_actually_loaded() -> None:
    """Guards both checks above: an empty README would pass the first and skip the second."""
    assert len(README) > 3000
    assert "## Troubleshooting" in README


def test_the_action_examples_are_examples_of_this_integrations_actions() -> None:
    """``docs-examples`` is satisfied by a fenced yaml block, which is a weak marker on its own.

    A snippet that does not name an action of this integration would be documentation of
    something else, so the marker is backed by the thing it stands for.
    """
    assert README.count("action: ha_avpro_edge.") >= 2


def test_polling_claims_reference_the_measurement_that_supports_them() -> None:
    """`appropriate-polling` is the one Bronze rule that is a numeric claim rather than a
    structural one, so the number has to be here to be checkable."""
    block = TEXT.split("appropriate-polling:", 1)[1].split("brands:", 1)[0]
    assert "0.45 req/s" in block
