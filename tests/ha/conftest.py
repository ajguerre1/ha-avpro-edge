"""Fixtures for the Home Assistant-dependent suite.

**This directory runs in CI only.** ``pytest-homeassistant-custom-component`` pulls in Home
Assistant, which cannot be imported on Windows at all -- ``homeassistant.runner`` imports
POSIX-only ``fcntl``. The offline suite in the parent directory covers everything that does not
need Home Assistant, and runs everywhere.

These tests drive the integration against ``tools/fake_avpro.py`` over real loopback sockets, so
they exercise the actual transport rather than a mock of it. ``pytest-socket`` arrives with
``pytest-homeassistant-custom-component`` and blocks sockets session-wide, which is why every
module here opts back in with ``pytest.mark.enable_socket``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

ROOT = Path(__file__).resolve().parents[2]

# The repository root, so `custom_components.ha_avpro_edge` imports as a package. The offline
# suite deliberately does the opposite -- it puts the *component* directory on the path and
# imports `avpro` directly, which avoids executing the package __init__ and its Home Assistant
# imports. Here Home Assistant is available and the real package is what is under test.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from fake_avpro import FakeMatrix

from custom_components.ha_avpro_edge.const import DOMAIN

# No `pytest_plugins` here. pytest refuses it outside the top-level conftest, and it is redundant
# anyway: pytest-homeassistant-custom-component registers itself through a pytest11 entry point,
# so installing it is enough.


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Without this, Home Assistant refuses to load anything under custom_components/."""
    return


@pytest.fixture
async def fake() -> FakeMatrix:
    """A fake matrix on an ephemeral loopback port."""
    async with FakeMatrix() as matrix:
        yield matrix


#: The MAC the fake matrix reports, formatted the way Home Assistant stores one.
FAKE_MAC = "aa:bb:cc:dd:ee:ff"


def make_entry(host: str, *, unique_id: str = FAKE_MAC, **options: Any) -> MockConfigEntry:
    """Build an unloaded entry.

    ``unique_id`` is a constructor argument rather than something to assign afterwards:
    ``MockConfigEntry`` refuses direct assignment, since in production it may only change through
    ``async_update_entry``.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="AC-MX44-AUHD",
        data={CONF_HOST: host},
        options=options,
        unique_id=unique_id,
    )


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, fake: FakeMatrix) -> MockConfigEntry:
    """An entry set up against the fake and fully loaded."""
    entry = make_entry(fake.host)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
