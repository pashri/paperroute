"""The Strip and Strip + map views' content: rows, times, vehicles, panel."""

from collections.abc import Sequence
from dataclasses import replace

from paperroute.core.map_view import Marker
from paperroute.core.model import StopPlace
from paperroute.core.reach import Reach, ReachRow, RowKind
from paperroute.core.stop_view import DOT, clock_text
from paperroute.core.strip_model import (
    CURSOR_MARGIN,
    HINTS,
    INDENT,
    LOADING,
    NO_STRIP,
    PANE_FROM_ROW,
    PANE_ROWS,
    PANE_TRACK_X,
    PANEL_ROWS,
    PICK_FROM,
    ROW_H,
    STRIP_ROWS,
    STRIP_TOP,
    TRACK_X,
    Band,
    Marks,
    StripInputs,
    StripPane,
    StripPanel,
    StripRow,
    StripView,
)
from paperroute.core.strip_vehicles import (
    Spot,
    TripInputs,
    first_arrivals,
    vehicle_markers,
)


def line_reach(*, line: Sequence[StopPlace]) -> Reach:
    """
    Lay out a towards line as a plain strip, for picking a From.

    Parameters
    ----------
    line : Sequence[StopPlace]
        Stations in running order.

    Returns
    -------
    Reach
        One main-line row per station, at its first call, each with the
        previous row as its parent; no From row and no station map.
    """
    places: dict[str, StopPlace] = {}
    for place in line:
        places.setdefault(place.stop_id, place)
    return Reach(
        rows=tuple(
            ReachRow(
                place=place,
                kind=RowKind.MAIN,
                depth=0,
                above=False,
                parent=None if index == 0 else index - 1,
            )
            for index, place in enumerate(places.values())
        ),
        stations=len(places),
        station_of={},
    )


