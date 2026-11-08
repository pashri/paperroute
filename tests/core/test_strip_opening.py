"""Tests for opening a strip's folds as they reach the screen."""

from dataclasses import replace
from datetime import timedelta
from typing import Final

import pytest

from paperroute.city import SYDNEY, SydneyMode
from paperroute_hardware.core.knob_events import Knob, KnobPressed, KnobTurned
from paperroute_testkit.core.factories import (
    START,
    begun,
    inputs_of,
    line_pattern,
    live,
    place,
    train,
)
from paperroute_testkit.core.reach_factories import (
    BLACKTOWN,
    REAL_DB,
    STRATHFIELD,
    TOWN_HALL,
    has_runs,
    train_reach,
)
from paperroute_testkit.core.strip_factories import (
    KEY,
    PIN,
    on_view,
    run,
    turn,
)
from paperroute_transit.core.chooser import Stage, to_row, turned_to
from paperroute_transit.core.map_view import DEFAULT_ZOOM
from paperroute_transit.core.messages import StripLoaded, TimetableSeen
from paperroute_transit.core.model import Pin
from paperroute_transit.core.reach import (
    Reach,
    ReachInput,
    ReachRow,
    RowKind,
    RunTail,
    build_reach,
)
from paperroute_transit.core.saved import LiveView, Saved, default_settings
from paperroute_transit.core.state_model import State
from paperroute_transit.core.strip_folds import FOLD_MIN, folded
from paperroute_transit.core.strip_follow import origin_of
from paperroute_transit.core.strip_model import (
    OPEN_TAG,
    StripMapView,
    StripView,
)
from paperroute_transit.core.strip_view import (
    build_strip_view,
    home_of,
    window_of,
)
from tests.core.chooser_walks import CHOSEN_KEY, DOWN, TO_STAGE, at_to

PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
TOP_PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.TOP)
TOP_TURN: Final[KnobTurned] = KnobTurned(knob=Knob.TOP, steps=1)
QUIET: Final[int] = 2  # trips into a quiet station
BUSY: Final[int] = 100  # trips into a busy one
CENTRAL: Final[str] = '200060'


def fold_reach(*, origin: str = 'C', size: int = 40, at: int = 20,
               quiet: int = 6) -> Reach:
    """
    Build a straight reach whose far ends are quiet.

    Parameters
    ----------
    origin : str
        The From's ID.
    size : int
        Rows.
    at : int
        The From row.
    quiet : int
        Quiet stations at each far end.

    Returns
    -------
    Reach
        Rows S0 to S<size - 1> with the From at row `at`; the first and
        last `quiet` rows fold.
    """
    return Reach(
        rows=tuple(
            ReachRow(
                place=place(origin if i == at else f'S{i}', name=(
                    origin if i == at else f'S{i}'
                )),
                kind=RowKind.ORIGIN if i == at else RowKind.MAIN,
                depth=0,
                above=i < at,
                parent=None if i == at else (i + 1 if i < at else i - 1),
                trips=0 if i == at else (
                    QUIET
                    if i < min(quiet, at) or i >= max(size - quiet, at + 1)
                    else BUSY
                ),
            )
            for i in range(size)
        ),
        stations=size - 1,
        station_of={},
    )


def shown_kinds(*, state: State) -> list[RowKind]:
    """
    List the kinds of the rows on screen.

    Parameters
    ----------
    state : State
        State drawn on a strip.

    Returns
    -------
    list[RowKind]
        Row kinds, top to bottom.
    """
    view = state.last_view
    if isinstance(view, StripMapView):
        return [row.kind for row in view.pane.rows]
    assert isinstance(view, StripView)
    return [row.kind for row in view.rows]


def names(*, state: State) -> list[str]:
    """
    Name the Strip's rows on screen.

    Parameters
    ----------
    state : State
        State drawn on the Strip.

    Returns
    -------
    list[str]
        Names, top to bottom.
    """
    assert isinstance(state.last_view, StripView)
    return [row.name for row in state.last_view.rows]


def on_fold_strip() -> State:
    """
    Show a Strip with no Destination over a reach with folds.

    Returns
    -------
    State
        State on the Strip at rest.
    """
    pin = replace(PIN, destination=None)
    state, _ = on_view(view=LiveView.STRIP, pin=pin)
    state, _ = run(
        state=state, events=[StripLoaded(key=KEY, reach=fold_reach())],
    )
    return state


