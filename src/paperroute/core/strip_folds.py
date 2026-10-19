"""Fold the quiet far ends of a strip's lines into single rows."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from typing import Final

from paperroute.core.model import StopPlace
from paperroute.core.reach import Reach, ReachRow, RowKind

FOLD_SHARE: Final[float] = 0.05  # of the busiest first link's trips
FOLD_MIN: Final[int] = 4  # stations in a fold
STATIONS: Final[frozenset[RowKind]] = frozenset(
    {RowKind.MAIN, RowKind.BRANCH},
)


@dataclass(frozen=True)
class Fold:
    """
    A quiet far end of a line, shown as one row while closed.

    Attributes
    ----------
    key : str
        Station ID of its first station, the one nearest the From.
    rows : range
        Its rows in the reach, top to bottom.
    entry : int
        The row of its first station.
    """

    key: str
    rows: range
    entry: int


def children_of(*, reach: Reach) -> list[list[int]]:
    """
    List the rows whose track comes from each row.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    list[list[int]]
        Child rows by row.
    """
    found: list[list[int]] = [[] for _ in reach.rows]
    for index, row in enumerate(reach.rows):
        if row.parent is not None:
            found[row.parent].append(index)
    return found


def floor_of(*, reach: Reach) -> float:
    """
    Find the trips under which a station counts as quiet.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    float
        `FOLD_SHARE` of the trips on the busiest track out of the From.
    """
    return FOLD_SHARE * max(
        (
            row.trips
            for row in reach.rows
            if row.parent is not None
            and reach.rows[row.parent].kind is RowKind.ORIGIN
        ),
        default=0,
    )


def under(*, index: int, children: Sequence[list[int]]) -> list[int]:
    """
    List a row and every row whose track comes from it, however far on.

    Parameters
    ----------
    index : int
        The row.
    children : Sequence[list[int]]
        Child rows by row.

    Returns
    -------
    list[int]
        The row and all rows after it on its line and the lines it leads
        to.
    """
    found, todo = [], [index]
    while todo:
        here = todo.pop()
        found.append(here)
        todo.extend(children[here])
    return found


def is_quiet(*, reach: Reach, rows: Sequence[int], floor: float) -> bool:
    """
    Say whether some rows are stations each reached on few trips.

    Parameters
    ----------
    reach : Reach
        The reach.
    rows : Sequence[int]
        Rows.
    floor : float
        Trips under which a station is quiet.

    Returns
    -------
    bool
        True when every row is a station reached on fewer than `floor`
        trips.
    """
    return all(
        reach.rows[i].kind in STATIONS and reach.rows[i].trips < floor
        for i in rows
    )


def far_ends(*, reach: Reach, children: Sequence[list[int]],
             floor: float) -> list[list[int]]:
    """
    Find the quiet far ends: a quiet station and all that comes after it.

    Parameters
    ----------
    reach : Reach
        The reach.
    children : Sequence[list[int]]
        Child rows by row.
    floor : float
        Trips under which a station is quiet.

    Returns
    -------
    list[list[int]]
        For each station that, with everything after it, is quiet while
        its parent with everything after that is not, those rows, the
        station first.
    """
    quiet = [
        is_quiet(
            reach=reach, rows=under(index=i, children=children), floor=floor,
        )
        for i in range(len(reach.rows))
    ]
    return [
        under(index=i, children=children)
        for i, row in enumerate(reach.rows)
        if quiet[i] and (row.parent is None or not quiet[row.parent])
    ]


def folds_of(*, reach: Reach, keep: Collection[str] = ()
             ) -> tuple[Fold, ...]:
    """
    Find the quiet far ends of a reach's lines.

    Parameters
    ----------
    reach : Reach
        The reach.
    keep : Collection[str]
        Station IDs never folded away, such as the Destination's.

    Returns
    -------
    tuple[Fold, ...]
        Each far end of at least `FOLD_MIN` stations, everything after
        its first station included so nothing hangs beyond it, each
        reached on fewer than `FOLD_SHARE` of the trips on the busiest
        track out of the From; top to bottom.
    """
    children = children_of(reach=reach)
    ends = far_ends(
        reach=reach, children=children, floor=floor_of(reach=reach),
    )
    return tuple(sorted(
        (
            Fold(
                key=reach.rows[found[0]].place.stop_id,
                rows=range(min(found), max(found) + 1),
                entry=found[0],
            )
            for found in ends
            if len(found) >= FOLD_MIN
            and max(found) - min(found) + 1 == len(found)
            and not any(reach.rows[i].place.stop_id in keep for i in found)
        ),
        key=lambda fold: fold.rows.start,
    ))


def line_end(*, reach: Reach, index: int, children: Sequence[list[int]]
             ) -> int:
    """
    Follow a row's own line to its far end.

    Parameters
    ----------
    reach : Reach
        The reach.
    index : int
        The row.
    children : Sequence[list[int]]
        Child rows by row.

    Returns
    -------
    int
        The last row on the row's line, at its depth.
    """
    depth = reach.rows[index].depth
    here: int | None = index
    while here is not None:
        index = here
        here = next(
            (i for i in children[here] if reach.rows[i].depth == depth),
            None,
        )
    return index


def short_name(*, place: StopPlace) -> str:
    """
    Name a station without its ' Station' suffix.

    Parameters
    ----------
    place : StopPlace
        The station.

    Returns
    -------
    str
        The name.
    """
    return place.name.removesuffix(' Station')


def fold_row(*, reach: Reach, fold: Fold) -> ReachRow:
    """
    Make the row a closed fold shows.

    Parameters
    ----------
    reach : Reach
        The reach.
    fold : Fold
        The fold.

    Returns
    -------
    ReachRow
        A fold row named '<first> … <last> +<n>', last the far end of
        its first station's line and n its stations, on its first
        station's track and parent, with its position.
    """
    entry = reach.rows[fold.entry]
    last = line_end(
        reach=reach, index=fold.entry, children=children_of(reach=reach),
    )
    name = (
        f'{short_name(place=entry.place)} … '
        f'{short_name(place=reach.rows[last].place)} +{len(fold.rows)}'
    )
    return replace(
        entry,
        place=replace(entry.place, name=name),
        kind=RowKind.FOLD,
        folded=len(fold.rows),
    )


def new_indexes(*, total: int, closed: Sequence[Fold]) -> list[int]:
    """
    Number the rows as they stand with some folds closed.

    Parameters
    ----------
    total : int
        Rows in the reach.
    closed : Sequence[Fold]
        Closed folds.

    Returns
    -------
    list[int]
        The new row of each reach row; every row of a closed fold maps
        to its fold row.
    """
    starts = {i: fold.rows.start for fold in closed for i in fold.rows}
    found: list[int] = []
    count = 0
    for index in range(total):
        start = starts.get(index, index)
        found.append(count if start == index else found[start])
        count += start == index
    return found


def folded_stations(*, reach: Reach, closed: Sequence[Fold]
                    ) -> dict[str, str]:
    """
    Map each stop to its station, or to its fold while that is closed.

    Parameters
    ----------
    reach : Reach
        The reach.
    closed : Sequence[Fold]
        Closed folds.

    Returns
    -------
    dict[str, str]
        The reach's map with every station of a closed fold, and every
        stop under one, sent to the fold's key.
    """
    moved = {
        reach.rows[index].place.stop_id: fold.key
        for fold in closed
        for index in fold.rows
    }
    return {
        stop: moved.get(station, station)
        for stop, station in reach.station_of.items()
    } | moved


def folded(*, reach: Reach, opened: Collection[str] = (),
           keep: Collection[str] = ()) -> Reach:
    """
    Show a reach with its folds closed but for the opened ones.

    Parameters
    ----------
    reach : Reach
        The reach.
    opened : Collection[str]
        Keys of the folds shown open.
    keep : Collection[str]
        Station IDs never folded away.

    Returns
    -------
    Reach
        The reach with each closed fold's rows replaced by its fold row,
        tracks renumbered, and its stations' stops mapped to the fold
        row; the reach itself with none closed.
    """
    closed = [
        fold
        for fold in folds_of(reach=reach, keep=keep)
        if fold.key not in opened
    ]
    if not closed:
        return reach
    index = new_indexes(total=len(reach.rows), closed=closed)
    shown = {
        fold.rows.start: fold_row(reach=reach, fold=fold) for fold in closed
    }
    gone = {i for fold in closed for i in fold.rows} - shown.keys()
    rows = (
        shown.get(i, row) for i, row in enumerate(reach.rows) if i not in gone
    )
    return replace(
        reach,
        rows=tuple(
            replace(
                row,
                parent=None if row.parent is None else index[row.parent],
            )
            for row in rows
        ),
        station_of=folded_stations(reach=reach, closed=closed),
    )
