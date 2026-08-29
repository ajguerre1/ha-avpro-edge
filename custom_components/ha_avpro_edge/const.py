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
CONF_TRANSPORT: Final = "transport"

#: The device's control port. Settable on the unit itself with ``SET TIP`` and reported back
#: in the network status, so an installation that has moved it off 23 is a real configuration
#: rather than a hypothetical. Stored on the entry so it survives a restart.
CONF_TELNET_PORT: Final = "telnet_port"
DEFAULT_TELNET_PORT: Final = 23

#: How the integration reaches the matrix.
#:
#: Telnet is primary: it pushes changes within ~300-400 ms, reads the whole device in one
#: command, and is the only wire that can see output stream state, input power, key lock and the
#: LCD timeout. HTTP is the exception -- used when telnet is unavailable, for the one operation
#: only it has (renaming ports), or when the user has asked for it.
TRANSPORT_AUTO: Final = "auto"
TRANSPORT_TELNET: Final = "telnet"
TRANSPORT_HTTP: Final = "http"

TRANSPORT_OPTIONS: Final[tuple[str, ...]] = (TRANSPORT_AUTO, TRANSPORT_TELNET, TRANSPORT_HTTP)
DEFAULT_TRANSPORT: Final = TRANSPORT_AUTO

#: How often to re-check the control socket after falling back to HTTP.
#:
#: This was 300 s, on the reasoning that the socket was usually held by another control system and
#: that does not change by the second. That premise is gone: Home Assistant is the only thing
#: driving this matrix, so a socket we cannot have is a fault -- a rebooting device, a network
#: blip, a wedged session -- and every one of those clears in well under five minutes.
#:
#: The cost of checking is one connection attempt against a device on the LAN, so the interval is
#: set by how long a user should sit in a degraded state, not by politeness to a neighbour that no
#: longer exists.
TELNET_RETRY_INTERVAL: Final = 60.0

#: Repair-issue id for "telnet was expected and is not available", suffixed per entry.
#:
#: A log line was enough when falling back was a legitimate accommodation. Now it is the only
#: signal that something is wrong, and nobody reads logs they have no reason to open.
ISSUE_TELNET_UNAVAILABLE: Final = "telnet_unavailable"

#: Safety-net read interval on a pushing transport. Pushes carry normal operation; this exists
#: only so a missed one cannot leave state stale indefinitely.
PUSH_SAFETY_NET_INTERVAL: Final = 60

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

#: How long an input's TMDS is held off during a hot-plug reset.
#:
#: **Not measured, unlike every other timing constant here, and the exception is deliberate rather
#: than an oversight.** What this needs to be is a property of the *source device* rather than of
#: the matrix: long enough for a set-top box or console to notice its hot-plug line drop and
#: re-read the EDID when it returns. Nothing on either wire can report whether that happened, so
#: no probe against the matrix can establish it -- only watching a real source recover can.
#:
#: One second is the conventional figure for HDMI hot-plug signalling and is comfortably above the
#: ~100 ms most sinks need. Erring long costs a slightly longer blank; erring short means the
#: source never notices and the button silently does nothing, which is much worse. Confirmed by
#: T-L7 when a real source is available.
HOT_PLUG_RESET_HOLD: Final = 1.0

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
KEY_SIGNAL: Final = "signal"

#: The four telnet reads the CGI interface has no status endpoint for. ``KEY_TMDS_STREAM`` used to
#: sit here as a placeholder for the first of them and was never referenced by anything; these are
#: the names the wire grammar actually produces.
KEY_STREAM: Final = "stream"
KEY_INPUT_POWER: Final = "input_power"
KEY_KEY_LOCK: Final = "key_lock"
KEY_LCD_TIMEOUT: Final = "lcd_timeout"

KEY_EDID: Final = "edid"
KEY_BIND_MODE: Final = "bind_mode"


def port_key(kind: str, index: int) -> str:
    """``("video_route", 2)`` -> ``"video_route_2"``."""
    return f"{kind}_{index}"
