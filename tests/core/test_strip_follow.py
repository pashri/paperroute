"""Tests for laying the rows below a strip's From along a followed train."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.messages import Tick
from paperroute.core.model import CarryOn, StopPlace
from paperroute.core.patterns import TripPattern
from paperroute.core.reach import (
    Reach,
    ReachInput,
    RowKind,
    RunTail,
    build_reach,
)
from paperroute.core.reach_layout import LineExit
from paperroute.core.saved import LiveView
from paperroute.core.state import step
from paperroute.core.state_model import State
from paperroute.core.strip_follow import (
    common_pairs,
    followed_reach,
    origin_of,
)
from paperroute.core.strip_model import StripInputs
from paperroute.core.strip_state import shown_reach
from paperroute.core.strip_view import build_strip_view, marks_of, trips_of
from tests.core.factories import (
    EAST,
    START,
    example_reach,
    inputs_of,
    line_pattern,
    live,
    train,
)
from tests.core.strip_factories import (
    KEY,
    PIN,
    long_reach,
    on_view,
    trains,
    turn,
)

FIXED: Final[tuple[str, ...]] = ('SP', 'ER', 'MV', 'CE', 'TH', 'WY', 'CQ')
CITY: Final[tuple[str, ...]] = (
    'SP', 'ER', 'MV', 'CE', 'MU', 'SJ', 'CQ', 'WY', 'TH',
)
T8: Final[tuple[str, ...]] = ('SY', *CITY)
T2: Final[tuple[str, ...]] = ('TH', 'CE', 'RF', 'LW')
DIRECT: Final[tuple[str, ...]] = ('SY', 'SP', 'ER', 'MV', 'CE', 'TH', 'WY')


def sydenham_reach() -> Reach:
    """
    Build a strip from Sydenham whose fixed line runs Central, Town Hall.

    Returns
    -------
    Reach
        The reach, with Bexley approach rows above, an exit from
        Erskineville to X, and one from Central to Museum.
    """
    keys = ('SY', 'BX', 'TP', *CITY, 'X')
    places = {
        key: StopPlace(stop_id=key, name=f'{key} Station', point=None)
        for key in keys
    }
    return build_reach(data=ReachInput(
        origin=places['SY'],
        tails=(
            RunTail(before=('BX', 'TP'), after=FIXED, trips=10),
            RunTail(before=(), after=CITY, trips=4),
        ),
        places=places,
        station_of={key: key for key in keys},
        exits=(
            LineExit(junction='ER', off='X', lines=('T4',), trips=2),
            LineExit(junction='CE', off='MU', lines=('T8',), trips=4),
        ),
    ))


def joined(*, trip_id: str, stations: tuple[str, ...], minutes: int,
           next_trip: str | None) -> TripPattern:
    """
    Make a line pattern that runs on through as another trip.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    stations : tuple[str, ...]
        Stations in order.
    minutes : int
        Minutes after 8:00 at the first station.
    next_trip : str | None
        The trip it runs on through as.

    Returns
    -------
    TripPattern
        The pattern.
    """
    return replace(
        line_pattern(trip_id=trip_id, stations=stations, minutes=minutes),
        next_trip=next_trip,
    )


def sydenham(*, destination: str | None = 'WY',
             patterns: dict[str, TripPattern | None] | None = None
             ) -> StripInputs:
    """
    Make inputs for Sydenham with a short train first, then a T8 to T2.

    Parameters
    ----------
    destination : str | None
        The Destination.
    patterns : dict[str, TripPattern | None] | None
        Patterns in place of the usual ones.

    Returns
    -------
    StripInputs
        A train 's' at 8:01 to Erskineville only, a train 'a' at 8:03
        round the city to Town Hall, running on as 'b' west via Central.
    """
    departures: list[LiveDeparture] = [
        live(item=train(minutes=1, trip_id='s')),
        live(item=train(minutes=3, trip_id='a')),
    ]
    usual: dict[str, TripPattern | None] = {
        's': joined(
            trip_id='s', stations=('SY', 'SP', 'ER'), minutes=1,
            next_trip=None,
        ),
        'a': joined(trip_id='a', stations=T8, minutes=3, next_trip='b'),
        'b': joined(trip_id='b', stations=T2, minutes=30, next_trip=None),
    }
    return inputs_of(
        reach=sydenham_reach(),
        departures=departures,
        patterns=usual if patterns is None else patterns,
        destination=destination,
    )


def followed(*, inputs: StripInputs) -> StripInputs:
    """
    Lay the inputs' rows below the From along their followed train.

    Parameters
    ----------
    inputs : StripInputs
        The inputs.

    Returns
    -------
    StripInputs
        The inputs with the followed reach.
    """
    assert inputs.reach is not None
    reach = followed_reach(
        reach=inputs.reach,
        trips=trips_of(inputs=inputs),
        target=inputs.destination,
    )
    return replace(inputs, reach=reach)


def below(*, reach: Reach | None) -> list[str]:
    """
    List the station IDs of the rows below the From.

    Parameters
    ----------
    reach : Reach | None
        The reach.

    Returns
    -------
    list[str]
        IDs in row order, exits marked with a leading '>'.
    """
    assert reach is not None
    origin = origin_of(reach=reach)
    assert origin is not None
    return [
        ('>' if row.kind is RowKind.EXIT else '') + row.place.stop_id
        for row in reach.rows[origin + 1:]
    ]


def test_rows_below_the_from_follow_the_next_train_to_the_destination(
) -> None:
    """Sydenham to Wynyard follows the T8 round the city and on as a T2."""
    inputs = followed(inputs=sydenham())
    assert below(reach=inputs.reach) == [
        'SP', 'ER', '>X', 'MV', 'CE', 'MU', 'SJ', 'CQ', 'WY', 'TH',
        'CE', 'RF', 'LW',
    ]
    view = build_strip_view(inputs=replace(inputs, scroll=4))
    lower = [row for row in view.rows if not row.name.startswith('X')]
    assert all(row.time for row in lower if row.station != 'SY')


def test_every_row_below_the_from_has_the_trains_time() -> None:
    """Each followed call's row has that call's time, a loop's both."""
    inputs = followed(inputs=sydenham())
    reach = inputs.reach
    assert reach is not None
    shown = marks_of(inputs=inputs, reach=reach).times
    origin = origin_of(reach=reach)
    assert origin is not None
    assert [
        (row.place.stop_id, shown.get(index, ''))
        for index, row in enumerate(reach.rows)
        if index > origin and row.kind is RowKind.MAIN
    ] == [
        ('SP', '8:06'), ('ER', '8:09'), ('MV', '8:12'), ('CE', '8:15'),
        ('MU', '8:18'), ('SJ', '8:21'), ('CQ', '8:24'), ('WY', '8:27'),
        ('TH', '8:30'), ('CE', '8:33'), ('RF', '8:36'), ('LW', '8:39'),
    ]
    assert shown[origin] == '8:03'


