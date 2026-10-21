"""The Strip and Strip + map views' content: rows, times, vehicles, panel."""

from collections.abc import Sequence
from dataclasses import replace

from paperroute.core.map_view import Marker
from paperroute.core.model import StopPlace
from paperroute.core.reach import Reach, ReachRow, RowKind
from paperroute.core.reach_exits import exit_name
from paperroute.core.stop_view import DOT, clock_text
from paperroute.core.strip_badges import (
    HALF_WIDE,
    NAME_REACH,
    Keepouts,
    Spot,
    level_with,
)
from paperroute.core.strip_model import (
    ALL_TAG,
    BANNER_ROWS,
    CURSOR_MARGIN,
    HINTS,
    INDENT,
    LOADING,
    NO_STRIP,
    OPEN_HINT,
    OPEN_TAG,
    PANE_FROM_ROW,
    PANE_ROWS,
    PANE_TRACK_X,
    PANEL_ROWS,
    PICK_FROM,
    PICK_TO,
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
    Window,
    hook_straight,
)
from paperroute.core.strip_notes import carry_note
from paperroute.core.strip_times import (
    TripInputs,
    first_arrivals,
    next_departure,
)
from paperroute.core.strip_vehicles import vehicle_markers


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
        The index of the station's row past the From, or None when there
        is no destination or it isn't on the strip.
    """
    station = destination_station(reach=reach, destination=destination)
    return next(
        (
            index
            for index, row in enumerate(reach.rows)
            if station is not None
            and row.place.stop_id == station
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
        Clock time by row: the From's next departure (to the Destination
        when there is one, on the strip or not), then the first arrival
        on the ridden rows past the From, or on every reached row when
        nothing is ridden.
        Approach rows have none.
    """
    if origin is None:
        return {}
    station = reach.rows[origin].place.stop_id
    trips = trips_of(inputs=inputs)
    best = first_arrivals(trips=trips, origin=station)
    times = {
        index: clock_text(moment=best[row.place.stop_id])
        for index, row in enumerate(reach.rows)
        if row.kind in (RowKind.MAIN, RowKind.BRANCH)
        and (not ride or index in ride)
        and row.place.stop_id in best
    }
    leaves = next_departure(
        trips=trips,
        origin=station,
        target=destination_station(
            reach=reach, destination=inputs.destination,
        ),
    )
    if leaves is not None:
        times[origin] = clock_text(moment=leaves)
    return times


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
        The From, Destination, ride, times, cursor and carry-on note.
    """
    origin = origin_of(reach=reach)
    target = target_of(reach=reach, destination=inputs.destination)
    ride = ride_of(reach=reach, target=target)
    return Marks(
        origin=origin,
        target=target,
        ride=ride,
        times=row_times(
            inputs=inputs,
            reach=reach,
            origin=origin,
            ride=ride,
        ),
        cursor=inputs.cursor,
        picking=inputs.picking,
        note=carry_note(trips=trips_of(inputs=inputs), reach=reach),
    )


def tag_of(*, index: int, marks: Marks, kind: RowKind) -> str:
    """
    Choose a row's tag.

    Parameters
    ----------
    index : int
        Row index.
    marks : Marks
        What the rows are marked with.
    kind : RowKind
        What the row shows.

    Returns
    -------
    str
        'TO?' or 'FROM?' on the cursor while picking, else ''. On the
        From while picking TO it reads ALL_TAG, as pressing there commits
        all departures, and on a closed fold OPEN_TAG, as pressing there
        opens it. The From and the Destination carry no other tag;
        their discs mark them.
    """
    if index != marks.cursor or marks.picking is None:
        return ''
    if marks.picking == PICK_TO and index == marks.origin:
        return ALL_TAG
    if marks.picking == PICK_TO and kind is RowKind.FOLD:
        return OPEN_TAG
    return f'{marks.picking}?'


def window_of(*, banner: bool, rows: int = STRIP_ROWS) -> Window:
    """
    Find where a strip's row slots sit.

    Parameters
    ----------
    banner : bool
        True while a live data banner shows under the header.
    rows : int
        Slots with no banner.

    Returns
    -------
    Window
        The slots from `STRIP_TOP`; under a banner they start below it,
        `BANNER_ROWS` fewer.
    """
    drop = BANNER_ROWS if banner else 0
    return Window(top=STRIP_TOP + drop * ROW_H, rows=rows - drop)


def inputs_window(*, inputs: StripInputs, rows: int = STRIP_ROWS) -> Window:
    """
    Find where the slots sit for a view's inputs.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs; their Stop view says whether a banner shows.
    rows : int
        Slots with no banner.

    Returns
    -------
    Window
        The slots.
    """
    return window_of(banner=inputs.stop_view.banner is not None, rows=rows)


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


def strip_row(
    *, reach: Reach, index: int, first: int, marks: Marks, window: Window,
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
    window : Window
        Where the slots sit.

    Returns
    -------
    StripRow
        The row; an exit row is named by `exit_name`, with no time, tag
        or bold.
    """
    row = reach.rows[index]
    parent = None if row.parent is None else reach.rows[row.parent]
    plain = row.kind is not RowKind.EXIT
    return StripRow(
        name=(
            row.place.name.removesuffix(' Station')
            if plain
            else exit_name(place=row.place, lines=row.lines)
        ),
        depth=row.depth,
        kind=row.kind,
        y=slot_y(index=index, first=first, window=window),
        parent_y=(
            None
            if row.parent is None
            else slot_y(index=row.parent, first=first, window=window)
        ),
        parent_depth=row.depth if parent is None else parent.depth,
        tag=tag_of(index=index, marks=marks, kind=row.kind) if plain else '',
        time=marks.times.get(index, '') if plain else '',
        ride=index in marks.ride,
        cursor=index == marks.cursor,
        bold=plain and index in (marks.origin, marks.target),
        station=row.place.stop_id,
        note=(
            marks.note[1]
            if marks.note is not None and marks.note[0] == index
            else None
        ),
    )