def test_folds_stay_shut_until_they_reach_the_screen() -> None:
    """At rest the far ends are folded and nothing is open."""
    state = on_fold_strip()
    assert state.strip.opened == frozenset()
    assert RowKind.FOLD not in shown_kinds(state=state)


def test_scrolling_onto_a_fold_opens_it_in_place() -> None:
    """Turning up onto a fold opens it, the rows on screen staying put."""
    state = on_fold_strip()
    for _ in range(30):
        top = names(state=state)[0]
        state, _ = run(state=state, events=[turn(steps=-1)])
        assert RowKind.FOLD not in shown_kinds(state=state)
        if top != 'S0':
            assert names(state=state)[0] == f'S{int(top[1:]) - 1}'
    assert state.strip.opened == {'S5'}
    assert names(state=state)[0] == 'S0'


def test_leaving_the_strip_shuts_its_folds() -> None:
    """A view change shuts the opened folds."""
    state = on_fold_strip()
    state, _ = run(state=state, events=[turn(steps=-30)])
    assert state.strip.opened
    state, _ = run(state=state, events=[PRESS])
    assert state.view is LiveView.STRIP_MAP
    assert state.strip.opened == frozenset()
    assert RowKind.FOLD not in shown_kinds(state=state)


def test_to_cursor_stops_on_a_closed_fold() -> None:
    """The TO cursor may rest on a closed fold, the last row here."""
    reach = folded(reach=fold_reach(origin='S2', at=0, size=12, quiet=4))
    chooser = turned_to(chooser=at_to(), steps=12, reach=reach)
    origin = origin_of(reach=reach)
    assert origin is not None
    row = to_row(chooser=chooser, reach=reach, origin=origin)
    assert reach.rows[row].kind is RowKind.FOLD


def mid_fold_reach(*, origin: str = 'C') -> Reach:
    """
    Build a reach with a quiet spur part way down and a quiet far end.

    Parameters
    ----------
    origin : str
        The From's ID.

    Returns
    -------
    Reach
        The From, A1..A20 below it with spur Q1..Q4 at A3 and the far
        end Z1..Z4 after A20; both quiet ends fold.
    """
    line = ' '.join(f'A{k}' for k in range(1, 21))
    keys = {origin, *line.split(), 'Q1', 'Q2', 'Q3', 'Q4'} | {
        f'Z{k}' for k in range(1, 5)
    }
    places = {key: place(key, name=key) for key in keys}
    return build_reach(data=ReachInput(
        origin=places[origin],
        tails=(
            RunTail(before=(), after=tuple(line.split()), trips=100),
            RunTail(before=(), after=('A1', 'A2', 'A3', 'Q1', 'Q2', 'Q3',
                                      'Q4'), trips=2),
            RunTail(before=(), after=(*line.split(), 'Z1', 'Z2', 'Z3',
                                      'Z4'), trips=2),
        ),
        places=places,
        station_of={key: key for key in keys},
    ))


def test_a_fold_with_rows_beyond_stays_shut_when_scrolled_past() -> None:
    """The spur fold stays shut on the Strip; the far end opens."""
    pin = replace(PIN, destination=None)
    state, _ = on_view(view=LiveView.STRIP, pin=pin)
    state, _ = run(
        state=state, events=[StripLoaded(key=KEY, reach=mid_fold_reach())],
    )
    seen = set()
    for _ in range(30):
        state, _ = run(state=state, events=[turn(steps=1)])
        assert isinstance(state.last_view, StripView)
        seen |= {row.name for row in state.last_view.rows}
        assert 'Q1' not in state.strip.opened
    assert 'Q1 … Q4 +4' in seen
    assert state.strip.opened == {'Z1'}
    assert names(state=state)[-1] == 'Z4'


def test_press_on_a_fold_opens_it_without_picking() -> None:
    """In the TO stage a press on a fold opens it, cursor on its first."""
    state, _ = on_view(view=LiveView.STOP, pin=replace(PIN, kind=None))
    state, _ = run(state=state, events=[
        *TO_STAGE,
        StripLoaded(key=CHOSEN_KEY, reach=mid_fold_reach(origin='S2')),
        DOWN, DOWN, DOWN, DOWN,
    ])
    view = state.last_view
    assert isinstance(view, StripView)
    assert next(row for row in view.rows if row.cursor).kind is RowKind.FOLD
    state, _ = run(state=state, events=[PRESS])
    assert state.chooser is not None
    assert state.strip.opened == {'Q1'}
    view = state.last_view
    assert isinstance(view, StripView)
    assert next(row for row in view.rows if row.cursor).name == 'Q1'