def origin_of(*, reach: Reach) -> int | None:
    """
    Find the From row.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    int | None
        Its index, or None for a reach without one.
    """
    return next(
        (
            index
            for index, row in enumerate(reach.rows)
            if row.kind is RowKind.ORIGIN
        ),
        None,
    )


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
        The index of the station's row past the From, or None when there
        is no destination or it isn't on the strip.
    """
    if destination is None:
        return None
    station = reach.station_of.get(destination, destination)
    return next(
        (
            index
            for index, row in enumerate(reach.rows)
            if row.place.stop_id == station
            and row.kind in (RowKind.MAIN, RowKind.BRANCH)
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


def row_times(
    *,
    inputs: StripInputs,
    reach: Reach,
    origin: int | None,
    ride: frozenset[int],
) -> dict[int, str]:
    """
    Choose the clock time each row shows.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    reach : Reach
        The reach.
    origin : int | None
        The From row; with none no row has a time.
    ride : frozenset[int]
        The ridden rows; empty when there is no Destination row.

    Returns
    -------
    dict[int, str]
        Clock time by row: the ridden rows past the From, or every
        reached row when nothing is ridden. Approach rows and the From
        have none.
    """
    if origin is None:
        return {}
    best = first_arrivals(
        trips=trips_of(inputs=inputs), origin=reach.rows[origin].place.stop_id,
    )
    return {
        index: clock_text(moment=best[row.place.stop_id])
        for index, row in enumerate(reach.rows)
        if row.kind in (RowKind.MAIN, RowKind.BRANCH)
        and (not ride or index in ride)
        and row.place.stop_id in best
    }


def marks_of(*, inputs: StripInputs, reach: Reach) -> Marks:
    """
    Work out what each row is marked with.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    reach : Reach
        The reach.

    Returns
    -------
    Marks
        The From, Destination, ride, times and cursor.
    """
    origin = origin_of(reach=reach)
    target = target_of(reach=reach, destination=inputs.destination)
    ride = ride_of(reach=reach, target=target)
    return Marks(
        origin=origin,
        target=target,
        ride=ride,
        times=row_times(
            inputs=inputs, reach=reach, origin=origin, ride=ride,
        ),
        cursor=inputs.cursor,
        picking=inputs.picking,
    )


def tag_of(*, index: int, marks: Marks) -> str:
    """
    Choose a row's tag.

    Parameters
    ----------
    index : int
        Row index.
    marks : Marks
        What the rows are marked with.

    Returns
    -------
    str
        'FROM' on the From, then 'TO?' or 'FROM?' on the cursor, then
        'TO' on the Destination, else ''.
    """
    if index == marks.origin:
        return 'FROM'
    if index == marks.cursor and marks.picking is not None:
        return f'{marks.picking}?'
    return 'TO' if index == marks.target else ''


def slot_y(*, index: int, first: int) -> float:
    """
    Find the screen y of a row's centre line.

    Parameters
    ----------
    index : int
        Row index.
    first : int
        The row in the top slot.

    Returns
    -------
    float
        Screen y; above or below the strip for rows off it.
    """
    return STRIP_TOP + (index - first) * ROW_H + ROW_H / 2


def strip_row(*, reach: Reach, index: int, first: int, marks: Marks
              ) -> StripRow:
    """
    Make one visible row.

    Parameters
    ----------
    reach : Reach
        The reach.
    index : int
        Row index.
    first : int
        The row in the top slot.
    marks : Marks
        What the rows are marked with.

    Returns
    -------
    StripRow
        The row.
    """
    row = reach.rows[index]
    parent = None if row.parent is None else reach.rows[row.parent]
    return StripRow(
        name=row.place.name.removesuffix(' Station'),
        depth=row.depth,
        kind=row.kind,
        y=slot_y(index=index, first=first),
        parent_y=(
            None
            if row.parent is None
            else slot_y(index=row.parent, first=first)
        ),
        parent_depth=row.depth if parent is None else parent.depth,
        tag=tag_of(index=index, marks=marks),
        time=marks.times.get(index, ''),
        ride=index in marks.ride,
        cursor=index == marks.cursor,
        bold=index in (marks.origin, marks.target),
        station=row.place.stop_id,
    )


def home_of(*, reach: Reach, destination: str | None) -> int:
    """
    Find the unscrolled, unclamped top row of the Strip.

    Parameters
    ----------
    reach : Reach
        The reach.
    destination : str | None
        Destination stop or station ID, or None.

    Returns
    -------
    int
        The row that puts the From in the third slot. With a Destination
        on the strip, moved the least that shows the ride from the From
        to it when the ride fits; otherwise the From in the top slot for
        a ride down the strip, the bottom slot for one up it. 0 with no
        From.
    """
    origin = origin_of(reach=reach)
    if origin is None:
        return 0
    start = origin - PANE_FROM_ROW
    target = target_of(reach=reach, destination=destination)
    ride = ride_of(reach=reach, target=target)
    if not ride:
        return start
    low, high = min(ride), max(ride)
    if high - low < STRIP_ROWS:
        return min(max(start, high + 1 - STRIP_ROWS), low)
    return origin if high > origin else origin + 1 - STRIP_ROWS


def first_row(*, inputs: StripInputs, total: int, home: int) -> int:
    """
    Choose the row in the Strip's top slot.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    total : int
        Rows in the reach.
    home : int
        The unscrolled top row, from `home_of`.

    Returns
    -------
    int
        The home row moved by the scroll, then moved to keep any cursor
        `CURSOR_MARGIN` rows from the edges, clamped to the rows.
    """
    start = home + inputs.scroll
    cursor = inputs.cursor
    if cursor is not None:
        start = min(
            max(start, cursor + CURSOR_MARGIN + 1 - STRIP_ROWS),
            cursor - CURSOR_MARGIN,
        )
    return max(0, min(start, total - STRIP_ROWS))


def turns_away(*, inputs: StripInputs, origin: int, start: int,
               top: bool) -> int:
    """
    Count the detents back to the From.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    origin : int
        The From row.
    start : int
        The unclamped top row: the home row moved by the scroll.
    top : bool
        True when the From is above the strip.

    Returns
    -------
    int
        The cursor's distance from the From while picking; otherwise the
        fewest scroll steps that bring the From onto the strip.
    """
    if inputs.cursor is not None:
        return abs(inputs.cursor - origin)
    if top:
        return start - origin
    return origin - (start + STRIP_ROWS - 1)


def band_of(*, inputs: StripInputs, reach: Reach, rows: tuple[int, int]
            ) -> Band | None:
    """
    Make the From band when the From is off the Strip.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    reach : Reach
        The reach.
    rows : tuple[int, int]
        The home row and the row in the top slot.

    Returns
    -------
    Band | None
        The band at the edge nearest the From, or None when the From is
        on the strip or there is none.
    """
    home, first = rows
    origin = origin_of(reach=reach)
    if origin is None or first <= origin < first + STRIP_ROWS:
        return None
    top = origin < first
    name = reach.rows[origin].place.name.removesuffix(' Station')
    turns = turns_away(
        inputs=inputs, origin=origin, start=home + inputs.scroll, top=top,
    )
    unit = 'turn' if turns == 1 else 'turns'
    return Band(text=f'FROM {name}{DOT}{turns} {unit} away', top=top)


def strip_rows(*, inputs: StripInputs, reach: Reach
               ) -> tuple[tuple[StripRow, ...], Band | None]:
    """
    Lay out the Strip's visible rows and band.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    reach : Reach
        The reach.

    Returns
    -------
    tuple[tuple[StripRow, ...], Band | None]
        Rows top to bottom, and the band, which takes the edge slot.
    """
    marks = marks_of(inputs=inputs, reach=reach)
    total = len(reach.rows)
    home = home_of(reach=reach, destination=inputs.destination)
    first = first_row(inputs=inputs, total=total, home=home)
    band = band_of(inputs=inputs, reach=reach, rows=(home, first))
    indices = range(first, min(first + STRIP_ROWS, total))
    if band is not None:
        indices = indices[1:] if band.top else indices[:-1]
    rows = tuple(
        strip_row(reach=reach, index=index, first=first, marks=marks)
        for index in indices
    )
    return rows, band


def trips_of(*, inputs: StripInputs) -> TripInputs:
    """
    Pick out what the strip's times and vehicles come from.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.

    Returns
    -------
    TripInputs
        The departures, key, patterns, positions, time and station map.
    """
    return TripInputs(
        departures=inputs.departures,
        key=inputs.key,
        patterns=inputs.patterns,
        positions=inputs.positions,
        now=inputs.now,
        station_of={} if inputs.reach is None else inputs.reach.station_of,
    )


def strip_markers(*, inputs: StripInputs, rows: Sequence[StripRow],
                  track_x: int) -> tuple[Marker, ...]:
    """
    Place the strip's vehicles on its rows.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    rows : Sequence[StripRow]
        Visible rows.
    track_x : int
        Track x at depth 0.

    Returns
    -------
    tuple[Marker, ...]
        One badge per strip trip that can be placed, pushed apart.
    """
    return vehicle_markers(
        trips=trips_of(inputs=inputs),
        spots={
            row.station: Spot(x=track_x + INDENT * row.depth, y=row.y)
            for row in rows
        },
    )


def strip_panel(*, inputs: StripInputs) -> StripPanel:
    """
    Fill the departures panel.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.

    Returns
    -------
    StripPanel
        The From, its Destination line, the station count and the first
        departures: four, three under a hint, none while picking a From;
        the Stop view's empty text when there are none to list. No station
        count while picking a From, as there is no From yet.
    """
    stop = inputs.stop_view
    hint = None if inputs.picking is None else HINTS.get(inputs.picking)
    shown = PANEL_ROWS - (hint is not None)
    reach = inputs.reach
    return StripPanel(
        title=stop.origin,
        destination=stop.destination,
        note=(
            ''
            if reach is None or inputs.picking == PICK_FROM
            else f'{reach.stations} stations from here'
        ),
        rows=() if inputs.picking == PICK_FROM else stop.head_rows[:shown],
        no_destination=stop.destination is None,
        hint=hint,
        empty=None if inputs.picking == PICK_FROM else stop.empty,
    )


def empty_of(*, inputs: StripInputs) -> str | None:
    """
    Say why there is no strip.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.

    Returns
    -------
    str | None
        The given reason, `LOADING` with no reach yet, `NO_STRIP` for a
        reach with no stations, else None.
    """
    if inputs.unavailable is not None:
        return inputs.unavailable
    if inputs.reach is None:
        return LOADING
    return NO_STRIP if inputs.reach.stations == 0 else None


def shown_reach(*, inputs: StripInputs) -> Reach | None:
    """
    Give the reach to draw.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.

    Returns
    -------
    Reach | None
        The reach, or None when there is no strip to draw.
    """
    return None if empty_of(inputs=inputs) is not None else inputs.reach


def build_strip_view(*, inputs: StripInputs) -> StripView:
    """
    Build the Strip view.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.

    Returns
    -------
    StripView
        The view; no rows, band or markers when `empty` is set.
    """
    reach = shown_reach(inputs=inputs)
    rows, band = (
        ((), None) if reach is None else strip_rows(inputs=inputs, reach=reach)
    )
    stop = inputs.stop_view
    return StripView(
        origin=stop.origin,
        destination=stop.destination,
        clock=clock_text(moment=inputs.now),
        tag=inputs.tag,
        picking=inputs.picking,
        rows=rows,
        band=band,
        markers=strip_markers(inputs=inputs, rows=rows, track_x=TRACK_X),
        panel=strip_panel(inputs=inputs),
        empty=empty_of(inputs=inputs),
        banner=stop.banner,
    )


def build_strip_pane(*, inputs: StripInputs) -> StripPane:
    """
    Build the strip beside the map on Strip + map.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs; the scroll and cursor are ignored.

    Returns
    -------
    StripPane
        Up to `PANE_ROWS` rows with the From in the third slot, or the
        top slot as far up as it is, and their vehicles; nothing when
        there is no strip.
    """
    reach = shown_reach(inputs=inputs)
    if reach is None:
        return StripPane(rows=(), markers=())
    marks = marks_of(inputs=replace(inputs, cursor=None), reach=reach)
    first = max(
        0, 0 if marks.origin is None else marks.origin - PANE_FROM_ROW,
    )
    rows = tuple(
        strip_row(reach=reach, index=index, first=first, marks=marks)
        for index in range(first, min(first + PANE_ROWS, len(reach.rows)))
    )
    return StripPane(
        rows=rows,
        markers=strip_markers(
            inputs=inputs, rows=rows, track_x=PANE_TRACK_X,
        ),
    )
