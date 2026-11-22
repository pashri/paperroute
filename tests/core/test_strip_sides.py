"""Tests for the times of a both-sides strip."""

from dataclasses import replace
from typing import Final

import pytest

from paperroute_sydney.city import SydneyMode
from paperroute_testkit.core.factories import (
    TRAIN,
    inputs_of,
    line_pattern,
    live,
    place,
    train,
)
from paperroute_testkit.core.kit_steps import step
from paperroute_testkit.core.reach_factories import both_reach
from paperroute_testkit.core.strip_factories import PIN, on_view
from paperroute_transit.core.messages import StripLoaded
from paperroute_transit.core.model import Departure
from paperroute_transit.core.reach import RowKind, StripKey
from paperroute_transit.core.saved import LiveView
from paperroute_transit.core.strip_follow import followed_reach, origin_of
from paperroute_transit.core.strip_model import (
    StripInputs,
    StripView,
    trip_day,
)
from paperroute_transit.core.strip_sides import across_times
from paperroute_transit.core.strip_state import shown_key
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
    """A followed From shows the bus from across; rows split by kerb."""
    inputs = laid_out(inputs=sided(across_minutes=3, here_minutes=6))
    assert inputs.reach is not None and inputs.reach.trip is not None
    times = times_by_station(inputs=inputs)
    assert times['C'] == '8:03'
    assert times['B1'] == '8:09'
    assert times['A1'] == '8:06'


def test_marks_time_the_rows_below_by_the_picked_kerb_unfollowed() -> None:
    """Unfollowed, rows below keep the picked kerb's bus's times."""
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
    inputs = sided(across_minutes=3, here_minutes=6, stations=8)
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


def test_strip_scroll_bounds_centre_a_both_sides_from() -> None:
    """The From rests in slot 5, so the scroll runs from -3 to 3.

    The reach has 17 rows with the From in row 8 and the banner leaves
    11 slots, so the From sits at slot 5 when the scroll is 0.
    """
    both = replace(
        PIN, destination=None, kind=SydneyMode.BUS,
        opposite=place('x2', name='Opposite'),
    )
    state, _ = on_view(view=LiveView.STRIP, pin=both)
    state, _ = step(state, StripLoaded(
        key=shown_key(state=state), reach=both_reach(stations=8),
    ))
    assert isinstance(state.last_view, StripView)
    kinds_shown = [row.kind for row in state.last_view.rows]
    assert kinds_shown.index(RowKind.ORIGIN) == 5
    assert state.strip.bounds == (-3, 3)


def with_picked(*, selected: str | None, across: bool = True) -> StripInputs:
    """Make inputs with buses at +3 and +9 (across) and +6, one picked."""
    patterns = {
        'x': line_pattern(trip_id='x', stations=('C', 'A1', 'A2'), minutes=3),
        'y': line_pattern(
            trip_id='y', stations=('C', 'B1', 'B2', 'B3'), minutes=6,
        ),
        'z': line_pattern(trip_id='z', stations=('C', 'A1', 'A2'), minutes=9),
    }
    items = {
        trip: from_across(minutes=minutes, trip_id=trip)
        if across and trip != 'y'
        else train(minutes=minutes, trip_id=trip)
        for trip, minutes in (('x', 3), ('y', 6), ('z', 9))
    }
    inputs = inputs_of(
        reach=both_reach(),
        departures=[live(item=item) for item in items.values()],
        patterns=patterns,
    )
    chosen = None if selected is None else trip_day(
        departure=items[selected],
    )
    return replace(inputs, key=BOTH if across else TRAIN, selected=chosen)


@pytest.mark.parametrize('followed', [False, True])
def test_marks_time_rows_below_when_the_first_bus_from_across_is_picked(
    followed: bool,
) -> None:
    """Picking the first bus from across keeps the rows below timed."""
    inputs = with_picked(selected='x')
    times = times_by_station(
        inputs=laid_out(inputs=inputs) if followed else inputs,
    )
    assert times == {
        'A2': '8:09', 'A1': '8:06', 'C': '8:03',
        'B1': '8:09', 'B2': '8:12', 'B3': '8:15',
    }


@pytest.mark.parametrize('followed', [False, True])
def test_marks_time_rows_above_by_a_later_bus_from_across_picked(
    followed: bool,
) -> None:
    """Picking a later bus from across times the rows above by it."""
    inputs = with_picked(selected='z')
    times = times_by_station(
        inputs=laid_out(inputs=inputs) if followed else inputs,
    )
    assert times == {
        'A2': '8:15', 'A1': '8:12', 'C': '8:09',
        'B1': '8:09', 'B2': '8:12', 'B3': '8:15',
    }


def test_marks_keep_a_single_kerb_from_on_the_followed_bus() -> None:
    """Without a kerb across the From shows the followed bus's time."""
    inputs = with_picked(selected=None, across=False)
    followed = laid_out(inputs=inputs)
    assert followed.reach is not None and followed.reach.trip == 'y'
    assert times_by_station(inputs=followed)['C'] == '8:06'


def test_followed_reach_keeps_the_facing_stations() -> None:
    """Laying the rows below along the next bus keeps the facing map."""
    inputs = sided(across_minutes=3, here_minutes=6)
    assert inputs.reach is not None
    facing = replace(inputs.reach, facing={'P1': 'A1'})
    followed = laid_out(inputs=replace(inputs, reach=facing)).reach
    assert followed is not None and followed.trip == 'y'
    assert followed.facing == {'P1': 'A1'}
