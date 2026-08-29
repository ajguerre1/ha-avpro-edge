"""Shared test configuration for the offline suite.

The vendored client is placed on ``sys.path`` as a top-level ``avpro`` package rather than being
reached through ``custom_components.ha_avpro_edge``. That is not a shortcut: importing the parent
package would execute ``custom_components/ha_avpro_edge/__init__.py``, which imports Home
Assistant and therefore cannot run on Windows. Importing ``avpro`` directly is what lets this
suite run on the development box, and it structurally enforces that the client has no Home
Assistant imports -- if one is ever added, these tests stop collecting.

Tests that genuinely need Home Assistant live in ``tests/ha/`` and run in CI only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "ha_avpro_edge"
_AVPRO = _COMPONENT / "avpro"


def _load_avpro_without_shadowing_the_stdlib() -> None:
    """Register ``avpro`` as a top-level package without putting its parent on ``sys.path``.

    The obvious approach -- appending the component directory to ``sys.path`` -- is a trap. That
    directory contains ``select.py``, ``sensor.py``, ``switch.py`` and ``media_player.py``, and
    the first of those **shadows the standard library's ``select``**. Anything importing it
    afterwards gets a Home Assistant platform module instead: ``asyncio`` pulls in ``selectors``
    which imports ``select``, so the failure is an unimportable ``asyncio``.

    It did not bite only because pytest imports asyncio before conftest runs, leaving the real
    ``select`` already in ``sys.modules``. That is luck about import order, not a design, and it
    breaks the moment anything is imported in a different sequence.

    Loading the package directly by path avoids the parent directory entirely.
    """
    if "avpro" in sys.modules:
        return

    spec = importlib.util.spec_from_file_location(
        "avpro",
        _AVPRO / "__init__.py",
        submodule_search_locations=[str(_AVPRO)],
    )
    if spec is None or spec.loader is None:  # pragma: no cover - only on a broken checkout
        raise RuntimeError(f"cannot load the vendored client from {_AVPRO}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["avpro"] = module
    spec.loader.exec_module(module)


_load_avpro_without_shadowing_the_stdlib()


def _home_assistant_is_importable() -> bool:
    """Whether this machine can import Home Assistant at all.

    It cannot on Windows: ``homeassistant.runner`` imports POSIX-only ``fcntl``. That is a
    property of the platform, not of the installation, so there is no version of
    ``pip install`` that fixes it.
    """
    return importlib.util.find_spec("homeassistant") is not None


#: Skip the Home Assistant-dependent suite where Home Assistant cannot be imported, so that a
#: plain ``pytest tests/`` works on the development box without remembering a flag. In CI, where
#: Home Assistant is installed, nothing is skipped.
#:
#: The whole directory is named rather than a glob inside it: a glob still lets pytest descend far
#: enough to load ``tests/ha/conftest.py``, which is itself the thing that cannot be imported.
collect_ignore: list[str] = [] if _home_assistant_is_importable() else ["ha"]