def test_the_destination_disc_sits_on_its_row() -> None:
    """The Destination row is bold and the ride runs to it."""
    inputs = followed(inputs=sydenham())
    view = build_strip_view(inputs=inputs)
    wynyard = [row for row in view.rows if row.station == 'WY']
    assert wynyard and wynyard[0].bold and wynyard[0].ride


def test_a_direct_pin_follows_its_train_past_the_destination() -> None:
    """Past Central the rows are the direct train's remaining stops."""
    patterns: dict[str, TripPattern | None] = {
        'd': joined(trip_id='d', stations=DIRECT, minutes=1, next_trip=None),
    }
    inputs = replace(
        sydenham(destination='CE', patterns=patterns),
        departures=(live(item=train(minutes=1, trip_id='d')),),
    )
    assert below(reach=followed(inputs=inputs).reach) == [
        'SP', 'ER', '>X', 'MV', 'CE', '>MU', 'TH', 'WY',
    ]


def test_no_destination_merges_the_next_departures() -> None:
    """With no To the next trains run on past the first one's end."""
    assert below(reach=followed(inputs=sydenham(destination=None)).reach) == [
        'SP', 'ER', '>X', 'MV', 'CE', 'MU', 'SJ', 'CQ', 'WY', 'TH', 'CE',
        'RF', 'LW',
    ]


