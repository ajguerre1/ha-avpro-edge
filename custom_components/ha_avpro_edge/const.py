"""Constants, and the state-key scheme the whole integration is addressed by.

Every timing constant here carries the measurement that produced it. A number without one is a
guess wearing a constant's clothes.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ha_avpro_edge"
MANUFACTURER: Final = "AVPro Edge"

# ---------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------

CONF_ALLOW_WRITES: Final = "allow_writes"
CONF_POLLING_PROFILE: Final = "polling_profile"

#: Seconds between polls for each profile. "Balanced" matches the cadence the unit's own web UI
#: uses for the tab it is displaying, which is the most defensible default available: it is what
#: the vendor considered reasonable for this hardware.
POLLING_PROFILES: Final[dict[str, int]] = {
    "responsive": 3,
    "balanced": 5,
    "gentle": 15,
}
DEFAULT_POLLING_PROFILE: Final = "balanced"

#: Writes are on by default, as in any ordinary integration. The option exists because this
#: hardware is frequently driven by a third-party control system, and an installer may want to
#: watch the integration run before letting it touch anything.
DEFAULT_ALLOW_WRITES: Final = True

# ---------------------------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------------------------

#: How long a commanded value is shown before the device becomes authoritative again.
#:
#: MEASURED on an AC-MX44-AUHD, firmware V1.41: 20 route changes across all four outputs, each
#: timed from "command sent" to "change visible in VIDDivSta.CGI", polling every 100 ms.
#:
#:     min 25 ms   p50 28 ms   19 of 20 within 25-46 ms   max 404 ms
#:
#: One sample in twenty took an order of magnitude longer than the rest, so the tail matters more
#: than the median. 1.0 s is ~2.5x that worst observation. Twenty samples do not establish a true
#: p99, so the margin is deliberate rather than tight; if a rarer outlier exceeds it the result is
#: a brief flicker, not a wrong value.
#:
#: This is the only tuning knob for a problem that cannot be solved: from a value-only poll there
#: is no way to distinguish "the matrix has not applied it yet" from "another controller
#: overwrote it". Too short and a routing change visibly flickers back and forward; too long and
#: a genuine override shows a stale value for that long. Both failure modes are bounded by this
#: one number.
WRITE_SETTLE_WINDOW: Final = 1.0

#: Added to the settle window before the expiry watchdog fires, so the confirming poll gets a
#: chance to land first and resolve the entry cleanly.
WRITE_EXPIRY_MARGIN: Final = 0.5

# ---------------------------------------------------------------------------------------------
# State keys
# ---------------------------------------------------------------------------------------------
#
# One key per settable or readable thing, used as the entity's unique-id suffix, as the pending
# overlay's key, and as the lookup into MatrixState. Having a single vocabulary for all three is
# what makes confirm-by-value a dictionary comparison rather than a reverse mapping from URLs.
#
# These strings are part of the integration's public surface: they end up in entity ids, so
# renaming one silently breaks every automation that referenced it.

KEY_VIDEO_ROUTE: Final = "video_route"
KEY_AUDIO_ROUTE: Final = "audio_route"
KEY_EXTRACTED_AUDIO: Final = "extracted_audio"
KEY_AUDIO_DELAY: Final = "audio_delay"
KEY_SCALER: Final = "scaler"
KEY_IMAGE_ENHANCEMENT: Final = "image_enhancement"
KEY_TEST_PATTERN: Final = "test_pattern"
KEY_TMDS_STREAM: Final = "tmds_stream"
KEY_SIGNAL: Final = "signal"
KEY_EDID: Final = "edid"
KEY_BIND_MODE: Final = "bind_mode"


def port_key(kind: str, index: int) -> str:
    """``("video_route", 2)`` -> ``"video_route_2"``."""
    return f"{kind}_{index}"
