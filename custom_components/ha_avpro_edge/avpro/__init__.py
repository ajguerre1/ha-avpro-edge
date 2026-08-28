"""Vendored client for AVPro Edge AUHD-series HDMI matrix switchers.

This package is deliberately free of Home Assistant imports, which is enforced by
``tests/test_no_ha_imports.py``. Two things follow from that:

* it can be developed and tested on any platform, including Windows, where Home Assistant itself
  cannot be imported at all; and
* ``manifest.json`` keeps ``requirements: []``. Vendoring rather than depending is the deliberate
  choice -- a ``git+https`` requirement would be refetched on every restart, because Home
  Assistant's ``is_installed()`` returns False for URL requirements.

The transport is HTTP, never telnet. The telnet server on this hardware accepts a single client
at a time and is normally held open by a third-party control system; see ``client.py``.
"""

from __future__ import annotations
