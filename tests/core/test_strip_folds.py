"""Tests for folding a strip's quiet far ends into single rows."""

from collections.abc import Sequence

from hypothesis import given
from hypothesis import strategies as st

from paperroute.core.model import StopPlace
from paperroute.core.reach import Reach, ReachRow, RowKind, RunTail
from paperroute.core.reach_layout import LineExit
from paperroute.core.strip_folds import floor_of, folded, folds_of
from paperroute_testkit.core.reach_factories import (
    STATIONS,
    ids,
    random_tails,
    reach_of,
    tail,
)

type RowSpec = tuple[str, int, int | None, int]


def reach_from(*, specs: Sequence[RowSpec], above: int = 0) -> Reach:
    """
    Build a reach from rows given as name, depth, parent row and trips.

    Parameters
    ----------
    specs : Sequence[RowSpec]
        Rows top to bottom; the one with no parent is the From.
    above : int
        Rows above the From.

    Returns
    -------
    Reach
        The reach; each station also has a platform '<name>_1'.
    """
    rows = tuple(
        ReachRow(
            place=StopPlace(stop_id=name, name=f'{name} Station', point=None),
            kind=(
                RowKind.ORIGIN
                if parent is None
                else RowKind.MAIN if depth == 0 else RowKind.BRANCH
            ),
            depth=depth,
            above=index < above,
            parent=parent,
            trips=trips,
        )
        for index, (name, depth, parent, trips) in enumerate(specs)
    )
    names = [name for name, *_ in specs]
    return Reach(
        rows=rows,
        stations=len(rows) - 1,
        station_of={
            **{name: name for name in names},
            **{f'{name}_1': name for name in names},
        },
    )


def line(*trips: int) -> list[RowSpec]:
    """
    Make the From and one line below it.

    Parameters
    ----------
    *trips : int
        Trips into each station, nearest first.

    Returns
    -------
    list[RowSpec]
        Rows C, E1, E2, ...
    """
    return [
        ('C', 0, None, 0),
        *(
            (f'E{k + 1}', 0, k, count)
            for k, count in enumerate(trips)
        ),
    ]


def names(*, reach: Reach) -> list[str]:
    """
    List a reach's row names.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    list[str]
        Names, top to bottom.
    """
    return [row.place.name for row in reach.rows]


def test_quiet_far_end_folds_into_one_row() -> None:
    """Four stations on few trips at a line's end become one fold row."""
    reach = folded(reach=reach_from(specs=line(100, 100, 4, 4, 4, 4)))
    assert names(reach=reach) == [
        'C Station', 'E1 Station', 'E2 Station', 'E3 … E6 +4',
    ]
    fold = reach.rows[3]
    assert (fold.kind, fold.folded, fold.parent) == (RowKind.FOLD, 4, 2)
    assert fold.place.stop_id == 'E3'


def test_fold_thresholds() -> None:
    """Under 5% of the busiest first link, and four stations at least."""
    assert folds_of(reach=reach_from(specs=line(100, 5, 5, 5, 5))) == ()
    assert folds_of(reach=reach_from(specs=line(100, 100, 4, 4, 4))) == ()
    (fold,) = folds_of(reach=reach_from(specs=line(100, 4, 4, 4, 4)))
    assert (fold.key, fold.rows) == ('E2', range(2, 6))


def test_exit_rows_never_fold() -> None:
    """Past an exit the quiet line folds; the exit and J stay rows."""
    reach = reach_of(
        tails=[tail('W1 W2', 100), tail('J1 J F1 F2 F3 F4 F5', 2)],
        exits=[LineExit(junction='J', off='X', lines=('T9',), trips=2)],
    )
    assert ids(reach=reach) == [
        'F5', 'F4', 'F3', 'F2', 'F1', 'X', 'J', 'J1', 'C', 'W1', 'W2',
    ]
    shown = folded(reach=reach)
    assert [row.kind for row in shown.rows[:4]] == [
        RowKind.FOLD, RowKind.EXIT, RowKind.MAIN, RowKind.MAIN,
    ]
    assert ids(reach=shown)[:4] == ['F1', 'X', 'J', 'J1']
    assert shown.rows[0].folded == 5


def test_floor_ignores_exit_rows() -> None:
    """A busy exit at the From does not raise the quiet floor."""
    plain = reach_of(tails=[tail('E1 E2', 40)])
    exited = reach_of(
        tails=[tail('E1 E2', 40)],
        exits=[LineExit(junction='C', off='X', lines=('T9',), trips=900)],
    )
    assert floor_of(reach=exited) == floor_of(reach=plain)