def test_to_stage_opens_folds_before_the_cursor_reaches_them() -> None:
    """The TO screen follows the cursor and opens a fold as it shows."""
    state, _ = on_view(view=LiveView.STOP, pin=replace(PIN, kind=None))
    state, _ = run(state=state, events=[
        *TO_STAGE,
        StripLoaded(
            key=CHOSEN_KEY, reach=fold_reach(origin='S2', at=0, size=40),
        ),
    ])
    assert state.strip.opened == frozenset()
    for _ in range(40):
        state, _ = run(state=state, events=[DOWN])
        assert RowKind.FOLD not in shown_kinds(state=state)
    assert state.strip.opened == {'S34'}
    assert state.chooser is not None
    assert state.chooser.to_cursor == 39


@pytest.mark.skipif(
    not has_runs(path=REAL_DB),
    reason='needs data/timetable.db with run tables',
)
def test_central_opens_with_the_quiet_far_ends_folded() -> None:
    """Central opens 133 rows above FROM; Strathfield is 53 turns up."""
    reach, _ = train_reach(station=CENTRAL)
    shown = folded(reach=reach)
    assert origin_of(reach=shown) == 133
    assert [row.place.name for row in shown.rows][0] == (
        'Lapstone … Dubbo +40'
    )
    assert sum(row.kind is RowKind.FOLD for row in shown.rows) == 4
    home = home_of(
        reach=shown, destination=None, rows=window_of(banner=False).rows,
    )
    turns = home - [row.place.stop_id for row in shown.rows].index(
        STRATHFIELD,
    )
    assert turns == 53


@pytest.mark.skipif(
    not has_runs(path=REAL_DB),
    reason='needs data/timetable.db with run tables',
)
def test_town_hall_and_blacktown_fold_only_quiet_far_ends() -> None:
    """Every station is shown once, and the busy lines stay open."""
    for station, kept in (
        (TOWN_HALL, ('Strathfield Station', 'Parramatta Station')),
        (BLACKTOWN, ('Parramatta Station', 'Penrith Station')),
    ):
        reach, _ = train_reach(station=station)
        shown = folded(reach=reach)
        assert sum(row.folded or 1 for row in shown.rows) == len(reach.rows)
        names = {row.place.name for row in shown.rows}
        assert set(kept) <= names
        assert all(
            row.folded >= FOLD_MIN
            for row in shown.rows
            if row.kind is RowKind.FOLD
        )


def test_vehicle_inside_a_closed_fold_is_drawn_on_its_row() -> None:
    """A train between two folded stations sits on the fold row."""
    reach = folded(reach=fold_reach(at=0, size=12, quiet=4))
    stations = ('C', *(f'S{i}' for i in range(1, 12)))
    view = build_strip_view(inputs=inputs_of(
        reach=reach,
        departures=[live(item=train(minutes=0, trip_id='e'))],
        patterns={
            'e': line_pattern(trip_id='e', stations=stations, minutes=0),
        },
        now=START + timedelta(minutes=28),
    ))
    (marker,) = view.markers
    fold = next(row for row in view.rows if row.kind is RowKind.FOLD)
    assert marker.y == fold.y
    assert fold.time == ''


def test_to_cursor_on_a_fold_offers_to_open_it() -> None:
    """On a closed fold the TO tag reads OPEN and the hint says open."""
    state, _ = on_view(view=LiveView.STOP, pin=replace(PIN, kind=None))
    state, _ = run(state=state, events=[
        *TO_STAGE,
        StripLoaded(key=CHOSEN_KEY, reach=mid_fold_reach(origin='S2')),
        DOWN, DOWN, DOWN, DOWN,
    ])
    view = state.last_view
    assert isinstance(view, StripView)
    cursor = next(row for row in view.rows if row.cursor)
    assert (cursor.kind, cursor.tag) == (RowKind.FOLD, OPEN_TAG)
    assert view.panel.hint == 'Bottom: press to open \u2022 ALL: any stop'
    state, _ = run(state=state, events=[PRESS])
    view = state.last_view
    assert isinstance(view, StripView)
    assert next(row for row in view.rows if row.cursor).tag == 'TO?'
    assert view.panel.hint == 'Bottom: press to set To \u2022 ALL: any stop'


