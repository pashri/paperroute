"""Tests for the times of a both-sides strip."""

from dataclasses import replace

from paperroute_testkit.core.factories import (
    START,
    inputs_of,
    line_pattern,
    live,
    train,
)
from paperroute_testkit.core.reach_factories import reach_of, tail
from paperroute_transit.core.model import Departure
from paperroute_transit.core.reach import Reach, ReachInput, build_reach
from paperroute_transit.core.strip_follow import origin_of
from paperroute_transit.core.strip_model import StripInputs
from paperroute_transit.core.strip_sides import across_times
from paperroute_transit.core.strip_view import marks_of, trips_of


def both_reach() -> Reach:
    """Three stations each way: A3 to A1 above C, B1 to B3 below."""
    here = tail('B1 B2 B3', 6)
    there = tail('A1 A2 A3', 6)
    places = {
        row.place.stop_id: row.place
        for row in reach_of(tails=[here, there]).rows
    }
    return build_reach(data=ReachInput(
        origin=places['C'], tails=(here,), places=places, station_of={},
        across_tails=(there,),
    ))


def from_across(*, minutes: int, trip_id: str) -> Departure:
    """Make a departure from the kerb across."""
    item = train(minutes=minutes, trip_id=trip_id)
    return replace(item, facts=replace(item.facts, across=True))


def sided(*, across_minutes: int, here_minutes: int) -> StripInputs:
    """Make inputs with a bus from each kerb, the Strip not yet laid out."""
    return inputs_of(
        reach=both_reach(),
        departures=[
            live(item=from_across(minutes=across_minutes, trip_id='x')),
            live(item=train(minutes=here_minutes, trip_id='y')),
        ],
        patterns={
            'x': line_pattern(
                trip_id='x', stations=('C', 'A1', 'A2'),
                minutes=across_minutes,
            ),
            'y': line_pattern(
                trip_id='y', stations=('C', 'B1', 'B2', 'B3'),
                minutes=here_minutes,
            ),
        },
    )


def test_across_times_time_the_rows_above_by_the_bus_from_across() -> None:
    """The next bus from across times the rows above the From."""
    inputs = sided(across_minutes=3, here_minutes=6)
    reach = inputs.reach
    assert reach is not None
    found = across_times(trips=trips_of(inputs=inputs), reach=reach)
    assert {reach.rows[i].place.stop_id: t for i, t in found.items()} == {
        'A1': '8:06', 'A2': '8:09',
    }


def test_across_times_are_empty_without_a_bus_from_across() -> None:
    """A strip with no bus from the kerb across times nothing."""
    inputs = inputs_of(
        reach=both_reach(),
        departures=[live(item=train(minutes=6, trip_id='y'))],
        patterns={
            'y': line_pattern(
                trip_id='y', stations=('C', 'B1'), minutes=6,
            ),
        },
    )
    assert inputs.reach is not None
    assert not across_times(
        trips=trips_of(inputs=inputs), reach=inputs.reach,
    )


def test_marks_time_the_from_by_the_next_bus_from_across() -> None:
    """The From shows the bus from across when it leaves first."""
    inputs = sided(across_minutes=3, here_minutes=6)
    assert inputs.reach is not None
    origin = origin_of(reach=inputs.reach)
    times = marks_of(inputs=inputs, reach=inputs.reach).times
    assert origin is not None
    assert times[origin] == '8:03'


def test_marks_time_the_from_by_the_picked_kerb_when_it_leaves_first() -> None:
    """The From shows the picked kerb's bus when it leaves first."""
    inputs = sided(across_minutes=6, here_minutes=3)
    assert inputs.reach is not None
    origin = origin_of(reach=inputs.reach)
    times = marks_of(inputs=inputs, reach=inputs.reach).times
    assert origin is not None
    assert times[origin] == '8:03'
