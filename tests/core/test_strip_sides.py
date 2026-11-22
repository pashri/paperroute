"""Tests for the times of a both-sides strip."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import (
    START,
    TRAIN,
    inputs_of,
    line_pattern,
    live,
    train,
)
from paperroute_testkit.core.reach_factories import both_reach
from paperroute_transit.core.model import Departure
from paperroute_transit.core.reach import RowKind, StripKey
from paperroute_transit.core.strip_follow import followed_reach, origin_of
from paperroute_transit.core.strip_model import StripInputs
from paperroute_transit.core.strip_sides import across_times
from paperroute_transit.core.strip_view import (
    build_strip_pane,
    marks_of,
    strip_rows,
    trips_of,
)


def from_across(*, minutes: int, trip_id: str) -> Departure:
    """Make a departure from the kerb across."""
    item = train(minutes=minutes, trip_id=trip_id)
    return replace(item, facts=replace(item.facts, across=True))


BOTH: Final[StripKey] = replace(TRAIN, across='x2')


def sided(
    *,
    across_minutes: int,
    here_minutes: int,
    loaded: tuple[str, ...] = ('x', 'y'),
    across_stations: tuple[str, ...] = ('C', 'A1', 'A2'),
    stations: int = 3,
) -> StripInputs:
    """Make inputs with a bus from each kerb, the Strip not yet laid out."""
    patterns = {
        'x': line_pattern(
            trip_id='x', stations=across_stations, minutes=across_minutes,
        ),
        'y': line_pattern(
            trip_id='y', stations=('C', 'B1', 'B2', 'B3'),
            minutes=here_minutes,
        ),
    }
    return replace(inputs_of(
        reach=both_reach(stations=stations),
        departures=[
            live(item=from_across(minutes=across_minutes, trip_id='x')),
            live(item=train(minutes=here_minutes, trip_id='y')),
        ],
        patterns={key: patterns[key] for key in loaded},
    ), key=BOTH)


def laid_out(*, inputs: StripInputs) -> StripInputs:
    """Lay the inputs' rows below the From along the next buses."""
    assert inputs.reach is not None
    return replace(inputs, reach=followed_reach(
        reach=inputs.reach, trips=trips_of(inputs=inputs), target=None,
    ))


def times_by_station(*, inputs: StripInputs) -> dict[str, str]:
    """Read the clock time of each station row of the inputs' reach."""
    assert inputs.reach is not None
    shown = marks_of(inputs=inputs, reach=inputs.reach).times
    return {
        inputs.reach.rows[index].place.stop_id: text
        for index, text in shown.items()
    }


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


def test_across_times_map_only_rows_above_the_from() -> None:
    """A bus from across that also calls below the From times only above."""
    inputs = sided(
        across_minutes=3, here_minutes=6,
        across_stations=('C', 'A1', 'B1'),
    )
    reach = inputs.reach
    assert reach is not None
    origin = origin_of(reach=reach)
    found = across_times(trips=trips_of(inputs=inputs), reach=reach)
    assert origin is not None
    assert found
    assert all(index < origin for index in found)


def test_marks_time_a_followed_from_by_the_next_bus_from_across() -> None:
    """On followed rows the From shows the bus from across, rows below
    the picked kerb's bus and rows above the bus from across."""
    inputs = laid_out(inputs=sided(across_minutes=3, here_minutes=6))
    assert inputs.reach is not None and inputs.reach.trip is not None
    times = times_by_station(inputs=inputs)
    assert times['C'] == '8:03'
    assert times['B1'] == '8:09'
    assert times['A1'] == '8:06'


def test_marks_time_the_rows_below_by_the_picked_kerb_unfollowed() -> None:
    """Unfollowed, the bus from across leaves the rows below timed by the
    picked kerb's bus."""
    times = times_by_station(inputs=sided(across_minutes=3, here_minutes=6))
    assert times['B1'] == '8:09'
    assert times['A1'] == '8:06'
    assert times['C'] == '8:03'


def test_followed_runs_pass_over_a_bus_from_across_without_a_pattern() -> None:
    """With only the picked kerb's pattern loaded the rows stay followed."""
    inputs = laid_out(inputs=sided(
        across_minutes=3, here_minutes=6, loaded=('y',),
    ))
    assert inputs.reach is not None and inputs.reach.trip == 'y'
    assert times_by_station(inputs=inputs)['B1'] == '8:09'


def test_strip_rows_put_a_both_sides_from_in_slot_five() -> None:
    """The From rests in the sixth of twelve slots."""
    inputs = sided(across_minutes=3, here_minutes=6, stations=6)
    assert inputs.reach is not None
    rows, _ = strip_rows(inputs=inputs, reach=inputs.reach)
    assert [row.kind for row in rows].index(RowKind.ORIGIN) == 5


def test_build_strip_pane_puts_a_both_sides_from_in_slot_four() -> None:
    """The From rests in slot 4 of nine, and slot 3 under a banner."""
    inputs = sided(across_minutes=3, here_minutes=6, stations=6)
    pane = build_strip_pane(inputs=inputs)
    assert [row.kind for row in pane.rows].index(RowKind.ORIGIN) == 4
    bannered = replace(inputs, stop_view=replace(
        inputs.stop_view, banner='No live data yet',
    ))
    pane = build_strip_pane(inputs=bannered)
    assert [row.kind for row in pane.rows].index(RowKind.ORIGIN) == 3
