"""The quality-scale declaration.

Keeps the file from rotting into a list of aspirations. Two things are enforced: every Bronze
rule is accounted for, and nothing claims an exemption without saying why.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "custom_components" / "ha_avpro_edge" / "quality_scale.yaml"
TEXT = PATH.read_text(encoding="utf-8")

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


def test_polling_claims_reference_the_measurement_that_supports_them() -> None:
    """`appropriate-polling` is the one Bronze rule that is a numeric claim rather than a
    structural one, so the number has to be here to be checkable."""
    block = TEXT.split("appropriate-polling:", 1)[1].split("brands:", 1)[0]
    assert "0.45 req/s" in block
