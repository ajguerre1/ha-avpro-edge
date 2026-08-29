"""Packaging: what HACS and hassfest will look at.

These fail fast on the development box rather than after a push, and each one encodes a decision
that would otherwise erode quietly.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "ha_avpro_edge"
PACKAGE = ROOT / "custom_components" / DOMAIN
MANIFEST = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
HACS = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------------------


def test_the_component_lives_where_hacs_expects_it() -> None:
    assert (ROOT / "custom_components").is_dir()
    assert PACKAGE.is_dir()
    assert (PACKAGE / "manifest.json").is_file()


def test_there_is_exactly_one_integration_in_the_repository() -> None:
    """HACS allows one integration per repository."""
    packages = [p for p in (ROOT / "custom_components").iterdir() if p.is_dir()]
    assert len(packages) == 1


def test_the_domain_is_a_valid_python_identifier() -> None:
    """A hyphen here would make the package unimportable and fail hassfest.

    Worth asserting rather than assuming: the one other public integration for this device family
    ships ``"domain": "avpro-acmxnn"``, which cannot be imported as a module.
    """
    assert MANIFEST["domain"].isidentifier()
    assert MANIFEST["domain"] == DOMAIN == PACKAGE.name


# ---------------------------------------------------------------------------------------------
# manifest.json
# ---------------------------------------------------------------------------------------------


def test_every_key_hacs_requires_is_present() -> None:
    for key in ("domain", "documentation", "issue_tracker", "codeowners", "name", "version"):
        assert MANIFEST.get(key), f"manifest.json is missing {key}"


def test_there_are_no_runtime_dependencies() -> None:
    """The client is vendored under avpro/ rather than depended on.

    Never a git+https requirement either: Home Assistant's ``is_installed()`` returns False for
    URL requirements, so it would be refetched on every single restart.
    """
    assert MANIFEST["requirements"] == []
    assert MANIFEST["dependencies"] == []


def test_the_iot_class_says_push() -> None:
    """T-E6. The primary transport is pushed to, so the manifest has to say so.

    This asserted ``local_polling`` for as long as the telnet socket was assumed to belong to the
    house's control system -- "this device offers no push transport that can be used" was true of
    the installation, not of the device. It is not true here: Home Assistant is the only thing
    driving this matrix, telnet is primary, and the device volunteers changes on it within
    ~300-400 ms.

    ``local_push`` remains the honest answer even though the HTTP fallback polls. The class
    describes what the integration normally is, and someone comparing integrations wants to know
    that a routing change arrives rather than being waited for.
    """
    assert MANIFEST["iot_class"] == "local_push"


def test_it_declares_itself_a_device_with_a_config_flow() -> None:
    assert MANIFEST["integration_type"] == "device"
    assert MANIFEST["config_flow"] is True


def test_a_config_flow_is_declared_only_if_one_exists() -> None:
    if MANIFEST.get("config_flow"):
        assert (PACKAGE / "config_flow.py").is_file(), (
            "manifest declares config_flow: true but config_flow.py is missing -- hassfest fails "
            "on this"
        )


def test_the_version_is_semver() -> None:
    parts = MANIFEST["version"].split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_the_logger_matches_the_package() -> None:
    assert MANIFEST["loggers"] == [f"custom_components.{DOMAIN}"]


def test_documentation_and_issue_tracker_point_at_the_same_repository() -> None:
    assert MANIFEST["issue_tracker"].startswith(MANIFEST["documentation"])


def test_no_quality_scale_key_in_the_manifest() -> None:
    """`quality_scale` in manifest.json is for core integrations; hassfest objects here.

    The declaration lives in quality_scale.yaml instead.
    """
    assert "quality_scale" not in MANIFEST


# ---------------------------------------------------------------------------------------------
# hacs.json
# ---------------------------------------------------------------------------------------------


def test_hacs_declares_a_name_and_a_minimum_home_assistant() -> None:
    assert HACS["name"]
    assert HACS["homeassistant"]


def test_hacs_renders_the_readme() -> None:
    assert HACS.get("render_readme") is True
    assert (ROOT / "README.md").is_file()


def test_the_repository_carries_a_license() -> None:
    assert (ROOT / "LICENSE").is_file()


# ---------------------------------------------------------------------------------------------
# The vendored client stays free of Home Assistant
# ---------------------------------------------------------------------------------------------


def test_the_vendored_client_imports_no_home_assistant() -> None:
    """This is what lets the whole client be developed and tested on Windows.

    Home Assistant cannot be imported there at all -- ``homeassistant.runner`` needs POSIX-only
    ``fcntl`` -- so one stray import would move this entire suite into CI.
    """
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in (PACKAGE / "avpro").rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.startswith(("import homeassistant", "from homeassistant"))
    ]
    assert not offenders, "Home Assistant import inside the vendored client:\n" + "\n".join(
        offenders
    )


def test_the_vendored_client_is_a_real_package() -> None:
    assert (PACKAGE / "avpro" / "__init__.py").is_file()
    assert len(list((PACKAGE / "avpro").glob("*.py"))) >= 5