def test_fixed_rows_until_the_patterns_load() -> None:
    """With no patterns loaded the fixed rows stay."""
    inputs = sydenham(destination=None, patterns={})
    assert followed(inputs=inputs).reach == inputs.reach
    inputs = sydenham(patterns={})
    assert followed(inputs=inputs).reach == inputs.reach


def test_without_joins_only_the_boarded_trip_is_followed() -> None:
    """A timetable with no trip joins ends the rows at the trip's end."""
    patterns: dict[str, TripPattern | None] = {
        'a': joined(trip_id='a', stations=T8, minutes=3, next_trip=None),
    }
    assert below(reach=followed(
        inputs=sydenham(patterns=patterns),
    ).reach) == ['SP', 'ER', '>X', 'MV', 'CE', 'MU', 'SJ', 'CQ', 'WY', 'TH']


def test_a_train_leaving_up_the_strip_is_not_followed() -> None:
    """On a two-sided strip the next train down the strip is followed."""
    inputs = inputs_of(
        reach=long_reach(),
        departures=[
            live(item=train(minutes=1, trip_id='u')),
            live(item=train(minutes=2, trip_id='d')),
        ],
        patterns={
            'u': line_pattern(
                trip_id='u', stations=('S20', 'S19', 'S18'), minutes=1,
            ),
            'd': line_pattern(
                trip_id='d', stations=('S20', 'S21', 'S30'), minutes=2,
            ),
        },
    )
    reach = followed(inputs=inputs).reach
    assert below(reach=reach) == ['S21', 'S30']
    assert reach is not None and reach.trip == 'd'


def test_the_shown_reach_follows_the_next_train() -> None:
    """The Strip's reach in state follows the next train once loaded."""
    state, _ = on_view(
        view=LiveView.STRIP,
        pin=replace(PIN, destination=None),
        departures=trains(count=6),
    )
    state = replace(
        state,
        strip=replace(state.strip, reaches={KEY: example_reach()}),
        map=replace(state.map, patterns={
            'train-0': line_pattern(
                trip_id='train-0', stations=EAST, minutes=5,
            ),
        }),
    )
    reach = shown_reach(state=state)
    assert reach is not None and reach.trip == 'train-0'
    assert below(reach=reach) == ['E1', 'E2']


def test_a_reach_without_a_from_is_left_alone() -> None:
    """A towards line with no From row has nothing to follow."""
    inputs = sydenham()
    assert inputs.reach is not None
    bare = replace(inputs.reach, rows=inputs.reach.rows[:1])
    assert followed(inputs=replace(inputs, reach=bare)).reach == bare


def test_no_times_below_the_from_once_the_train_is_gone() -> None:
    """Rows laid out for a train no longer listed have no times."""
    inputs = followed(inputs=sydenham())
    gone = replace(inputs, departures=inputs.departures[:1])
    reach = gone.reach
    assert reach is not None
    origin = origin_of(reach=reach)
    assert origin is not None
    shown = marks_of(inputs=gone, reach=reach).times
    assert not [index for index in shown if index > origin]


