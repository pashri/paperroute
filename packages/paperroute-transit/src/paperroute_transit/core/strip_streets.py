"""Head a run of neighbouring stops on one street once."""

from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Final

from paperroute_transit.core.names import NameRules
from paperroute_transit.core.reach import RowKind
from paperroute_transit.core.streets import street_of, street_parts
from paperroute_transit.core.strip_model import StripRow

HEADING_ROOM: Final[float] = 20.0  # px added above each headed run
HEADED_KINDS: Final[frozenset[RowKind]] = frozenset(
    {RowKind.APPROACH, RowKind.ORIGIN, RowKind.MAIN, RowKind.BRANCH},
)


def street_runs(
    *, names: Sequence[str], rules: NameRules, least: int,
) -> tuple[tuple[int, int, str], ...]:
    """
    Find runs of neighbouring names on one street.

    A run of at least `least` names that starts at the first name has
    no room above it: that name keeps its full name and the run starts
    at the next one, if two or more are left.

    Parameters
    ----------
    names : Sequence[str]
        Names top to bottom; '' for a row that is never headed.
    rules : NameRules
        Name rules.
    least : int
        The fewest names a run needs.

    Returns
    -------
    tuple[tuple[int, int, str], ...]
        Start, end (inclusive) and street of each run.
    """
    streets = [
        None if not name else street_of(name=name, rules=rules)
        for name in names
    ]
    runs: list[tuple[int, int, str]] = []
    start = 0
    for index in range(1, len(streets) + 1):
        if index < len(streets) and streets[index] == streets[start]:
            continue
        street = streets[start]
        first = start + (start == 0)
        if street is not None and index - start >= least and index - first > 1:
            runs.append((first, index - 1, street))
        start = index
    return tuple(runs)


def room_ys(
    *, ys: Sequence[float], starts: Collection[int],
) -> tuple[float, ...]:
    """
    Make room above each run, taken evenly from every other gap.

    Parameters
    ----------
    ys : Sequence[float]
        Row centres, top to bottom.
    starts : Collection[int]
        Indexes of the rows each run starts at.

    Returns
    -------
    tuple[float, ...]
        The moved centres; the last row stays put when no run starts
        there.
    """
    others = len(ys) - len(starts)
    take = HEADING_ROOM * len(starts) / others if others else 0.0
    moved: list[float] = []
    shift = 0.0
    for index, y in enumerate(ys):
        shift += HEADING_ROOM if index in starts else -take
        moved.append(y + shift)
    return tuple(moved)


def moved_y(*, y: float, pairs: Sequence[tuple[float, float]]) -> float:
    """
    Move a y with the rows around it.

    Parameters
    ----------
    y : float
        A y on the old layout.
    pairs : Sequence[tuple[float, float]]
        Old and new centre of each row, top to bottom.

    Returns
    -------
    float
        The y on the new layout: between two rows in proportion, past
        either end by that end's shift.
    """
    for (old, new), (old_next, new_next) in zip(pairs, pairs[1:]):
        if old <= y <= old_next:
            share = (y - old) / (old_next - old) if old_next > old else 0.0
            return new + share * (new_next - new)
    end = pairs[0] if y < pairs[0][0] else pairs[-1]
    return y + end[1] - end[0]


def headable(*, row: StripRow) -> bool:
    """
    Say whether a row can sit under a street heading.

    Parameters
    ----------
    row : StripRow
        The row.

    Returns
    -------
    bool
        True for a station on the main line, off any loop or spur.
    """
    return row.kind in HEADED_KINDS and not row.depth and row.detour is None


def headed_rows(
    *, rows: Sequence[StripRow], rules: NameRules, least: int,
) -> tuple[StripRow, ...]:
    """
    Head each run of neighbouring stops on one street once.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Visible rows, top to bottom.
    rules : NameRules
        Name rules.
    least : int
        The fewest stops a run needs.

    Returns
    -------
    tuple[StripRow, ...]
        The rows; a run's rows carry their own part of the name, its
        first row the street, and every row moves to make room above
        each run.
    """
    runs = street_runs(
        names=[
            row.name
            if headable(row=row)
            else ''
            for row in rows
        ],
        rules=rules,
        least=least,
    )
    if not runs:
        return tuple(rows)
    labelled = list(rows)
    for start, end, street in runs:
        for index in range(start, end + 1):
            labelled[index] = labelled_row(
                row=rows[index],
                rules=rules,
                heading=street if index == start else '',
            )
    return spaced(rows=labelled, starts=[start for start, _, _ in runs])


def labelled_row(*, row: StripRow, rules: NameRules, heading: str
                 ) -> StripRow:
    """
    Give a run's row its own part of the name.

    Parameters
    ----------
    row : StripRow
        The row.
    rules : NameRules
        Name rules.
    heading : str
        The street on a run's first row, else ''.

    Returns
    -------
    StripRow
        The row with its label filled.
    """
    parts = street_parts(name=row.name, rules=rules)
    short = '' if parts is None else parts[1]
    return replace(
        row, label=replace(row.label, short=short, heading=heading),
    )


def spaced(*, rows: Sequence[StripRow], starts: Collection[int]
           ) -> tuple[StripRow, ...]:
    """
    Move rows and their parents to make room above each run.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Rows, top to bottom.
    starts : Collection[int]
        Indexes of the rows each run starts at.

    Returns
    -------
    tuple[StripRow, ...]
        The moved rows.
    """
    ys = [row.y for row in rows]
    moved = room_ys(ys=ys, starts=starts)
    pairs = sorted(zip(ys, moved))
    return tuple(
        replace(
            row,
            y=new,
            parent_y=(
                None
                if row.parent_y is None
                else moved_y(y=row.parent_y, pairs=pairs)
            ),
        )
        for row, new in zip(rows, moved)
    )
