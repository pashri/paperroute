"""Where the Strip's row slots sit and which rows fill them."""

from paperroute_device.core.text import DOT
from paperroute_transit.core.names import base_name
from paperroute_transit.core.reach import Reach, RowKind, StripKey
from paperroute_transit.core.strip_follow import origin_of
from paperroute_transit.core.strip_model import (
    BANNER_ROWS,
    CURSOR_MARGIN,
    PANE_FROM_ROW,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    Band,
    StripInputs,
    Window,
)


def destination_station(*, reach: Reach, destination: str | None
                        ) -> str | None:
    """
    Find the station a Destination stop is under.

    Parameters
    ----------
    reach : Reach
        The reach.
    destination : str | None
        Destination stop or station ID.

    Returns
    -------
    str | None
        The station ID, on the strip or not; None with no Destination.
    """
    if destination is None:
        return None
    return reach.station_of.get(destination, destination)


def target_of(*, reach: Reach, destination: str | None) -> int | None:
    """
    Find the Destination's row.

    Parameters
    ----------
    reach : Reach
        The reach.
    destination : str | None
        Destination stop or station ID.

    Returns
    -------
    int | None
        The index of the station's first row below the From, else its
        first row above; None when there is no destination or it isn't
        on the strip.
    """
    station = destination_station(reach=reach, destination=destination)
    origin = origin_of(reach=reach)
    after = 0 if origin is None else origin + 1
    order = (*range(after, len(reach.rows)), *range(after))
    return next(
        (
            index
            for index in order
            if station is not None
            and reach.rows[index].place.stop_id == station
            and reach.rows[index].kind in (RowKind.MAIN, RowKind.BRANCH)
        ),
        None,
    )


def ride_of(*, reach: Reach, target: int | None) -> frozenset[int]:
    """
    List the rows on the track from the Destination back to the From.

    Parameters
    ----------
    reach : Reach
        The reach.
    target : int | None
        The Destination's row.

    Returns
    -------
    frozenset[int]
        Row indices, both ends included; empty with no Destination row.
    """
    path: set[int] = set()
    at = target
    while at is not None:
        path.add(at)
        at = reach.rows[at].parent
    return frozenset(path)


def window_of(
    *, banner: bool, rows: int = STRIP_ROWS, top: int = STRIP_TOP,
) -> Window:
    """
    Find where a strip's row slots sit.

    Parameters
    ----------
    banner : bool
        True while a live data banner shows under the header.
    rows : int
        Slots with no banner.
    top : int
        Top of the first slot.

    Returns
    -------
    Window
        The slots from `top`; under a banner they start below it,
        `BANNER_ROWS` fewer.
    """
    drop = BANNER_ROWS if banner else 0
    return Window(top=top + drop * ROW_H, rows=rows - drop)


def inputs_window(
    *, inputs: StripInputs, rows: int = STRIP_ROWS, top: int = STRIP_TOP,
) -> Window:
    """
    Find where the slots sit for a view's inputs.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs; their Stop view says whether a banner shows.
    rows : int
        Slots with no banner.
    top : int
        Top of the first slot.

    Returns
    -------
    Window
        The slots.
    """
    return window_of(
        banner=inputs.stop_view.banner is not None, rows=rows, top=top,
    )


def slot_y(*, index: int, first: int, window: Window) -> float:
    """
    Find the screen y of a row's centre line.

    Parameters
    ----------
    index : int
        Row index.
    first : int
        The row in the top slot.
    window : Window
        Where the slots sit.

    Returns
    -------
    float
        Screen y; above or below the strip for rows off it.
    """
    return window.top + (index - first) * ROW_H + ROW_H / 2


def end_point_home(*, reach: Reach, origin: int, destination: bool) -> int:
    """
    Find the top row of an end-point kind's Strip.

    Parameters
    ----------
    reach : Reach
        The reach.
    origin : int
        The From row.
    destination : bool
        Whether the pin has a Destination.

    Returns
    -------
    int
        With a Destination, the From's row less up to two earlier
        calls. With rows above the From that are not earlier calls, the
        two directions, five rows above it. Otherwise the From's row.
    """
    above = reach.rows[:origin]
    if destination:
        return origin - min(2, sum(r.kind is RowKind.EARLIER for r in above))
    if any(row.kind is RowKind.MAIN for row in above):
        return origin - min(5, origin)
    return origin


def both_kerbs(*, key: StripKey | None) -> bool:
    """
    Tell whether a strip is fed by both kerbs of a road.

    Parameters
    ----------
    key : StripKey | None
        The strip's key, or None.

    Returns
    -------
    bool
        True when the key names a kerb across the road.
    """
    return key is not None and key.across is not None


