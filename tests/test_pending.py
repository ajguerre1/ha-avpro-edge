"""The optimistic-write overlay, driven by a fake clock.

The behaviour under test that matters most is the KEEP rule: a poll that disagrees with an
outstanding write inside the settle window must change nothing. Getting that wrong produces a
visible flick back to the old source and then forward again on every routing change.
"""

from __future__ import annotations

import pytest
from avpro.pending import PendingWrites

WINDOW = 1.5


class FakeClock:
    """A monotonic clock the test drives by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def pending(clock: FakeClock) -> PendingWrites:
    return PendingWrites(clock)


def device(**values):
    """Build a lookup function standing in for a freshly-polled MatrixState."""
    return lambda key: values.get(key)


# ---------------------------------------------------------------------------------------------
# Reading through the overlay
# ---------------------------------------------------------------------------------------------


def test_a_recorded_write_is_visible_immediately(pending: PendingWrites) -> None:
    pending.record("video_route_1", 3, WINDOW)
    assert pending.get("video_route_1") == 3


def test_an_unrecorded_key_falls_through_to_the_fallback(pending: PendingWrites) -> None:
    assert pending.get("video_route_1", 1) == 1


def test_an_empty_overlay_is_falsy_and_holds_nothing(pending: PendingWrites) -> None:
    assert len(pending) == 0
    assert "video_route_1" not in pending
    assert pending.next_deadline() is None


# ---------------------------------------------------------------------------------------------
# Confirmation by value
# ---------------------------------------------------------------------------------------------


def test_a_matching_poll_confirms_and_drops_the_entry(pending: PendingWrites) -> None:
    pending.record("video_route_1", 3, WINDOW)
    confirmed = pending.confirm(device(video_route_1=3))
    assert confirmed == {"video_route_1"}
    assert "video_route_1" not in pending


def test_confirmation_leaves_other_keys_alone(pending: PendingWrites) -> None:
    pending.record("video_route_1", 3, WINDOW)
    pending.record("video_route_2", 4, WINDOW)
    pending.confirm(device(video_route_1=3, video_route_2=1))
    assert "video_route_1" not in pending
    assert pending.get("video_route_2") == 4


def test_confirming_an_empty_overlay_is_harmless(pending: PendingWrites) -> None:
    assert pending.confirm(device()) == frozenset()


# ---------------------------------------------------------------------------------------------
# The KEEP rule -- the reason this module exists
# ---------------------------------------------------------------------------------------------


def test_a_disagreeing_poll_inside_the_window_changes_nothing(pending: PendingWrites) -> None:
    """A poll can beat the matrix's own apply latency.

    Clearing the overlay on that report would replace the commanded value with the pre-write
    one, and the entity would visibly flick back and then forward.
    """
    pending.record("video_route_1", 3, WINDOW)
    confirmed = pending.confirm(device(video_route_1=1))  # still showing the old input
    assert confirmed == frozenset()
    assert pending.get("video_route_1") == 3


def test_repeated_stale_polls_still_do_not_clear_the_overlay(
    pending: PendingWrites, clock: FakeClock
) -> None:
    pending.record("video_route_1", 3, WINDOW)
    for _ in range(5):
        clock.advance(0.2)
        pending.confirm(device(video_route_1=1))
    assert pending.get("video_route_1") == 3


def test_a_late_arriving_match_still_confirms(pending: PendingWrites, clock: FakeClock) -> None:
    """The normal case: a couple of stale polls, then the matrix catches up."""
    pending.record("video_route_1", 3, WINDOW)
    clock.advance(0.4)
    pending.confirm(device(video_route_1=1))
    clock.advance(0.4)
    assert pending.confirm(device(video_route_1=3)) == {"video_route_1"}
    assert len(pending) == 0


# ---------------------------------------------------------------------------------------------
# Expiry -- the device wins once the window has passed
# ---------------------------------------------------------------------------------------------


def test_nothing_expires_before_the_deadline(pending: PendingWrites, clock: FakeClock) -> None:
    pending.record("video_route_1", 3, WINDOW)
    clock.advance(WINDOW - 0.01)
    assert pending.expire() == frozenset()
    assert pending.get("video_route_1") == 3


def test_the_entry_expires_once_the_deadline_passes(
    pending: PendingWrites, clock: FakeClock
) -> None:
    pending.record("video_route_1", 3, WINDOW)
    clock.advance(WINDOW)
    assert pending.expire() == {"video_route_1"}
    assert "video_route_1" not in pending


def test_expiry_is_idempotent(pending: PendingWrites, clock: FakeClock) -> None:
    pending.record("video_route_1", 3, WINDOW)
    clock.advance(WINDOW + 1)
    assert pending.expire() == {"video_route_1"}
    assert pending.expire() == frozenset()


def test_expiry_reports_only_the_keys_that_actually_expired(
    pending: PendingWrites, clock: FakeClock
) -> None:
    pending.record("video_route_1", 3, WINDOW)
    clock.advance(1.0)
    pending.record("video_route_2", 4, WINDOW)  # younger, deadline is later
    clock.advance(0.6)
    assert pending.expire() == {"video_route_1"}
    assert pending.get("video_route_2") == 4


def test_an_overridden_write_expires_and_is_therefore_countable(
    pending: PendingWrites, clock: FakeClock
) -> None:
    """This is what "another controller owns this output" looks like from here."""
    pending.record("video_route_1", 3, WINDOW)
    overrides = 0
    for _ in range(4):
        clock.advance(0.5)
        pending.confirm(device(video_route_1=2))  # someone else's value, never ours
        overrides += len(pending.expire())
    assert overrides == 1


# ---------------------------------------------------------------------------------------------
# Re-recording and discarding
# ---------------------------------------------------------------------------------------------


def test_re_recording_a_key_restarts_its_window(pending: PendingWrites, clock: FakeClock) -> None:
    """The newer command is the one whose latency is now being waited out."""
    pending.record("video_route_1", 3, WINDOW)
    clock.advance(WINDOW - 0.1)
    pending.record("video_route_1", 4, WINDOW)
    clock.advance(0.2)
    assert pending.expire() == frozenset()
    assert pending.get("video_route_1") == 4


def test_discard_removes_without_confirmation(pending: PendingWrites) -> None:
    pending.record("video_route_1", 3, WINDOW)
    pending.discard("video_route_1")
    assert "video_route_1" not in pending


def test_discarding_an_absent_key_is_harmless(pending: PendingWrites) -> None:
    pending.discard("nothing_here")


def test_clear_drops_everything(pending: PendingWrites) -> None:
    """Called when the transport drops: replaying these later would be a stale command."""
    pending.record("video_route_1", 3, WINDOW)
    pending.record("audio_delay_2", "ms_90", WINDOW)
    pending.clear()
    assert len(pending) == 0


# ---------------------------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------------------------


def test_next_deadline_is_the_earliest_outstanding(
    pending: PendingWrites, clock: FakeClock
) -> None:
    pending.record("a", 1, WINDOW)
    clock.advance(0.5)
    pending.record("b", 2, WINDOW)
    assert pending.next_deadline() == pytest.approx(1000.0 + WINDOW)


def test_values_of_any_type_round_trip(pending: PendingWrites) -> None:
    """Keys cover routes (int), enum options (str) and toggles (bool)."""
    for key, value in (("route", 3), ("delay", "ms_90"), ("audio", True), ("nothing", None)):
        pending.record(key, value, WINDOW)
        assert pending.get(key, "fallback") == value
