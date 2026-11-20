"""The Strip and Strip + map views' content: rows, times, vehicles, panel."""

from collections.abc import Sequence
from dataclasses import replace

from paperroute_device.core.text import clock_text
from paperroute_transit.core.map_view import Marker
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach import Reach, ReachRow, RowKind
from paperroute_transit.core.reach_exits import exit_name
from paperroute_transit.core.strip_badges import (
    HALF_WIDE,
    NAME_REACH,
    Keepouts,
    Spot,
    level_with,
)
from paperroute_transit.core.strip_follow import (
    followed_note,
    origin_of,
    picked_calls,
)
from paperroute_transit.core.strip_list import listed_rows
from paperroute_transit.core.strip_model import (
    ALL_TAG,
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
    TRACK_X,
    Band,
    CarryNote,
    Marks,
    StripInputs,
    StripPane,
    StripPanel,
    StripRow,
    StripView,
    TripDay,
    Window,
    hook_straight,
    selected_index,
)
from paperroute_transit.core.strip_notes import carry_note
from paperroute_transit.core.strip_slots import (
    band_of,
    destination_station,
    first_row,
    home_of,
    inputs_window,
    ride_of,
    slot_y,
    target_of,
)
from paperroute_transit.core.strip_times import (
    TripInputs,
    shown_item,
    trip_text,
)
from paperroute_transit.core.strip_vehicles import vehicle_markers


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


def row_times(
    *,
    inputs: StripInputs,
    reach: Reach,
    origin: int | None,
) -> dict[int, str]:
    """
    Choose the clock time each row shows.

    Every time comes from one departure: the picked train, else the next
    to the Destination (or the next at all, with none).

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    reach : Reach
        The reach.
    origin : int | None
        The From row; with none no row has a time.

    Returns
    -------
    dict[int, str]
        Clock time by row: the From has the departure's time, and each
        other row the time it calls there, after the From or still to
        come before it. Rows below the From laid out along the next
        trains have the picked train's times by its place among them.
        Rows it skips, has passed or doesn't reach have none.
    """
    if origin is None:
        return {}
    trips = trips_of(inputs=inputs)
    target = destination_station(
        reach=reach, destination=inputs.destination,
    )
    if reach.trip is not None:
        return followed_times(
            trips=trips, reach=reach, target=target, selected=inputs.selected,
        )
    item = shown_item(
        trips=trips,
        origin=reach.rows[origin].place.stop_id,
        target=target,
        selected=inputs.selected,
    )
    if item is None:
        return {}
    return {
        **trip_text(
            item=item, trips=trips, reach=reach, rows=range(len(reach.rows)),
        ),
        origin: clock_text(moment=item.departs, zone=inputs.zone),
    }


def followed_times(
    *,
    trips: TripInputs,
    reach: Reach,
    target: str | None,
    selected: TripDay | None,
) -> dict[int, str]:
    """
    Time the rows of a reach laid out along the next trains.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, laid out by `followed_reach`.
    target : str | None
        The Destination station ID, or None.
    selected : TripDay | None
        The picked train, or None.

    Returns
    -------
    dict[int, str]
        Clock time by row: the picked train's at the From, at its calls
        still to come above the From, and at the line rows below it;
        the next train's at the From and above when the rows no longer
        match the trains.
    """
    origin = origin_of(reach=reach) or 0
    picked = picked_calls(
        trips=trips, reach=reach, target=target, selected=selected,
    )
    item = picked[0] if picked is not None else shown_item(
        trips=trips,
        origin=reach.rows[origin].place.stop_id,
        target=target,
        selected=selected,
    )
    if item is None:
        return {}
    below = {} if picked is None else picked[1]
    return {
        **trip_text(item=item, trips=trips, reach=reach, rows=range(origin)),
        **{
            index: clock_text(moment=at, zone=trips.zone)
            for index, at in below.items()
        },
        origin: clock_text(moment=item.departs, zone=trips.zone),
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
        The From, Destination, ride, times, cursor and carry-on note.
    """
    origin = origin_of(reach=reach)
    target = target_of(reach=reach, destination=inputs.destination)
    ride = ride_of(reach=reach, target=target)
    return Marks(
        origin=origin,
        target=target,
        ride=ride,
        times=row_times(inputs=inputs, reach=reach, origin=origin),
        cursor=inputs.cursor,
        picking=inputs.picking,
        note=note_of(inputs=inputs, reach=reach),
    )


def note_of(*, inputs: StripInputs, reach: Reach
            ) -> tuple[int, CarryNote] | None:
    """
    Place the strip's carry-on note.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    reach : Reach
        The reach.

    Returns
    -------
    tuple[int, CarryNote] | None
        On rows below the From laid out along the next trains, the
        change of the picked train, or the next with none picked; else
        the next strip run's change. None when there is none.
    """
    trips = trips_of(inputs=inputs)
    if reach.trip is None:
        return carry_note(trips=trips, reach=reach)
    return followed_note(
        trips=trips,
        reach=reach,
        target=destination_station(
            reach=reach, destination=inputs.destination,
        ),
        selected=inputs.selected,
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
        zone=inputs.zone,
        stale_after=inputs.stale_after,
        station_of={} if inputs.reach is None else inputs.reach.station_of,
        line_kinds=inputs.line_kinds,
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
        The From, its Destination line and the first departures: five,
        one fewer under a hint and one fewer under a banner, none while
        picking a From; the Stop view's empty text when there are none
        to list. The picked train's row is marked off the chooser.
    """
    stop = inputs.stop_view
    hint = OPEN_HINT if on_fold(inputs=inputs) else (
        None if inputs.picking is None else HINTS.get(inputs.picking)
    )
    shown = PANEL_ROWS - (hint is not None) - (stop.banner is not None)
    return StripPanel(
        title=stop.origin,
        destination=stop.destination,
        rows=listed_rows(inputs=inputs, shown=shown),
        no_destination=stop.destination is None,
        hint=hint,
        empty=None if inputs.picking == PICK_FROM else stop.empty,
        selected=None if inputs.picking is not None else selected_index(
            selected=inputs.selected, trains=stop.head_departures[:shown],
        ),
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
        clock=clock_text(moment=inputs.now, zone=inputs.zone),
        tag=inputs.tag,
        picking=inputs.picking,
        rows=rows,
        band=band,
        markers=markers,
        panel=strip_panel(inputs=inputs),
        empty=empty_of(inputs=inputs),
        banner=stop.banner,
        warning=stop.warning,
        more=stop.more,
        planned=stop.planned,
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