def strip_with_end_open(*, pins: tuple[Pin, ...]) -> State:
    """
    Show the first pin's Strip and scroll up until its top fold opens.

    Parameters
    ----------
    pins : tuple[Pin, ...]
        Pins; the first is shown.

    Returns
    -------
    State
        State on the Strip with fold 'S5' open.
    """
    saved = Saved(
        settings=default_settings(city=SYDNEY),
        pins=pins, current=0, unpinned=None, view=LiveView.STRIP,
    )
    state = begun(saved=saved)
    state, _ = run(state=state, events=[
        StripLoaded(key=KEY, reach=fold_reach()), turn(steps=-30),
    ])
    assert state.strip.opened == {'S5'}
    return state


def test_place_change_shuts_the_folds() -> None:
    """Turning to a pin with another strip shuts the folds."""
    other = Pin(origin=place('D', name='D'), kind=SydneyMode.TRAIN)
    state = strip_with_end_open(pins=(replace(PIN, destination=None), other))
    state, _ = run(state=state, events=[TOP_TURN])
    assert state.strip.opened == frozenset()


def test_destination_change_shuts_the_folds() -> None:
    """The same strip with another Destination starts with folds shut."""
    state = strip_with_end_open(pins=(
        replace(PIN, destination=None),
        replace(PIN, destination=place('S30', name='S30')),
    ))
    state, _ = run(state=state, events=[TOP_TURN])
    assert state.strip.opened == frozenset()


def to_stage_on_fold() -> State:
    """
    Reach the TO stage over the mid-fold strip with the cursor on Q1…Q4.

    Returns
    -------
    State
        State in the TO stage, cursor on the closed fold.
    """
    state, _ = on_view(view=LiveView.STOP, pin=replace(PIN, kind=None))
    state, _ = run(state=state, events=[
        *TO_STAGE,
        StripLoaded(key=CHOSEN_KEY, reach=mid_fold_reach(origin='S2')),
        DOWN, DOWN, DOWN, DOWN,
    ])
    return state


def test_leaving_the_to_stage_shuts_a_fold_it_opened() -> None:
    """A fold opened by a TO press shuts on going back to FROM."""
    state, _ = run(state=to_stage_on_fold(), events=[PRESS])
    assert state.strip.opened == {'Q1'}
    state, _ = run(state=state, events=[TOP_PRESS])
    assert state.chooser is not None
    assert state.chooser.stage is Stage.FROM
    assert state.strip.opened == frozenset()


def test_leaving_the_to_stage_shuts_an_end_fold() -> None:
    """A fold the TO screen opened at the end shuts on going back."""
    state, _ = on_view(view=LiveView.STOP, pin=replace(PIN, kind=None))
    state, _ = run(state=state, events=[
        *TO_STAGE,
        StripLoaded(
            key=CHOSEN_KEY, reach=fold_reach(origin='S2', at=0, size=40),
        ),
        *([DOWN] * 40),
    ])
    assert state.strip.opened == {'S34'}
    state, _ = run(state=state, events=[TOP_PRESS])
    assert state.strip.opened == frozenset()


def test_closing_the_chooser_shuts_its_folds() -> None:
    """Backing out of the chooser altogether leaves no fold open."""
    state, _ = run(state=to_stage_on_fold(), events=[PRESS])
    for _ in range(8):
        if state.chooser is None:
            break
        state, _ = run(state=state, events=[TOP_PRESS])
    assert state.chooser is None
    assert state.strip.opened == frozenset()


def test_new_timetable_shuts_the_folds() -> None:
    """A timetable swap drops the opened folds with the reaches."""
    state = strip_with_end_open(pins=(replace(PIN, destination=None),))
    state, _ = run(state=state, events=[TimetableSeen(version='v2')])
    assert state.strip.opened == frozenset()
    assert not state.strip.reaches


def test_new_timetable_with_folds_open_sends_the_strip_home() -> None:
    """A swap drops the reach, so the scroll goes back to home."""
    state = strip_with_end_open(pins=(replace(PIN, destination=None),))
    assert state.strip.scroll != 0
    state, _ = run(state=state, events=[TimetableSeen(version='v2')])
    assert state.strip.scroll == 0


def test_opening_the_chooser_sends_the_strip_home() -> None:
    """Folds shut as the chooser opens; cancelling lands on home rows."""
    state = strip_with_end_open(pins=(replace(PIN, destination=None),))
    home = on_fold_strip()
    state, _ = run(state=state, events=[TOP_PRESS])
    assert state.chooser is not None
    assert (state.strip.opened, state.strip.scroll) == (frozenset(), 0)
    state, _ = run(state=state, events=[TOP_PRESS])
    assert state.chooser is None
    assert names(state=state)[0] == names(state=home)[0] == 'S18'
