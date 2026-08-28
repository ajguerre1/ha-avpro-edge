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

import sys
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "ha_avpro_edge"

if str(_COMPONENT) not in sys.path:
    sys.path.insert(0, str(_COMPONENT))