def home_of(*, reach: Reach, destination: str | None, rows: int) -> int:
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
    if high - low < rows:
        return min(max(start, high + 1 - rows), low)
    return origin if high > origin else origin + 1 - rows


def first_row(
    *, scroll: int, cursor: int | None, total: int, home: int, rows: int,
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

    Returns
    -------
    int
        The home row moved by the scroll, then moved to keep any cursor
        `CURSOR_MARGIN` rows from the edges, clamped to the rows.
    """
    start = home + scroll
    if cursor is not None:
        start = min(
            max(start, cursor + CURSOR_MARGIN + 1 - rows),
            cursor - CURSOR_MARGIN,
        )
    return max(0, min(start, total - rows))


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
    name = reach.rows[origin].place.name.removesuffix(' Station')
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
    window = inputs_window(inputs=inputs)
    home = home_of(
        reach=reach, destination=inputs.destination, rows=window.rows,
    )
    first = first_row(
        scroll=inputs.scroll,
        cursor=inputs.cursor,
        total=len(reach.rows),
        home=home,
        rows=window.rows,
    )
    band = band_of(reach=reach, first=first, window=window)
    indices = range(first, min(first + window.rows, len(reach.rows)))
    if band is not None:
        indices = indices[1:] if band.top else indices[:-1]
    marks = marks_of(inputs=inputs, reach=reach)
    rows = tuple(
        strip_row(
            reach=reach, index=index, first=first, marks=marks, window=window,
        )
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


def spot_of(*, row: StripRow, track_x: int) -> Spot:
    """
    Give where a row's vehicles sit.

    Parameters
    ----------
    row : StripRow
        The row.
    track_x : int
        Track x at depth 0.

    Returns
    -------
    Spot
        On the row's track, or for an exit mid-way along its hook's
        straight.
    """
    if row.kind is not RowKind.EXIT:
        return Spot(x=track_x + INDENT * row.depth, y=row.y)
    left, right = hook_straight(track_x=track_x + INDENT * row.parent_depth)
    return Spot(x=(left + right) / 2, y=row.y, exit=True)


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
        One badge per strip trip that can be placed, on its own hop and
        off the names, the From and Destination discs and each other.
    """
    spots = {
        row.station: spot_of(row=row, track_x=track_x)
        for row in rows
    }
    return vehicle_markers(
        trips=trips_of(inputs=inputs),
        keep=Keepouts(
            spots=spots,
            discs=[spots[row.station] for row in rows if row.bold],
            top=min((row.y for row in rows), default=0.0) - ROW_H / 2,
        ),
    )


def names_clear(*, rows: Sequence[StripRow], markers: Sequence[Marker],
                track_x: int) -> tuple[StripRow, ...]:
    """
    Start each row's name past the badges beside its disc.

    Parameters
    ----------
    rows : Sequence[StripRow]
        Visible rows.
    markers : Sequence[Marker]
        Their badges.
    track_x : int
        Track x at depth 0.

    Returns
    -------
    tuple[StripRow, ...]
        The rows; one with a badge on or right of its track, centred within
        `NAME_REACH` of it or drawn into its name band, names where
        those badges end.
    """
    return tuple(
        replace(row, name_after=max(
            (
                marker.x + HALF_WIDE
                for marker in markers
                if marker.x >= track_x + INDENT * row.depth
                and (
                    abs(marker.y - row.y) < NAME_REACH
                    or level_with(marker=marker, spot=Spot(x=0, y=row.y))
                )
            ),
            default=None,
        ))
        for row in rows
    )


def with_vehicles(*, inputs: StripInputs, rows: Sequence[StripRow],
                  track_x: int
                  ) -> tuple[tuple[StripRow, ...], tuple[Marker, ...]]:
    """
    Place the vehicles on the rows and keep the names clear of them.

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
    tuple[tuple[StripRow, ...], tuple[Marker, ...]]
        The rows, names past any badges beside their discs, and the
        badges.
    """
    markers = strip_markers(inputs=inputs, rows=rows, track_x=track_x)
    return (
        names_clear(rows=rows, markers=markers, track_x=track_x),
        markers,
    )


def on_fold(*, inputs: StripInputs) -> bool:
    """
    Say whether the TO cursor is on a closed fold.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.

    Returns
    -------
    bool
        True while picking TO with the cursor on a fold row, which a
        press opens.
    """
    reach, cursor = inputs.reach, inputs.cursor
    return (
        inputs.picking == PICK_TO
        and reach is not None
        and cursor is not None
        and 0 <= cursor < len(reach.rows)
        and reach.rows[cursor].kind is RowKind.FOLD
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
    hint = OPEN_HINT if on_fold(inputs=inputs) else (
        None if inputs.picking is None else HINTS.get(inputs.picking)
    )
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
    rows, markers = with_vehicles(inputs=inputs, rows=rows, track_x=TRACK_X)
    return StripView(
        origin=stop.origin,
        destination=stop.destination,
        clock=clock_text(moment=inputs.now),
        tag=inputs.tag,
        picking=inputs.picking,
        rows=rows,
        band=band,
        markers=markers,
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
        Up to `PANE_ROWS` rows, one fewer below a banner, with the From
        in the third slot, or the top slot as far up as it is, and their
        vehicles; nothing when there is no strip.
    """
    reach = shown_reach(inputs=inputs)
    if reach is None:
        return StripPane(rows=(), markers=())
    marks = marks_of(inputs=replace(inputs, cursor=None), reach=reach)
    window = inputs_window(inputs=inputs, rows=PANE_ROWS)
    first = max(
        0, 0 if marks.origin is None else marks.origin - PANE_FROM_ROW,
    )
    rows = tuple(
        strip_row(
            reach=reach, index=index, first=first, marks=marks, window=window,
        )
        for index in range(first, min(first + window.rows, len(reach.rows)))
    )
    rows, markers = with_vehicles(
        inputs=inputs, rows=rows, track_x=PANE_TRACK_X,
    )
    return StripPane(rows=rows, markers=markers)