def from_slot(*, rows: int, middle: bool) -> int:
    """
    Find the slot the From rests in.

    Parameters
    ----------
    rows : int
        Row slots on the strip.
    middle : bool
        Whether both kerbs feed the strip.

    Returns
    -------
    int
        The upper middle slot when `middle`, else `PANE_FROM_ROW`.
    """
    return (rows - 1) // 2 if middle else PANE_FROM_ROW


def home_of(
    *,
    reach: Reach,
    destination: str | None,
    rows: int,
    end_points: bool = False,
    middle: bool = False,
) -> int:
    """
    Find the unscrolled, unclamped top row of the Strip.

    Parameters
    ----------
    reach : Reach
        The reach.
    destination : str | None
        Destination stop or station ID, or None.
    rows : int
        Row slots on the strip.
    end_points : bool
        Whether the pin's kind is chosen by end points, which ignores
        the ride (see `end_point_home`).
    middle : bool
        Whether both kerbs feed the strip.

    Returns
    -------
    int
        The row that puts the From in its `from_slot`. With a Destination
        on the strip, moved the least that shows the ride from the From
        to it when the ride fits; otherwise the From in the top slot for
        a ride down the strip, the bottom slot for one up it. Rows
        below the From that follow a train keep the From in the third
        slot, so the window stays put as the train changes. 0 with no
        From.
    """
    origin = origin_of(reach=reach)
    if origin is None:
        return 0
    if end_points:
        return end_point_home(
            reach=reach, origin=origin, destination=destination is not None,
        )
    start = origin - from_slot(rows=rows, middle=middle)
    if reach.trip is not None:
        return start
    target = target_of(reach=reach, destination=destination)
    ride = ride_of(reach=reach, target=target)
    if not ride:
        return start
    low, high = min(ride), max(ride)
    if high - low < rows:
        return min(max(start, high + 1 - rows), low)
    return origin if high > origin else origin + 1 - rows


def first_row(
    *,
    scroll: int,
    cursor: int | None,
    total: int,
    home: int,
    rows: int,
    end_points: bool = False,
) -> int:
    """
    Choose the row in the Strip's top slot.

    Parameters
    ----------
    scroll : int
        Rows scrolled from the home row.
    cursor : int | None
        The cursor's row, or None.
    total : int
        Rows in the reach.
    home : int
        The unscrolled top row, from `home_of`.
    rows : int
        Row slots on the strip.
    end_points : bool
        Whether the pin's kind is chosen by end points.

    Returns
    -------
    int
        The home row moved by the scroll, then moved to keep any cursor
        `CURSOR_MARGIN` rows from the edges, clamped to the rows. For an
        end-point kind the window never rises above home to fill itself:
        the lowest limit of the clamp is the larger of home and the row
        that puts the last row in the last slot.
    """
    start = home + scroll
    if cursor is not None:
        start = min(
            max(start, cursor + CURSOR_MARGIN + 1 - rows),
            cursor - CURSOR_MARGIN,
        )
    last = max(home, total - rows) if end_points else total - rows
    return max(0, min(start, last))


def stops_away(*, reach: Reach, origin: int, near: int) -> int:
    """
    Count the stations on the way from a row back to the From.

    Parameters
    ----------
    reach : Reach
        The reach.
    origin : int
        The From row.
    near : int
        The row shown nearest the From.

    Returns
    -------
    int
        Stations on its track back to the From, neither end counted; a
        fold row counts as the stations it stands for. Spur rows that
        sit between but off that track are not counted.
    """
    count = 0
    here = reach.rows[near].parent
    while here is not None and here != origin:
        count += reach.rows[here].folded or 1
        here = reach.rows[here].parent
    return count


def band_of(*, reach: Reach, first: int, window: Window) -> Band | None:
    """
    Make the From band when the From is off the Strip.

    Parameters
    ----------
    reach : Reach
        The reach.
    first : int
        The row in the top slot.
    window : Window
        Where the slots sit.

    Returns
    -------
    Band | None
        The band at the edge nearest the From, or None when the From is
        on the strip or there is none.
    """
    origin = origin_of(reach=reach)
    if origin is None or first <= origin < first + window.rows:
        return None
    top = origin < first
    name = base_name(name=reach.rows[origin].place.name)
    stops = stops_away(
        reach=reach,
        origin=origin,
        near=first + 1 if top else first + window.rows - 2,
    )
    unit = 'stop' if stops == 1 else 'stops'
    return Band(
        text=f'FROM {name}{DOT}{stops} {unit} away',
        top=top,
        y=window.top + (0 if top else (window.rows - 1) * ROW_H),
    )
