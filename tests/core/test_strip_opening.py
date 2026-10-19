"""Tests for opening a strip's folds as they reach the screen."""

from dataclasses import replace
from typing import Final

import pytest

from paperroute.core.chooser import turned_to
from paperroute.core.messages import Knob, KnobPressed, StripLoaded
from paperroute.core.reach import Reach, ReachRow, RowKind
from paperroute.core.saved import LiveView
from paperroute.core.state_model import State
from paperroute.core.strip_folds import FOLD_MIN, folded
from paperroute.core.strip_model import StripMapView, StripView
from paperroute.core.strip_view import origin_of
from tests.core.chooser_walks import CHOSEN_KEY, DOWN, TO_STAGE, at_to
from tests.core.factories import place
from tests.core.test_reach import (
    BLACKTOWN,
    REAL_DB,
    TOWN_HALL,
    has_runs,
    train_reach,
)
from tests.core.test_strip_state import KEY, PIN, on_view, run, turn

PRESS: Final[KnobPressed] = KnobPressed(knob=Knob.BOTTOM)
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


def test_to_cursor_never_lands_on_a_closed_fold() -> None:
    """The TO cursor steps over a closed fold to the station past it."""
    reach = folded(reach=fold_reach(origin='S2', at=0, size=12, quiet=4))
    assert reach.rows[-1].kind is RowKind.FOLD
    chooser = at_to()
    for _ in range(12):
        chooser = turned_to(chooser=chooser, steps=1, reach=reach)
        assert reach.rows[chooser.to_cursor].kind is not RowKind.FOLD


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
    """Central's strip opens 128 rows above FROM, five far ends folded."""
    reach, _ = train_reach(station=CENTRAL)
    shown = folded(reach=reach)
    assert origin_of(reach=shown) == 128
    assert [row.place.name for row in shown.rows][0] == (
        'Lapstone … Bathurst +26'
    )
    assert sum(row.kind is RowKind.FOLD for row in shown.rows) == 5


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
