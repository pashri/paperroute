"""Tests for turning a positions download into estimated trips."""

from dataclasses import replace
from datetime import date, timedelta
from typing import Final

import pytest

from paperroute_testkit.city_setting import city_under_test
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    pattern,
    snapshot,
    vehicle,
)
from paperroute_transit.core.estimates import estimated_snapshot
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.realtime import (
    EVERY_STOP,
    RealtimeSnapshot,
    StopUpdate,
    TripRealtime,
)

ZONE: Final = city_under_test().zone
LIMIT: Final[timedelta] = timedelta(minutes=2)
FIRST: Final[TripPattern] = replace(pattern(trip_id='t1'), next_trip='t2')
BACK: Final[TripPattern] = pattern(  # 8:15 from (1000, 0) to (0, 0) at 8:25
    trip_id='t2',
    stops=(
        PatternStop(
            arrives=EIGHT + 900, departs=EIGHT + 900, point=(1000.0, 0.0),
        ),
        PatternStop(
            arrives=EIGHT + 1500, departs=EIGHT + 1500, point=(0.0, 0.0),
        ),
    ),
)
BOTH: Final[dict[str, TripPattern | None]] = {'t1': FIRST, 't2': BACK}


def bus(*, x: float, seconds: int, trip_id: str = 't1') -> VehiclePosition:
    """A bus at (x, 0) some seconds after 8:00."""
    when = int((START + timedelta(seconds=seconds)).timestamp())
    return vehicle(timestamp=when, trip_id=trip_id, point=(x, 0.0))


def made(
    *buses: VehiclePosition,
    fetched: int,
    patterns: dict[str, TripPattern | None] = BOTH,
    trip_ids: frozenset[str] = frozenset({'t1', 't2'}),
    limit: timedelta = LIMIT,
    turns: dict[str, str] | None = None,
) -> RealtimeSnapshot:
    """Estimate a download fetched some seconds after 8:00."""
    return estimated_snapshot(
        positions=snapshot(
            fetched_at=START + timedelta(seconds=fetched), vehicles=buses,
        ),
        patterns=patterns,
        shapes={},
        trip_ids=trip_ids,
        limit=limit,
        zone=ZONE,
        turns=turns or {},
    )


def delay(*, trip: TripRealtime) -> int | None:
    """The one delay an estimated trip carries."""
    return trip.updates[0].departure_delay


def test_a_late_bus_gets_an_estimate() -> None:
    """A bus 2 minutes late, heard 30 s before the fetch."""
    found = made(bus(x=500.0, seconds=420), fetched=450)
    assert found.fetched_at == START + timedelta(seconds=450)
    assert found.trips['t1'] == TripRealtime(
        trip_id='t1',
        cancelled=False,
        updates=(
            StopUpdate(
                sequence=EVERY_STOP,
                stop_id=None,
                arrival_delay=None,
                departure_delay=120,
                arrival_time=None,
                departure_time=None,
            ),
        ),
        start_date=START.date(),
        estimated=True,
        uncertain=False,
        heard_at=START + timedelta(seconds=420),
    )


def test_more_than_ten_minutes_late_is_uncertain() -> None:
    """600 s late is certain; 601 s is not."""
    exactly = made(bus(x=500.0, seconds=900), fetched=900)
    over = made(bus(x=500.0, seconds=901), fetched=901)
    assert not exactly.trips['t1'].uncertain
    assert over.trips['t1'].uncertain


def test_more_than_ten_minutes_early_is_uncertain() -> None:
    """600 s early is certain; 601 s is not."""
    exactly = made(bus(x=500.0, seconds=-300), fetched=-300)
    over = made(bus(x=500.0, seconds=-301), fetched=-301)
    assert delay(trip=exactly.trips['t1']) == -600
    assert not exactly.trips['t1'].uncertain
    assert over.trips['t1'].uncertain


def test_within_150_m_of_either_end_is_uncertain() -> None:
    """149 m from the first or last stop is uncertain; 150 m is not."""
    near_start = made(bus(x=149.0, seconds=89), fetched=89)
    clear = made(bus(x=150.0, seconds=90), fetched=90)
    near_end = made(bus(x=851.0, seconds=511), fetched=511)
    assert near_start.trips['t1'].uncertain
    assert not clear.trips['t1'].uncertain
    assert near_end.trips['t1'].uncertain


def test_a_position_older_than_the_limit_gives_no_estimate() -> None:
    """Heard 121 s before the feed's time is too old at a 2 minute limit."""
    assert not made(bus(x=500.0, seconds=479), fetched=600).trips
    assert made(bus(x=500.0, seconds=480), fetched=600).trips