def test_rows_skip_run_through_calls_and_repeat_platforms() -> None:
    """A call with no stopping, or a second platform, gets no row."""
    base = joined(trip_id='a', stations=T8, minutes=3, next_trip=None)
    stops = list(base.stops)
    stops[3] = replace(stops[3], passes=True)
    stops.insert(5, replace(stops[4], station='CE'))
    stops.insert(1, replace(stops[0], station='SY'))
    inputs = sydenham(patterns={'a': replace(base, stops=tuple(stops))})
    assert below(reach=followed(inputs=inputs).reach) == [
        'SP', 'ER', '>X', 'CE', 'MU', 'SJ', 'CQ', 'WY', 'TH',
    ]


def line_state(*, scroll: int) -> State:
    """
    Make a Strip state on a long line, an all-stops train first.

    Parameters
    ----------
    scroll : int
        Detents the knob turns.

    Returns
    -------
    State
        Trains from C five minutes apart from 8:05: all stops to L29,
        four expresses to L29, then all stops again; at 8:00, scrolled.
    """
    line = tuple(f'L{k}' for k in range(30))
    places = {
        key: StopPlace(stop_id=key, name=key, point=None)
        for key in ('C', *line)
    }
    reach = build_reach(data=ReachInput(
        origin=places['C'],
        tails=(RunTail(before=(), after=line, trips=5),),
        places=places,
        station_of={key: key for key in places},
    ))
    state, _ = on_view(
        view=LiveView.STRIP,
        pin=replace(PIN, destination=None),
        departures=trains(count=6),
    )
    state = replace(
        state,
        strip=replace(state.strip, reaches={KEY: reach}),
        map=replace(state.map, patterns={
            'train-0': line_pattern(
                trip_id='train-0', stations=('C', *line), minutes=5,
            ),
            **{
                f'train-{k}': line_pattern(
                    trip_id=f'train-{k}', stations=('C', 'L29'),
                    minutes=5 + 5 * k,
                )
                for k in range(1, 5)
            },
            'train-5': line_pattern(
                trip_id='train-5', stations=('C', *line), minutes=30,
            ),
        }),
    )
    state, _ = step(state, Tick(now=START))
    state, _ = step(state, turn(steps=scroll))
    return state


def test_a_scroll_survives_the_followed_train_changing() -> None:
    """An express's short rows hold the scroll back, not lose it."""
    state = line_state(scroll=6)
    assert state.strip.scroll == 6
    state, _ = step(state, Tick(now=START + timedelta(minutes=6)))
    assert state.strip.scroll < 6
    state, _ = step(state, Tick(now=START + timedelta(minutes=26)))
    assert state.strip.scroll == 6


def carrying(*, item: LiveDeparture) -> LiveDeparture:
    """
    Make a departure's vehicle carry on as a T2 to Leppington.

    Parameters
    ----------
    item : LiveDeparture
        The departure.

    Returns
    -------
    LiveDeparture
        It, with its next run set.
    """
    departure = item.departure
    return replace(item, departure=replace(departure, facts=replace(
        departure.facts,
        then=CarryOn(route='T2', headsign='Leppington', trip_id='b'),
    )))


def test_the_carry_note_is_the_followed_trains() -> None:
    """Another train's change is hidden; the followed one's shows."""
    inputs = sydenham()
    short, long = inputs.departures
    other = followed(inputs=replace(
        inputs, departures=(carrying(item=short), long),
    ))
    assert other.reach is not None
    assert marks_of(inputs=other, reach=other.reach).note is None
    own = followed(inputs=replace(
        inputs, departures=(short, carrying(item=long)),
    ))
    reach = own.reach
    assert reach is not None
    note = marks_of(inputs=own, reach=reach).note
    assert note is not None
    origin = origin_of(reach=reach)
    assert origin is not None
    assert reach.rows[note[0]].place.stop_id == 'TH' and note[0] > origin
    assert note[1].then == 'T2 Leppington'


