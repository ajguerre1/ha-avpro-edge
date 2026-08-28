"""Diagnostics, written on the assumption that they will be pasted into a public issue.

That assumption is why this is a hand-built summary rather than a dump with a redaction list.
A denylist protects the fields somebody remembered; everything this device returns that is worth
protecting -- the four output names are room names, the four input names are source names, and
the network body carries the address and MAC -- would have to be on it. Building up from shapes
and counts instead means a field added to the protocol later cannot leak by default.

What a bug report actually needs is which endpoints this firmware has, what the routing looks
like, and whether writes are being overridden. None of that requires a single name.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import AvProConfigEntry

#: Kept in sync with the config-entry data keys that must never appear in a diagnostics dump.
TO_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AvProConfigEntry
) -> dict[str, Any]:
    """Summarise one matrix without disclosing anything about the site."""
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": {
            "options": dict(entry.options),
            "data_keys": sorted(entry.data),  # names of the keys, never their values
            "unique_id_kind": "mac" if not str(entry.unique_id).startswith("host-") else "host",
        },
        "matrix": coordinator.diagnostics(),
    }
