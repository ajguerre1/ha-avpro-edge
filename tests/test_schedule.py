"""Poll cadence.

The load arithmetic in this file is the justification for `appropriate-polling`. If a tier moves,
these numbers move with it and the docstring in schedule.py is wrong.
"""

from __future__ import annotations

from collections import Counter

from avpro import schedule as sch
from avpro.protocol import StatusEndpoint
from avpro.schedule import PollSchedule, endpoints_for_tick

TICK_SECONDS = 5


# ---------------------------------------------------------------------------------------------
# Tier membership
# ---------------------------------------------------------------------------------------------


def test_every_status_endpoint_is_in_exactly_one_tier() -> None:
    """An endpoint in no tier is never read; one in two tiers is read twice."""
    tiers = Counter([*sch.HOT, *sch.WARM, *sch.COLD, *sch.CENSUS_ONLY])
    assert set(tiers) == set(StatusEndpoint)
    assert all(count == 1 for count in tiers.values())


def test_video_is_the_hot_endpoint() -> None:
    """Routing is the only thing an external controller changes behind Home Assistant's back."""
    assert sch.HOT == (StatusEndpoint.VIDEO,)


def test_network_and_tmds_are_read_once_at_census_only() -> None:
    """The MAC does not change, and TMDS has no status endpoint on the tested firmware."""
    assert set(sch.CENSUS_ONLY) == {StatusEndpoint.NETWORK, StatusEndpoint.TMDS}


# ---------------------------------------------------------------------------------------------
# The cadence, over a full minute
# ---------------------------------------------------------------------------------------------


def test_cadence_over_twelve_ticks() -> None:
    counts = Counter(e for tick in range(12) for e in endpoints_for_tick(tick))
    assert counts[StatusEndpoint.VIDEO] == 12  # every tick
    assert counts[StatusEndpoint.AUDIO] == 6  # every 2nd
    assert counts[StatusEndpoint.INFO] == 6  # every 2nd
    assert counts[StatusEndpoint.SYSTEM] == 1  # every 12th
    assert counts[StatusEndpoint.EDID] == 1
    assert counts[StatusEndpoint.WEB] == 1


def test_no_cold_endpoint_is_starved() -> None:
    """Round-robin, so each cold endpoint must come round within one full cycle."""
    seen = {e for tick in range(12) for e in endpoints_for_tick(tick)}
    assert set(sch.COLD) <= seen


def test_census_only_endpoints_never_appear_on_a_schedule() -> None:
    scheduled = {e for tick in range(240) for e in endpoints_for_tick(tick)}
    assert not scheduled & set(sch.CENSUS_ONLY)


def test_steady_state_request_rate_matches_the_documented_arithmetic() -> None:
    """The web UI polls one endpoint every 5 s, and only while a browser is open.

    Reading all six every tick would be six times that, permanently. The tiers bring it to 2.25
    requests per tick: one hot, one warm (two endpoints alternating still costs a whole request
    per tick), and a quarter of a cold one.

    This assertion is the guard on schedule.py's docstring. If a tier moves, the arithmetic there
    is wrong and this fails.
    """
    ticks = 240  # 20 minutes
    total = sum(len(endpoints_for_tick(tick)) for tick in range(ticks))
    per_tick = total / ticks
    assert per_tick == 2.25
    assert total / (ticks * TICK_SECONDS) == 0.45  # req/s


def test_polling_stays_far_below_what_the_device_was_measured_to_take() -> None:
    """The unit served 30 requests at 10/s with zero failures and a 12.5 ms mean.

    That is the ceiling this cadence has to stay under, and the margin should be large: the
    matrix also serves its own web UI and coexists with a third-party control system.
    """
    measured_safe_rate = 10.0  # req/s, observed
    ticks = 240
    per_second = sum(len(endpoints_for_tick(t)) for t in range(ticks)) / (ticks * TICK_SECONDS)
    assert per_second * 20 < measured_safe_rate


def test_a_tick_never_reads_the_same_endpoint_twice() -> None:
    for tick in range(240):
        due = endpoints_for_tick(tick)
        assert len(due) == len(set(due))


def test_the_schedule_is_a_pure_function_of_the_tick() -> None:
    assert endpoints_for_tick(37) == endpoints_for_tick(37)


# ---------------------------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------------------------


def test_the_first_poll_reads_everything() -> None:
    """Entities are created from what the device reports, so the opening read must be complete."""
    schedule = PollSchedule()
    assert set(schedule.next_endpoints()) == set(StatusEndpoint)
    assert schedule.census_done


def test_subsequent_polls_follow_the_tiers() -> None:
    schedule = PollSchedule()
    schedule.next_endpoints()  # census
    assert set(schedule.next_endpoints()) < set(StatusEndpoint)


def test_a_fresh_schedule_has_not_done_its_census() -> None:
    assert not PollSchedule().census_done


# ---------------------------------------------------------------------------------------------
# Promotion -- so a write is confirmed by the next poll, not twelve ticks later
# ---------------------------------------------------------------------------------------------


def test_a_promoted_endpoint_is_read_on_the_very_next_tick() -> None:
    schedule = PollSchedule()
    schedule.next_endpoints()  # census
    schedule.promote(StatusEndpoint.EDID)  # a cold endpoint, otherwise 12 ticks away
    assert StatusEndpoint.EDID in schedule.next_endpoints()


def test_promotion_does_not_persist_into_the_following_tick() -> None:
    schedule = PollSchedule()
    schedule.next_endpoints()
    schedule.promote(StatusEndpoint.EDID)
    schedule.next_endpoints()
    assert StatusEndpoint.EDID not in schedule.next_endpoints()


def test_promoting_an_already_due_endpoint_does_not_duplicate_it() -> None:
    schedule = PollSchedule()
    schedule.next_endpoints()
    schedule.promote(StatusEndpoint.VIDEO)
    due = schedule.next_endpoints()
    assert due.count(StatusEndpoint.VIDEO) == 1


def test_promotion_before_the_census_is_harmless() -> None:
    schedule = PollSchedule()
    schedule.promote(StatusEndpoint.EDID)
    assert set(schedule.next_endpoints()) == set(StatusEndpoint)


def test_several_promotions_are_all_honoured() -> None:
    schedule = PollSchedule()
    schedule.next_endpoints()
    schedule.promote(StatusEndpoint.EDID)
    schedule.promote(StatusEndpoint.SYSTEM)
    due = schedule.next_endpoints()
    assert {StatusEndpoint.EDID, StatusEndpoint.SYSTEM} <= set(due)


def test_the_tick_counter_advances_once_per_poll() -> None:
    schedule = PollSchedule()
    for expected in range(1, 5):
        schedule.next_endpoints()
        assert schedule.tick == expected