def test_an_overshoot_is_kept_to_the_rows_shown() -> None:
    """Turning past the express's rows asks for no more than they show."""
    state = line_state(scroll=0)
    state, _ = step(state, Tick(now=START + timedelta(minutes=6)))
    shown = state.strip.scroll
    state, _ = step(state, turn(steps=20))
    assert state.strip.asked == state.strip.scroll >= shown
    held = state.strip.asked
    state, _ = step(state, Tick(now=START + timedelta(minutes=26)))
    assert state.strip.scroll == held


def test_a_turn_before_the_rows_load_is_kept() -> None:
    """With no reach yet the knob's turn waits for the rows to bound it."""
    state, _ = on_view(
        view=LiveView.STRIP, pin=replace(PIN, destination=None),
    )
    state, _ = step(state, turn(steps=3))
    assert state.strip.asked == 3
    loaded = line_state(scroll=0)
    state = replace(state, strip=replace(
        state.strip, reaches=loaded.strip.reaches,
    ), map=loaded.map)
    state, _ = step(state, Tick(now=START))
    assert state.strip.scroll == 3


WEST_LINE: Final[tuple[str, ...]] = (
    'HP', 'GR', 'AU', 'LI', 'FL', 'HB', 'SF', 'RF', 'CE',
)
EXPRESS: Final[tuple[str, ...]] = ('PA', 'SF', 'RF', 'CE', 'TH')
ALL_STOPS: Final[tuple[str, ...]] = ('PA', *WEST_LINE, 'MU')


def parramatta(*, express: int, stopping: int,
               destination: str | None = 'CE') -> StripInputs:
    """
    Make inputs for Parramatta with a T1 express and a T2 all stops.

    Parameters
    ----------
    express : int
        Minutes after 8:00 the express 'x' leaves, to Town Hall.
    stopping : int
        Minutes after 8:00 the all-stops 'y' leaves, to Museum.
    destination : str | None
        The Destination.

    Returns
    -------
    StripInputs
        The inputs on a strip whose fixed line is the all-stops one.
    """
    keys = (*ALL_STOPS, 'TH')
    places = {
        key: StopPlace(stop_id=key, name=key, point=None) for key in keys
    }
    reach = build_reach(data=ReachInput(
        origin=places['PA'],
        tails=(RunTail(before=(), after=ALL_STOPS[1:], trips=5),),
        places=places,
        station_of={key: key for key in keys},
    ))
    return inputs_of(
        reach=reach,
        departures=[
            live(item=train(minutes=express, trip_id='x')),
            live(item=train(minutes=stopping, trip_id='y')),
        ],
        patterns={
            'x': line_pattern(trip_id='x', stations=EXPRESS, minutes=express),
            'y': line_pattern(
                trip_id='y', stations=ALL_STOPS, minutes=stopping,
            ),
        },
        destination=destination,
    )


def row_times(*, inputs: StripInputs) -> list[tuple[str, str]]:
    """
    Read the stations and times of the rows below the From.

    Parameters
    ----------
    inputs : StripInputs
        The inputs, rows not yet laid out.

    Returns
    -------
    list[tuple[str, str]]
        Station and time of each line row below the From.
    """
    laid = followed(inputs=inputs)
    reach = laid.reach
    assert reach is not None
    origin = origin_of(reach=reach)
    assert origin is not None
    shown = marks_of(inputs=laid, reach=reach).times
    return [
        (row.place.stop_id, shown.get(index, ''))
        for index, row in enumerate(reach.rows)
        if index > origin and row.kind is RowKind.MAIN
    ]


def test_rows_merge_an_express_and_an_all_stops_train() -> None:
    """With the express next only its stops have times."""
    assert row_times(inputs=parramatta(express=1, stopping=3))[:9] == [
        ('HP', ''), ('GR', ''), ('AU', ''), ('LI', ''), ('FL', ''),
        ('HB', ''), ('SF', '8:04'), ('RF', '8:07'), ('CE', '8:10'),
    ]