def test_a_blank_start_date_is_taken_as_today() -> None:
    """With no start date the bus runs today; another day's trip is not it."""
    yesterday = START.date() - timedelta(days=1)
    other: dict[str, TripPattern | None] = {
        't1': replace(FIRST, days=(yesterday,)),
    }
    blank = bus(x=500.0, seconds=420)
    assert not made(blank, fetched=420, patterns=other).trips
    dated = replace(blank, start_date=yesterday)
    assert not made(dated, fetched=420).trips


def test_the_freshest_vehicle_on_a_trip_wins() -> None:
    """Of two buses on one trip, the one heard last gives the estimate."""
    found = made(
        bus(x=450.0, seconds=420), bus(x=400.0, seconds=360), fetched=420,
    )
    assert delay(trip=found.trips['t1']) == 150


def test_lateness_carries_into_the_joined_trip_less_its_slack() -> None:
    """7 min late into a 5 min layover starts the next trip 4 min late."""
    found = made(bus(x=500.0, seconds=720), fetched=720)
    assert delay(trip=found.trips['t1']) == 420
    assert not found.trips['t1'].uncertain
    carried = found.trips['t2']
    assert delay(trip=carried) == 240
    assert carried.estimated
    assert carried.uncertain
    assert carried.heard_at == found.trips['t1'].heard_at


def test_a_carried_delay_stops_at_twenty_minutes() -> None:
    """Over 20 minutes still to carry, the next trip starts 20 min late."""
    capped = made(bus(x=500.0, seconds=2100), fetched=2100)
    assert delay(trip=capped.trips['t1']) == 1800
    assert delay(trip=capped.trips['t2']) == 1200
    edge = made(bus(x=500.0, seconds=1680), fetched=1680)
    assert delay(trip=edge.trips['t2']) == 1200
    under = made(bus(x=500.0, seconds=1620), fetched=1620)
    assert delay(trip=under.trips['t2']) == 1140


def test_a_layover_that_absorbs_the_lateness_carries_nothing() -> None:
    """3 min late into a 5 min layover leaves the next trip to its time."""
    assert 't2' not in made(bus(x=500.0, seconds=480), fetched=480).trips


def test_only_a_late_bus_carries_over() -> None:
    """With a 1 min layover, on time carries nothing; 30 s late, 90 s."""
    tight = replace(BACK, stops=tuple(
        replace(stop, arrives=stop.arrives - 240, departs=stop.departs - 240)
        for stop in BACK.stops
    ))
    patterns: dict[str, TripPattern | None] = {'t1': FIRST, 't2': tight}
    on_time = made(bus(x=500.0, seconds=300), fetched=300, patterns=patterns)
    late = made(bus(x=500.0, seconds=330), fetched=330, patterns=patterns)
    assert 't2' not in on_time.trips
    assert delay(trip=late.trips['t2']) == 90


def test_the_next_trips_own_position_replaces_the_carry_over() -> None:
    """Once the next trip's bus is heard on its way, its own estimate wins."""
    found = made(
        bus(x=500.0, seconds=720),
        bus(x=500.0, seconds=1200, trip_id='t2'),
        fetched=1200,
        limit=timedelta(minutes=10),
    )
    assert delay(trip=found.trips['t2']) == 0
    assert not found.trips['t2'].uncertain


def test_a_finished_bus_carries_its_lateness_through_the_layover() -> None:
    """At the last stop at 8:15, 5 min late, the next trip starts 2 min late."""
    found = made(bus(x=990.0, seconds=900), fetched=900)
    assert 't1' not in found.trips
    carried = found.trips['t2']
    assert delay(trip=carried) == 120
    assert carried.uncertain
    assert carried.heard_at == START + timedelta(seconds=900)


def test_a_finished_bus_on_time_carries_nothing() -> None:
    """Arriving on time at 8:10 into a 5 min layover carries nothing."""
    assert not made(bus(x=990.0, seconds=600), fetched=600).trips


def test_a_stale_finished_bus_carries_nothing() -> None:
    """A finished bus heard 121 s before the feed's time carries nothing."""
    assert not made(bus(x=990.0, seconds=839), fetched=960).trips
    assert made(bus(x=990.0, seconds=840), fetched=960).trips


def test_only_the_asked_trips_are_kept() -> None:
    """A trip the board does not show is left out; its carry-over is kept."""
    found = made(
        bus(x=500.0, seconds=720), fetched=720, trip_ids=frozenset({'t2'}),
    )
    assert set(found.trips) == {'t2'}


