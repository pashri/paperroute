"""Tests for the connection Strip's model."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Final

from paperroute.city import SYDNEY_ZONE
from paperroute.core.chains import Chain
from paperroute.core.connection_strip import (
    LegCall,
    StopMark,
    approach_note,
    build_connection_strip,
    fold_leg,
    leg_calls,
    walk_note,
)
from paperroute.core.live import LiveDeparture
from paperroute.core.stop_view import StopView
from paperroute.core.strip_model import LOADING, NO_LEG_STOPS
from paperroute_testkit.core.connection_factories import (
    LEG1,
    WORK,
    at,
    run,
    work_chains,
)
from paperroute_testkit.core.factories import (
    STALE_LIMIT,
    departure,
    live,
    pattern,
    place,
    snapshot,
    vehicle,
)
from paperroute_transit.core.model import Change, StopPlace
from paperroute_transit.core.patterns import PatternStop, TripPattern

STOP: Final[StopView] = StopView(
    origin='Hollis St Opp Constitution Hill Library',
    destination='Westmead Station',
    clock='7:30',
    hero=None,
    rows=(),
    banner=None,
    empty=None,
    scroll=0,
)
CHAINS: Final[tuple[Chain, ...]] = work_chains()
NAMES: Final[dict[str, str]] = {
    'G2145615': 'Hollis St Opp Constitution Hill Library',
    'G215254': 'Hammers Rd Opp Sherwood St',
    '2145648': 'Constitution Hill, North West Twy',
    '214510': 'Westmead Station',
}


def timed(
    *, stations: Sequence[str], start: tuple[int, int], step: int,
) -> tuple[PatternStop, ...]:
    """
    Named calls a fixed time apart from a clock time.

    Parameters
    ----------
    stations : Sequence[str]
        Station IDs in running order.
    start : tuple[int, int]
        Hour and minute of the first call.
    step : int
        Seconds between calls.

    Returns
    -------
    tuple[PatternStop, ...]
        The calls.
    """
    first = start[0] * 3600 + start[1] * 60
    return tuple(
        PatternStop(
            arrives=first + step * index,
            departs=first + step * index,
            point=None,
            station=station,
            name=NAMES.get(station, f'Stop {station}'),
        )
        for index, station in enumerate(stations)
    )


FIRST_LEG: Final[TripPattern] = pattern(
    trip_id='a0733',
    stops=timed(
        stations=('G2145615', 's1', 's2', 's3', 'G215254'),
        start=(7, 33),
        step=90,
    ),
    days=(at(7, 33).date(),),
)
SECOND_LEG: Final[TripPattern] = pattern(
    trip_id='b0746',
    route='665',
    stops=timed(
        stations=('2145648', 't1', 't2', 't3', 't4', '214510'),
        start=(7, 46),
        step=132,
    ),
    days=(at(7, 46).date(),),
)
PATTERNS: Final[dict[str, TripPattern | None]] = {
    'a0733': FIRST_LEG,
    'b0746': SECOND_LEG,
}


def test_sections_for_change_pair() -> None:
    """A change pair has one section per leg."""
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=CHAINS, selected=0, patterns=PATTERNS,
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    first, second = view.sections
    assert first.title == (
        'Hollis St Opp Constitution Hill Library → Hammers Rd Opp Sherwood St'
    )
    assert first.note is None
    assert first.route == '606'
    assert second.title == (
        'Constitution Hill, North West Twy → Westmead Station'
    )
    assert second.note == 'walk 150 m, 7 min'
    assert second.route == '665'
    for section in view.sections:
        assert section.stops[0].mark is StopMark.FIRST
        assert section.stops[-1].mark is StopMark.LAST
        assert section.empty is None
    assert [stop.time for stop in second.stops] == ['7:46', '', '7:57']
    assert second.stops[1].name == '4 stops'


def test_leg_times_follow_delay() -> None:
    """A late leg 1 shifts its own rows only."""
    late = Chain(
        legs=(live(item=LEG1[0], late=3), CHAINS[0].legs[1]), way=0,
    )
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=(late,), selected=0,
        patterns=PATTERNS,
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    first, second = view.sections
    assert [stop.time for stop in first.stops] == ['7:36', '', '7:42']
    assert second.stops[0].time == '7:46'


def test_leg_arrival_is_the_live_arrival_the_pair_was_made_with() -> None:
    """A leg's last row shows the live arrival, not its departure delay."""
    first = LEG1[0]
    item = replace(
        live(item=first, late=3), arrives=first.arrives + timedelta(minutes=1),
    )
    view = build_connection_strip(
        stop_view=STOP, pin=WORK,
        chains=(Chain(legs=(item, CHAINS[0].legs[1]), way=0),),
        selected=0,
        patterns=PATTERNS,
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    assert [stop.time for stop in view.sections[0].stops] == [
        '7:36', '', '7:40',
    ]


def test_leg_calls_skip_passing() -> None:
    """Passing calls are left out and the ends are kept."""
    stops = list(FIRST_LEG.stops)
    stops[2] = PatternStop(
        arrives=stops[2].arrives, departs=stops[2].departs, point=None,
        station='s2', passes=True,
    )
    trip = pattern(trip_id='a0733', stops=tuple(stops))
    calls = leg_calls(
        item=live(item=LEG1[0]), last=live(item=LEG1[0]), pattern=trip, start='G2145615',
        end='G215254',
        zone=SYDNEY_ZONE,
    )
    assert [stop.station for stop, _ in calls] == [
        'G2145615', 's1', 's3', 'G215254',
    ]


def test_rowless_leg_says_no_stops() -> None:
    """A loaded leg whose trip never reaches its end says so."""
    trip = pattern(trip_id='b0746', route='665', stops=FIRST_LEG.stops)
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=CHAINS, selected=0,
        patterns={'a0733': FIRST_LEG, 'b0746': trip},
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    assert view.sections[1].stops == ()
    assert view.sections[1].empty == NO_LEG_STOPS


def test_unknown_trip_says_no_stops() -> None:
    """A leg whose trip the timetable does not know is not loading."""
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=CHAINS, selected=0,
        patterns={'a0733': FIRST_LEG, 'b0746': None},
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    assert view.sections[1].empty == NO_LEG_STOPS


def test_leg_follows_joined_trip_to_its_end() -> None:
    """A leg whose trip runs on as the next one reaches its end on it."""
    split = len(SECOND_LEG.stops) - 2
    first = replace(
        SECOND_LEG, stops=SECOND_LEG.stops[:split], next_trip='b0746x',
    )
    joined = replace(
        SECOND_LEG, trip_id='b0746x', stops=SECOND_LEG.stops[split - 1:],
    )
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=CHAINS, selected=0,
        patterns={'a0733': FIRST_LEG, 'b0746': first, 'b0746x': joined},
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    second = view.sections[1]
    assert second.empty is None
    assert second.stops[-1].name == 'Westmead Station'


def test_leg_calls_missing_end() -> None:
    """A trip that never reaches the end gives no calls."""
    calls = leg_calls(
        item=live(item=LEG1[0]), last=live(item=LEG1[0]), pattern=FIRST_LEG, start='G2145615',
        end='nowhere',
        zone=SYDNEY_ZONE,
    )
    assert calls == ()


def test_stay_on_ends_at_last_leg_visit() -> None:
    """A stayed-on ride ends where, and when, its last leg arrives."""
    loop = pattern(trip_id='a0733', stops=timed(
        stations=('G2145615', 's1', 'G215254', 's2', 'G215254'),
        start=(7, 33),
        step=60,
    ))
    item = live(item=departure(departs=at(7, 33), ride=2, trip_id='a0733'))
    last = live(
        item=departure(departs=at(7, 36), ride=1, trip_id='a0733'), late=2,
    )
    calls = leg_calls(
        item=item, last=last, pattern=loop, start='G2145615', end='G215254',
        zone=SYDNEY_ZONE,
    )
    assert len(calls) == 5
    assert calls[-1][1] == at(7, 39)
    alone = leg_calls(
        item=item, last=item, pattern=loop, start='G2145615', end='G215254',
        zone=SYDNEY_ZONE,
    )
    assert len(alone) == 3


def calls_at(*, count: int) -> tuple[LegCall, ...]:
    """
    Calls one minute apart from 8:00 at stops named by number.

    Parameters
    ----------
    count : int
        Number of calls.

    Returns
    -------
    tuple[LegCall, ...]
        The calls.
    """
    return tuple(
        (
            PatternStop(
                arrives=0, departs=0, point=None, station=f's{index}',
                name=f'Stop {index}',
            ),
            at(8, index),
        )
        for index in range(count)
    )


def test_leg_folds_middle() -> None:
    """A leg shows its first and last stop and folds the rest."""
    rows = fold_leg(calls=calls_at(count=11), zone=SYDNEY_ZONE)
    assert [(row.name, row.time) for row in rows] == [
        ('Stop 0', '8:00'), ('9 stops', ''), ('Stop 10', '8:10'),
    ]
    assert [row.mark for row in rows] == [
        StopMark.FIRST, StopMark.FOLD, StopMark.LAST,
    ]


def test_short_leg_no_fold() -> None:
    """Two calls need no fold row."""
    rows = fold_leg(calls=calls_at(count=2), zone=SYDNEY_ZONE)
    assert [row.name for row in rows] == ['Stop 0', 'Stop 1']


def test_one_stop_singular() -> None:
    """One stop between reads singular."""
    assert fold_leg(
        calls=calls_at(count=3),
        zone=SYDNEY_ZONE,
    )[1].name == '1 stop'


LEPPINGTON: Final[StopPlace] = place(
    'LEP', name='Leppington Station', point=(400.0, 0.0),
)
T5: Final[TripPattern] = pattern(
    trip_id='t5',
    route='T5',
    stops=tuple(
        PatternStop(
            arrives=28800 + 120 * index,
            departs=28800 + 120 * index,
            point=(100.0 * index, 0.0),
            station=station,
            name=station,
        )
        for index, station in enumerate(('s0', 's1', 's2', 's3', 'LEP'))
    ),
)
T5_LEG: Final[LiveDeparture] = live(item=departure(
    departs=at(8, 8), ride=10, route='T5', trip_id='t5',
))


def test_vehicle_before_board_note() -> None:
    """A vehicle short of the boarding stop counts the stops to it."""
    note = approach_note(
        leg=T5_LEG,
        position=vehicle(timestamp=0, trip_id='t5', point=(150.0, 0.0)),
        board=LEPPINGTON,
        pattern=T5,
        zone=SYDNEY_ZONE,
    )
    assert note == 'T5 3 stops before Leppington Station'
    near = approach_note(
        leg=T5_LEG,
        position=vehicle(timestamp=0, trip_id='t5', point=(350.0, 0.0)),
        board=LEPPINGTON,
        pattern=T5,
        zone=SYDNEY_ZONE,
    )
    assert near == 'T5 1 stop before Leppington Station'


def test_loop_note_counts_from_boarded_visit() -> None:
    """On a loop the note counts to the visit boarded, by its time."""
    loop = replace(T5, stops=tuple(
        replace(stop, station='LEP') if index == 0 else stop
        for index, stop in enumerate(T5.stops)
    ))
    note = approach_note(
        leg=T5_LEG,
        position=vehicle(timestamp=0, trip_id='t5', point=(150.0, 0.0)),
        board=LEPPINGTON,
        pattern=loop,
        zone=SYDNEY_ZONE,
    )
    assert note == 'T5 3 stops before Leppington Station'


def test_no_note_after_boarding() -> None:
    """Past the boarding stop, or with no position, there is no note."""
    past = place('s2', name='s2', point=(200.0, 0.0))
    assert approach_note(
        leg=T5_LEG,
        position=vehicle(timestamp=0, trip_id='t5', point=(350.0, 0.0)),
        board=past,
        pattern=T5,
        zone=SYDNEY_ZONE,
    ) is None
    assert approach_note(
        leg=T5_LEG, position=None, board=LEPPINGTON, pattern=T5,
        zone=SYDNEY_ZONE,
    ) is None
    assert approach_note(
        leg=T5_LEG,
        position=vehicle(timestamp=0, trip_id='t5', point=(150.0, 0.0)),
        board=LEPPINGTON,
        pattern=None,
        zone=SYDNEY_ZONE,
    ) is None


def test_strip_section_carries_vehicle_note() -> None:
    """A heard vehicle of a section's trip sets that section's note."""
    feed = snapshot(
        fetched_at=at(7, 30),
        vehicles=(vehicle(
            timestamp=int(at(7, 30).timestamp()), trip_id='t5',
            point=(150.0, 0.0),
        ),),
    )
    pin = replace(WORK, origin=LEPPINGTON, ways=())
    view = build_connection_strip(
        stop_view=STOP,
        pin=pin,
        chains=(Chain(legs=(T5_LEG,), way=None),),
        selected=0,
        patterns={'t5': T5},
        positions={'f': feed},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    assert view.sections[0].vehicle == (
        'T5 3 stops before Leppington Station'
    )
    quiet = build_connection_strip(
        stop_view=STOP,
        pin=pin,
        chains=(Chain(legs=(T5_LEG,), way=None),),
        selected=0,
        patterns={'t5': T5},
        positions={'f': feed},
        now=at(7, 45),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    assert quiet.sections[0].vehicle is None


def test_stay_pair_one_section() -> None:
    """Staying on gives one section running to the destination."""
    stay = Chain(
        legs=(live(item=run(
            departs=(7, 40), arrives=(8, 5), route='609', trip_id='d0740',
        )),),
        way=None,
    )
    trip = pattern(
        trip_id='d0740',
        route='609',
        stops=timed(
            stations=('G2145615', 's1', 'G215254', '214510'),
            start=(7, 40),
            step=500,
        ),
    )
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=(stay,), selected=0,
        patterns={'d0740': trip},
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    (section,) = view.sections
    assert section.route == '609'
    assert section.title == 'Hollis St Opp Constitution Hill Library → Westmead Station'
    assert section.stops[-1].name == 'Westmead Station'
    assert section.stops[-1].time == '8:05'


def test_missing_pattern_loading() -> None:
    """A leg whose pattern has not loaded shows the loading text."""
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=CHAINS, selected=0,
        patterns={'a0733': FIRST_LEG},
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    second = view.sections[1]
    assert second.stops == ()
    assert second.empty == LOADING


def test_selected_clamped() -> None:
    """The selected pair is clamped to the last pair."""
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=CHAINS, selected=9, patterns={},
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    assert len(view.pairs) == 5
    assert view.selected == 4
    assert view.sections[0].empty == LOADING
    assert view.empty is None


def test_no_pairs_no_sections() -> None:
    """With no pair there are no sections and the empty text is set."""
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, chains=(), selected=0, patterns={},
        positions={},
        now=at(7, 30),
        stale_after=STALE_LIMIT,
        zone=SYDNEY_ZONE,
    )
    assert view.sections == ()
    assert view.selected == 0
    assert view.empty == 'No connections to Westmead Station in the next 3 hours'


def test_change_without_a_walk_says_change() -> None:
    """Changing at one stop, or at stops no distance apart, has no walk."""
    here = place('215020', name='Parramatta Station', point=(0.0, 0.0))
    there = place('2150401', name='Parramatta Station', point=(0.0, 0.0))
    nowhere = place('215099', name='Parramatta Station')
    assert walk_note(change=Change(alight=here, board=here, allow=5)) == (
        'change, 5 min'
    )
    assert walk_note(change=Change(alight=here, board=there, allow=2)) == (
        'change, 2 min'
    )
    assert walk_note(
        change=Change(alight=nowhere, board=here, allow=2),
    ) == 'change, 2 min'