def test_every_row_is_timed_once_the_express_has_gone() -> None:
    """After the express leaves the all-stops train times every row."""
    inputs = parramatta(express=1, stopping=3)
    gone = replace(inputs, now=START + timedelta(minutes=2))
    found = row_times(inputs=gone)
    assert all(time for _, time in found)
    assert [s for s, _ in found][:9] == [
        s for s, _ in row_times(inputs=inputs)
    ][:9]


def test_rows_stay_put_whichever_train_is_next() -> None:
    """The layout is the same with the express or the all stops first."""
    first = followed(inputs=parramatta(express=1, stopping=3))
    second = followed(inputs=parramatta(express=3, stopping=1))
    assert below(reach=first.reach)[:-1] == below(reach=second.reach)[:-1]


def test_rows_past_the_destination_follow_the_next_train() -> None:
    """Past Central the rows go the next train's way only."""
    assert below(reach=followed(
        inputs=parramatta(express=1, stopping=3),
    ).reach)[-1] == 'TH'
    assert below(reach=followed(
        inputs=parramatta(express=3, stopping=1),
    ).reach)[-1] == 'MU'


def test_the_note_is_the_next_trains_own() -> None:
    """The express's change shows; the all-stops one's does not."""
    inputs = parramatta(express=1, stopping=3)
    express, stopping = inputs.departures
    patterns = dict(inputs.patterns)
    patterns['y'] = line_pattern(
        trip_id='y', stations=ALL_STOPS[:-1], minutes=3,
    )
    late = followed(inputs=replace(
        inputs, departures=(express, carrying(item=stopping)),
        patterns=patterns,
    ))
    assert late.reach is not None
    assert marks_of(inputs=late, reach=late.reach).note is None
    early = followed(inputs=replace(
        inputs, departures=(carrying(item=express), stopping),
    ))
    reach = early.reach
    assert reach is not None
    note = marks_of(inputs=early, reach=reach).note
    assert note is not None and reach.rows[note[0]].place.stop_id == 'TH'


def test_a_stop_only_a_skipping_train_shares_falls_in_place() -> None:
    """A third train stopping only at Granville and Central keeps the
    line's order, with the express next."""
    inputs = parramatta(express=1, stopping=3)
    skipping = live(item=train(minutes=2, trip_id='z'))
    inputs = replace(
        inputs,
        departures=(*inputs.departures, skipping),
        patterns={
            **inputs.patterns,
            'z': line_pattern(
                trip_id='z', stations=('PA', 'GR', 'CE'), minutes=2,
            ),
        },
    )
    assert below(reach=followed(inputs=inputs).reach) == [
        *WEST_LINE, 'TH',
    ]


def test_common_pairs_keep_both_orders() -> None:
    """Shared stations are matched in order, either side skipping."""
    assert common_pairs(first='ABCE', second='XBYE') == [(1, 1), (3, 3)]
    assert common_pairs(first='AE', second='BCDE') == [(1, 3)]


def test_rows_above_the_from_show_only_the_shown_train() -> None:
    """Above the From only the next train's calls still to come have times."""
    inputs = inputs_of(
        reach=example_reach(),
        departures=[
            live(item=train(minutes=0, trip_id='e')),
            live(item=train(minutes=1, trip_id='w')),
        ],
        patterns={
            'e': line_pattern(
                trip_id='e',
                stations=('W2', 'W1', 'J', 'C', 'E1', 'E2'),
                minutes=-9,
            ),
            'w': line_pattern(
                trip_id='w', stations=('C', 'J', 'W1', 'W2'), minutes=1,
            ),
        },
        now=START - timedelta(minutes=4),
    )
    laid = followed(inputs=inputs)
    reach = laid.reach
    assert reach is not None
    times = marks_of(inputs=laid, reach=reach).times
    above = {
        row.place.stop_id: times.get(index, '')
        for index, row in enumerate(reach.rows)
        if row.above
    }
    assert above == {
        'W2': '', 'W1': '', 'J': '7:57', 'R1': '', 'R2': '', 'R3': '',
    }