NIGHT: Final[TripPattern] = pattern(  # 24:00 to 24:10 under yesterday's run
    trip_id='t1',
    stops=(
        PatternStop(arrives=86400, departs=86400, point=(0.0, 0.0)),
        PatternStop(arrives=87000, departs=87000, point=(1000.0, 0.0)),
    ),
    days=(START.date(), START.date() + timedelta(days=1)),
)


def at_midnight(
    *, seconds: int, start: date | None = None,
) -> VehiclePosition:
    """A bus at (500, 0) some seconds after midnight following START's day."""
    midnight = START.replace(hour=0, minute=0) + timedelta(days=1)
    when = int((midnight + timedelta(seconds=seconds)).timestamp())
    return replace(
        vehicle(timestamp=when, point=(500.0, 0.0)), start_date=start,
    )


def test_a_blank_start_date_after_midnight_reads_yesterdays_run() -> None:
    """00:05 on a trip timed 24:05 there is on time, not a day early."""
    found = estimated_snapshot(
        positions=snapshot(
            fetched_at=START + timedelta(days=1, seconds=-28500),
            vehicles=(at_midnight(seconds=300),),
        ),
        patterns={'t1': NIGHT},
        shapes={},
        trip_ids=frozenset({'t1'}),
        limit=LIMIT,
        zone=ZONE,
        turns={},
    )
    trip = found.trips['t1']
    assert delay(trip=trip) == 0
    assert trip.start_date == START.date()


def test_a_blank_start_date_by_day_is_still_read_against_today() -> None:
    """8:07 with both days running is 2 minutes late against today."""
    both = replace(
        FIRST, days=(START.date() - timedelta(days=1), START.date()),
    )
    found = made(
        bus(x=500.0, seconds=420), fetched=420, patterns={'t1': both},
    )
    assert delay(trip=found.trips['t1']) == 120
    assert found.trips['t1'].start_date == START.date()


def test_an_explicit_start_date_is_never_second_guessed() -> None:
    """With today's date given, 00:05 is read against today, a day early."""
    today = START.date() + timedelta(days=1)
    found = estimated_snapshot(
        positions=snapshot(
            fetched_at=START + timedelta(days=1, seconds=-28500),
            vehicles=(at_midnight(seconds=300, start=today),),
        ),
        patterns={'t1': NIGHT},
        shapes={},
        trip_ids=frozenset({'t1'}),
        limit=LIMIT,
        zone=ZONE,
        turns={},
    )
    assert delay(trip=found.trips['t1']) == -86400


@pytest.mark.parametrize(('vehicle_at', 'kept'), [(420, True), (300, False)])
def test_staleness_follows_the_vehicles_timestamp_not_the_fetch(
    vehicle_at: int, kept: bool,
) -> None:
    """With the fetch long after the header, the header-to-fix gap counts."""
    stamp = int((START + timedelta(seconds=vehicle_at)).timestamp())
    positions = PositionsSnapshot(
        fetched_at=START + timedelta(seconds=1000),
        header_time=int((START + timedelta(seconds=450)).timestamp()),
        vehicles=(vehicle(timestamp=stamp, point=(500.0, 0.0)),),
    )
    found = estimated_snapshot(
        positions=positions,
        patterns=BOTH,
        shapes={},
        trip_ids=frozenset({'t1'}),
        limit=LIMIT,
        zone=ZONE,
        turns={},
    )
    assert bool(found.trips) is kept
    if kept:
        assert found.trips['t1'].heard_at == START + timedelta(seconds=970)


TURNED: Final[dict[str, TripPattern | None]] = {
    't1': pattern(trip_id='t1'),
    't2': BACK,
}


def test_carried_trips_carries_through_a_turnaround() -> None:
    """7 min late into a 5 min layover starts the turned trip 4 min late."""
    found = made(
        bus(x=500.0, seconds=720),
        fetched=720,
        patterns=TURNED,
        turns={'t1': 't2'},
    )
    carried = found.trips['t2']
    assert delay(trip=carried) == 240
    assert carried.uncertain


def test_carried_trips_caps_a_turnaround_at_twenty_minutes() -> None:
    """45 min late into a turnaround starts the next trip 20 min late."""
    found = made(
        bus(x=500.0, seconds=2700),
        fetched=2700,
        patterns=TURNED,
        turns={'t1': 't2'},
    )
    assert delay(trip=found.trips['t2']) == 1200


def test_carried_trips_ignores_a_turnaround_absorbed_by_layover() -> None:
    """3 min late into a 5 min layover carries nothing."""
    found = made(
        bus(x=500.0, seconds=480),
        fetched=480,
        patterns=TURNED,
        turns={'t1': 't2'},
    )
    assert 't2' not in found.trips