def test_fold_needs_nothing_busy_beyond_it() -> None:
    """A quiet stretch with a busier station after it stays open."""
    assert folds_of(reach=reach_from(specs=line(100, 4, 4, 4, 4, 50))) == ()


def test_fold_takes_the_branches_beyond_it() -> None:
    """A spur off a quiet far end folds with it, named by its own line."""
    reach = reach_from(specs=[
        ('C', 0, None, 0),
        ('E1', 0, 0, 100),
        ('E2', 0, 1, 4),
        ('E3', 0, 2, 4),
        ('S1', 1, 3, 2),
        ('S2', 1, 4, 2),
        ('E4', 0, 3, 4),
    ])
    assert names(reach=folded(reach=reach)) == [
        'C Station', 'E1 Station', 'E2 … E4 +5',
    ]


def test_fold_above_is_named_from_the_from_side() -> None:
    """Above the From the fold row still reads nearest station first."""
    reach = reach_from(
        specs=[
            ('E4', 0, 1, 4),
            ('E3', 0, 2, 4),
            ('E2', 0, 3, 4),
            ('E1', 0, 4, 4),
            ('C', 0, None, 0),
            ('W1', 0, 4, 100),
        ],
        above=4,
    )
    shown = folded(reach=reach)
    assert names(reach=shown) == ['E1 … E4 +4', 'C Station', 'W1 Station']
    assert shown.rows[0].parent == 1
    assert shown.rows[2].parent == 1


def test_destination_is_never_folded_away() -> None:
    """A fold holding a kept station stays open."""
    reach = reach_from(specs=line(100, 4, 4, 4, 4))
    assert folded(reach=reach, keep={'E3'}) == reach


def test_opened_fold_shows_its_stations() -> None:
    """An opened fold's rows are back, and its stops map to themselves."""
    reach = reach_from(specs=line(100, 4, 4, 4, 4))
    assert folded(reach=reach, opened={'E2'}) == reach
    shut = folded(reach=reach)
    assert shut.station_of['E4_1'] == 'E2'
    assert shut.station_of['E4'] == 'E2'
    assert shut.station_of['E1_1'] == 'E1'
    assert shut.stations == reach.stations


@st.composite
def busy_tails(draw: st.DrawFn) -> list[RunTail]:
    """
    Draw random tails with one busy line, so quiet ends fold.

    Parameters
    ----------
    draw : st.DrawFn
        Hypothesis draw.

    Returns
    -------
    list[RunTail]
        Tails from C.
    """
    busy = RunTail(
        before=(), after=STATIONS[:2], trips=draw(st.integers(20, 400)),
    )
    return [busy, *draw(random_tails())]


@given(tails=busy_tails(), data=st.data())
def test_every_station_shows_once_folded_or_open(
    tails: list[RunTail], data: st.DataObject,
) -> None:
    """Each station is one row, or inside exactly one closed fold."""
    reach = reach_of(tails=tails)
    keys = [fold.key for fold in folds_of(reach=reach)]
    opened = data.draw(st.sets(st.sampled_from(keys)) if keys else st.just(
        set[str](),
    ))
    shown = folded(reach=reach, opened=opened)
    assert sum(row.folded or 1 for row in shown.rows) == len(reach.rows)
    every = {row.place.stop_id for row in reach.rows}
    assert {shown.station_of.get(key, key) for key in every} == {
        row.place.stop_id for row in shown.rows
    }
    assert all(
        row.parent is None or shown.rows[row.parent].kind is not RowKind.FOLD
        for row in shown.rows
    )
    assert sum(row.kind is RowKind.FOLD for row in shown.rows) == len(
        set(keys) - opened,
    )


def test_folding_is_worked_out_once_per_reach() -> None:
    """The same reach and opened folds give back the same folded reach."""
    reach = reach_from(specs=line(100, 4, 4, 4, 4))
    shut = folded(reach=reach)
    assert folded(reach=reach) is shut
    assert folded(reach=reach, opened={'E2'}) is reach
    assert folded(reach=reach_from(specs=line(100, 4, 4, 4, 4))) == shut


def test_approach_rows_never_fold() -> None:
    """A long quiet run before FROM is never folded."""
    before = ' '.join(f'P{index}' for index in range(40))
    reach = reach_of(tails=[tail('E1', 1, before=before)])
    shown = folded(reach=reach)
    assert ids(reach=shown)[:40] == ids(reach=reach)[:40]
