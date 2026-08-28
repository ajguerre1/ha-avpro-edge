"""Translations.

Two failure modes this catches, both of which show up to a user as a raw key on screen:

* ``strings.json`` and ``translations/en.json`` drifting apart -- CI diffs them, but finding out
  after a push is slower than finding out here;
* the code referencing a key that nobody wrote, or a string existing for a key nothing raises.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "ha_avpro_edge"
STRINGS_PATH = PACKAGE / "strings.json"
EN_PATH = PACKAGE / "translations" / "en.json"

STRINGS = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))


def _source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.rglob("*.py"))


# ---------------------------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------------------------


def test_strings_and_english_translations_are_byte_identical() -> None:
    """CI runs the same `diff`. Failing here first is faster than failing after a push."""
    assert STRINGS_PATH.read_bytes() == EN_PATH.read_bytes()


def test_both_files_exist() -> None:
    """`diff` also fails when exactly one exists, which is the other way this drifts."""
    assert STRINGS_PATH.is_file()
    assert EN_PATH.is_file()


# ---------------------------------------------------------------------------------------------
# The config flow's keys
# ---------------------------------------------------------------------------------------------


def test_every_error_the_flow_can_emit_has_a_message() -> None:
    """A missing key renders as the raw string, which is how users end up reading `not_avpro`."""
    emitted = set(re.findall(r'errors\["base"\]\s*=\s*"([a-z_]+)"', _source()))
    emitted |= set(re.findall(r'AvProValidationError\("([a-z_]+)"\)', _source()))
    declared = set(STRINGS["config"]["error"])
    assert emitted <= declared, f"undeclared error keys: {sorted(emitted - declared)}"


def test_no_error_message_is_declared_that_can_never_be_shown() -> None:
    emitted = set(re.findall(r'errors\["base"\]\s*=\s*"([a-z_]+)"', _source()))
    emitted |= set(re.findall(r'AvProValidationError\("([a-z_]+)"\)', _source()))
    declared = set(STRINGS["config"]["error"])
    assert declared <= emitted, f"unreachable error keys: {sorted(declared - emitted)}"


def test_every_abort_reason_the_flow_uses_is_declared() -> None:
    declared = set(STRINGS["config"]["abort"])
    used = set(re.findall(r'reason="([a-z_]+)"', _source()))
    assert used <= declared, f"undeclared abort reasons: {sorted(used - declared)}"


def test_the_standard_abort_reasons_are_present() -> None:
    """Home Assistant raises these itself, so they must be declared even though no line here
    spells them out."""
    for reason in ("already_configured", "reconfigure_successful"):
        assert reason in STRINGS["config"]["abort"]


def test_each_flow_step_has_a_title_and_labels_every_field() -> None:
    for name, step in STRINGS["config"]["step"].items():
        assert step.get("title"), f"step {name} has no title"
        for field in step.get("data", {}):
            assert step["data"][field], f"step {name} field {field} has no label"


# ---------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------


def test_every_option_is_labelled_and_explained() -> None:
    from_const = {"polling_profile", "allow_writes"}
    step = STRINGS["options"]["step"]["init"]
    assert set(step["data"]) == from_const
    assert set(step["data_description"]) == from_const


def test_every_polling_profile_has_a_translated_label() -> None:
    """The select is rendered from POLLING_PROFILES; an unlabelled one shows its raw key."""
    import sys

    sys.path.insert(0, str(PACKAGE))
    from const import POLLING_PROFILES

    labels = STRINGS["selector"]["polling_profile"]["options"]
    assert set(labels) == set(POLLING_PROFILES)
    assert all(labels.values())


# ---------------------------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------------------------


def test_declared_exception_messages_are_non_empty_and_placeholders_are_paired() -> None:
    for key, entry in STRINGS.get("exceptions", {}).items():
        message = entry.get("message", "")
        assert message, f"exception {key} has no message"
        # An unbalanced brace renders literally in the UI.
        assert message.count("{") == message.count("}")
