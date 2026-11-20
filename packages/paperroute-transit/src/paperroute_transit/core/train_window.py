"""The dashed slot and the tail of a train Strip's window."""

from collections.abc import Sequence
from dataclasses import replace

from paperroute_transit.core.reach import Reach, RowKind
from paperroute_transit.core.strip_model import StripRow


def slot_indices(
    *, first: int, slots: int, total: int, destination: int | None,
) -> list[int]:
    """
    List the reach rows a window's slots are made from.

    Parameters
    ----------
    first : int
        The row in the top slot.
    slots : int
        Row slots.
    total : int
        Rows in the reach.
    destination : int | None
        The Destination's row, or None.

    Returns
    -------
    list[int]
        The rows from `first` on, one per slot; when the Destination is
        below the slots, its row is added last for the pinned slot.
    """
    found = list(range(first, min(first + slots, total)))
    if destination is not None and destination - first > slots - 1:
        found.append(destination)
    return found


def shows_dash(*, reach: Reach, first: int) -> bool:
    """
    Say whether a window at rest dashes its top slot.

    Parameters
    ----------
    reach : Reach
        The reach.
    first : int
        The row in the top slot.

    Returns
    -------
    bool
        True when the top two slots hold earlier calls.
    """
    return [row.kind for row in reach.rows[first:first + 2]] == [
        RowKind.EARLIER,
        RowKind.EARLIER,
    ]


def stub_text(*, stops: int, passed: int, outside: bool) -> str:
    """
    Say what the dashed stub above a pinned Destination hides.

    Parameters
    ----------
    stops : int
        Stations called at.
    passed : int
        Stations passed through.
    outside : bool
        Whether any hidden hop is outside the track.

    Returns
    -------
    str
        For example '3 stops, 5 stations passed'. A zero part is left
        out, the passed part too when `outside`, and singulars are used
        for one; empty when nothing is left.
    """
    parts: list[str] = []
    if stops:
        parts.append(f'{stops} {"stop" if stops == 1 else "stops"}')
    if passed and not outside:
        unit = 'station' if passed == 1 else 'stations'
        parts.append(f'{passed} {unit} passed')
    return ', '.join(parts)


def stub_of(
    *, rows: Sequence[StripRow], reach: Reach, first: int, destination: int,
) -> StripRow:
    """
    Make the dashed stub over the pinned Destination.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The window's slot rows, then the Destination's row.
    reach : Reach
        The reach.
    first : int
        The row in the top slot.
    destination : int
        The Destination's row.

    Returns
    -------
    StripRow
        A bare row in the slot before the last, on the track from the row
        above it. It names the stops and passed stations of the rows
        from its slot up to the Destination, which is not counted, and
        drops the passed part when the hop into any of them, the
        Destination included, is outside the track.
    """
    slots = len(rows) - 1
    span = reach.rows[first + slots - 2:destination + 1]
    hidden = span[:-1]
    stub = rows[slots - 2]
    return replace(
        stub,
        name=stub_text(
            stops=sum(row.kind is RowKind.MAIN for row in hidden),
            passed=sum(row.kind is RowKind.PASSED for row in hidden),
            outside=not all(row.inside for row in span),
        ),
        kind=RowKind.DASHED,
        depth=0,
        parent_y=rows[slots - 3].y if slots > 2 else None,
        parent_depth=0,
        tag='',
        time='',
        ride=rows[-1].ride,
        cursor=False,
        bold=False,
        station='',
        note=None,
        names=(),
    )


def tailed(
    *, rows: Sequence[StripRow], reach: Reach, first: int, destination: int,
) -> list[StripRow]:
    """
    Put a stub and the pinned Destination in a window's last two slots.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The window's slot rows, then the Destination's row.
    reach : Reach
        The reach.
    first : int
        The row in the top slot.
    destination : int
        The Destination's row.

    Returns
    -------
    list[StripRow]
        The rows with the last two slots replaced: the stub, then the
        Destination on the last slot, drawn from the stub.
    """
    stub = stub_of(
        rows=rows, reach=reach, first=first, destination=destination,
    )
    last = rows[len(rows) - 2]
    pinned = replace(
        rows[-1], y=last.y, parent_y=stub.y, parent_depth=stub.depth,
    )
    return [*rows[:len(rows) - 3], stub, pinned]


def dashed(*, row: StripRow) -> StripRow:
    """
    Turn an earlier call into the bare dashed slot above the others.

    Parameters
    ----------
    row : StripRow
        The top slot's row.

    Returns
    -------
    StripRow
        The row with no name, time, tag or station, kept on its track.
    """
    return replace(
        row,
        name='',
        kind=RowKind.DASHED,
        tag='',
        time='',
        cursor=False,
        station='',
        note=None,
    )


def windowed(
    *,
    rows: Sequence[StripRow],
    reach: Reach,
    first: int,
    scroll: int,
    destination: int | None,
    slots: int,
) -> list[StripRow]:
    """
    Dash the top of a train Strip's window and pin a far Destination.

    Parameters
    ----------
    rows : Sequence[StripRow]
        The slot rows from `slot_indices`: one per slot, then the
        Destination's row when it is below them.
    reach : Reach
        The reach.
    first : int
        The row in the top slot.
    scroll : int
        The Strip's scroll; 0 at rest.
    destination : int | None
        The Destination's row, or None.
    slots : int
        Row slots.

    Returns
    -------
    list[StripRow]
        The slot rows. At rest, when the top two slots are earlier
        calls, the top one is a bare dashed slot. When the Destination
        is below the slots, the last two are a dashed stub, naming the
        stops and stations passed in the hidden rows, and the
        Destination; exit rows hidden by it are not counted.
    """
    shown = list(rows)
    if destination is not None and destination - first > slots - 1:
        shown = tailed(
            rows=shown, reach=reach, first=first, destination=destination,
        )
    top = [row.kind for row in shown[:2]]
    if scroll == 0 and top == [RowKind.EARLIER, RowKind.EARLIER]:
        shown[0] = dashed(row=shown[0])
    return shown
