"""Tests for the connection Strip's model."""

from collections.abc import Sequence
from typing import Final

from paperroute.core.connection_model import Pair, pairs_of
from paperroute.core.connection_strip import (
    LegStop,
    StopMark,
    build_connection_strip,
    leg_calls,
    shares,
    trimmed,
)
from paperroute.core.patterns import PatternStop, TripPattern
from paperroute.core.stop_view import StopView
from paperroute.core.strip_model import LOADING
from tests.core.connection_factories import LEG1, LEG2, WORK, at, run
from tests.core.factories import live, pattern

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
PAIRS: Final[tuple[Pair, ...]] = pairs_of(
    first=tuple(live(item=item) for item in LEG1),
    second=tuple(live(item=item) for item in LEG2),
    direct=(),
    allow=7,
    now=at(7, 30),
)
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
        stop_view=STOP, pin=WORK, pairs=PAIRS, selected=0, patterns=PATTERNS,
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
    assert [stop.time for stop in second.stops] == [
        '7:46', '7:48', '7:50', '7:52', '7:54', '7:57',
    ]


def test_leg_times_follow_delay() -> None:
    """A late leg 1 shifts its own rows only."""
    late = Pair(first=live(item=LEG1[0], late=3), second=PAIRS[0].second)
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, pairs=(late,), selected=0,
        patterns=PATTERNS,
    )
    first, second = view.sections
    assert [stop.time for stop in first.stops] == [
        '7:36', '7:37', '7:39', '7:40', '7:42',
    ]
    assert second.stops[0].time == '7:46'


def test_leg_calls_skip_passing() -> None:
    """Passing calls are left out and the ends are kept."""
    stops = list(FIRST_LEG.stops)
    stops[2] = PatternStop(
        arrives=stops[2].arrives, departs=stops[2].departs, point=None,
        station='s2', passes=True,
    )
    trip = pattern(trip_id='a0733', stops=tuple(stops))
    calls = leg_calls(
        item=live(item=LEG1[0]), pattern=trip, start='G2145615',
        end='G215254',
    )
    assert [stop.station for stop, _ in calls] == [
        'G2145615', 's1', 's3', 'G215254',
    ]


def test_leg_calls_missing_end() -> None:
    """A trip that never reaches the end gives no calls."""
    calls = leg_calls(
        item=live(item=LEG1[0]), pattern=FIRST_LEG, start='G2145615',
        end='nowhere',
    )
    assert calls == ()


def test_shares_and_fold() -> None:
    """Rows are shared out, and a long leg folds its middle."""
    assert shares(counts=(8, 12)) == (4, 7)
    assert shares(counts=(2, 20)) == (2, 9)
    assert shares(counts=(20, 2)) == (9, 2)
    assert shares(counts=(4, 5)) == (4, 5)
    assert shares(counts=(30,)) == (11,)
    assert shares(counts=(9, 13)) == (5, 6)
    calls = [
        LegStop(name=f'call {index}', time='', mark=StopMark.MIDDLE)
        for index in range(8)
    ]
    kept = trimmed(calls=calls, share=4)
    assert [stop.name for stop in kept] == [
        'call 0', 'call 1', '5 stops', 'call 7',
    ]
    assert kept[2].mark is StopMark.FOLD
    assert trimmed(calls=calls[:3], share=4) == tuple(calls[:3])


def test_stay_pair_one_section() -> None:
    """Staying on gives one section running to the destination."""
    stay = Pair(
        first=live(item=run(
            departs=(7, 40), arrives=(8, 5), route='609', trip_id='d0740',
        )),
        second=None,
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
        stop_view=STOP, pin=WORK, pairs=(stay,), selected=0,
        patterns={'d0740': trip},
    )
    (section,) = view.sections
    assert section.route == '609'
    assert section.title == 'Hollis St Opp Constitution Hill Library → Westmead Station'
    assert section.stops[-1].name == 'Westmead Station'
    assert section.stops[-1].time == '8:05'


def test_missing_pattern_loading() -> None:
    """A leg whose pattern has not loaded shows the loading text."""
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, pairs=PAIRS, selected=0,
        patterns={'a0733': FIRST_LEG},
    )
    second = view.sections[1]
    assert second.stops == ()
    assert second.empty == LOADING


def test_selected_clamped() -> None:
    """The selected pair is clamped to the last pair."""
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, pairs=PAIRS, selected=9, patterns={},
    )
    assert len(view.pairs) == 5
    assert view.selected == 4
    assert view.sections[0].empty == LOADING
    assert view.empty is None


def test_no_pairs_no_sections() -> None:
    """With no pair there are no sections and the empty text is set."""
    view = build_connection_strip(
        stop_view=STOP, pin=WORK, pairs=(), selected=0, patterns={},
    )
    assert view.sections == ()
    assert view.selected == 0
    assert view.empty == 'No connection in the next 3 hours'
