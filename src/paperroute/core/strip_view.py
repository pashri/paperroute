"""The Strip and Strip + map views' content: rows, times, vehicles, panel."""

# pylint: disable=too-many-lines

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.map_view import (
    MapView,
    Marker,
    is_fresh,
    position_age,
    timetable_moment,
)
from paperroute.core.model import (
    Departure,
    StopPlace,
    service_seconds,
    service_time,
    shifted,
)
from paperroute.core.patterns import TripPattern, hop_at, hop_near
from paperroute.core.positions import PositionsSnapshot, VehiclePosition
from paperroute.core.reach import (
    Reach,
    ReachRow,
    RowKind,
    StripKey,
    every_route,
)
from paperroute.core.stop_view import (
    DOT,
    PinTag,
    Row,
    StopView,
    clock_text,
    not_departed,
)

STRIP_TOP: Final[int] = 44
ROW_H: Final[int] = 36
STRIP_ROWS: Final[int] = 12
TRACK_X: Final[int] = 30
INDENT: Final[int] = 16
PANE_TRACK_X: Final[int] = 500
PANE_ROWS: Final[int] = 12
PANE_FROM_ROW: Final[int] = 2
BADGE_STEP: Final[int] = 22
PANEL_ROWS: Final[int] = 4
CURSOR_MARGIN: Final[int] = 2
STRIP_LOOKBACK: Final[timedelta] = timedelta(minutes=60)
STRIP_AHEAD: Final[timedelta] = timedelta(minutes=30)
LOADING: Final[str] = 'Loading the line…'
NO_STRIP: Final[str] = 'No strip for this stop'
NEEDS_UPDATE: Final[str] = 'Strip needs a timetable update'
HINTS: Final[dict[str, str]] = {
    'TO': f'Press: set TO{DOT}press FROM: all departures',
    'FROM': f'Press: set FROM{DOT}top press: back',
}
PICK_FROM: Final[str] = 'FROM'
EMPTY_STOP_VIEW: Final[StopView] = StopView(
    origin='',
    destination=None,
    clock='',
    hero=None,
    rows=(),
    banner=None,
    empty=None,
    scroll=0,
)
type Heard = Mapping[str, tuple[VehiclePosition, float]]


@dataclass(frozen=True)
class StripRow:
    """
    One station as the strip draws it.

    Attributes
    ----------
    name : str
        Station name, without ' Station'.
    depth : int
        Indent: 0 for the main line, one more per branch level.
    kind : RowKind
        Approach, the From, the main line or a branch.
    y : float
        Screen y of the row's centre line.
    parent_y : float | None
        Screen y of the row the track comes from, which may be off the
        strip; None for the From.
    parent_depth : int
        Depth of that row; the row's own depth when it has none.
    tag : str
        'FROM', 'TO', 'TO?', 'FROM?' or ''.
    time : str
        Clock time of the first arrival, or ''.
    ride : bool
        True on the ridden rows, From to Destination.
    cursor : bool
        True on the row being picked.
    bold : bool
        True for the From and the Destination.
    station : str
        The row's station ID.
    """

    name: str
    depth: int
    kind: RowKind
    y: float
    parent_y: float | None
    parent_depth: int
    tag: str
    time: str
    ride: bool
    cursor: bool
    bold: bool
    station: str


@dataclass(frozen=True)
class Band:
    """
    The band standing in for a From scrolled off the strip.

    Attributes
    ----------
    text : str
        For example 'FROM Town Hall · 3 turns away'.
    top : bool
        True in the top slot, False in the bottom one.
    """

    text: str
    top: bool


@dataclass(frozen=True)
class StripPanel:
    """
    The departures panel beside the strip.

    Attributes
    ----------
    title : str
        The From name.
    subtitle : str | None
        '→ <Destination>', or None when the place has no destination.
    note : str
        How many stations the strip reaches.
    rows : tuple[Row, ...]
        Departures to show; each `Row.then` is drawn as a second line.
    no_destination : bool
        True when the rows are the whole-stop board.
    hint : str | None
        The knob hint while picking, else None.
    """

    title: str
    subtitle: str | None
    note: str
    rows: tuple[Row, ...]
    no_destination: bool
    hint: str | None


@dataclass(frozen=True)
class StripView:
    """
    Everything the Strip view draws.

    Attributes
    ----------
    origin : str
        From name.
    destination : str | None
        Destination name, or None.
    clock : str
        Current time.
    tag : PinTag
        The header's pin tag.
    picking : str | None
        'TO' or 'FROM' while the chooser picks a stop, else None.
    rows : tuple[StripRow, ...]
        Visible rows, top to bottom.
    band : Band | None
        The From band, when the From is off the strip.
    markers : tuple[Marker, ...]
        Vehicles on the strip.
    panel : StripPanel
        The departures panel.
    empty : str | None
        Why there is no strip, or None.
    banner : str | None
        Live data banner, or None.
    """

    origin: str
    destination: str | None
    clock: str
    tag: PinTag
    picking: str | None
    rows: tuple[StripRow, ...]
    band: Band | None
    markers: tuple[Marker, ...]
    panel: StripPanel
    empty: str | None
    banner: str | None


@dataclass(frozen=True)
class StripPane:
    """
    The strip beside the map on Strip + map.

    Attributes
    ----------
    rows : tuple[StripRow, ...]
        Visible rows, top to bottom.
    markers : tuple[Marker, ...]
        Vehicles on the strip.
    """

    rows: tuple[StripRow, ...]
    markers: tuple[Marker, ...]


@dataclass(frozen=True)
class StripMapView:
    """
    Everything the Strip + map view draws.

    Attributes
    ----------
    map : MapView
        The map at the left.
    pane : StripPane
        The strip at the right.
    """

    map: MapView
    pane: StripPane


@dataclass(frozen=True)
class StripInputs:
    """
    Everything the Strip views are built from.

    Attributes
    ----------
    reach : Reach | None
        The strip's rows, or None while loading or unavailable.
    unavailable : str | None
        Why there is no reach, or None.
    departures : tuple[LiveDeparture, ...]
        Every departure from the From, with live times applied.
    patterns : Mapping[str, TripPattern | None]
        Loaded trip patterns by trip ID; None when unknown.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed in use.
    now : datetime
        Current time.
    scroll : int
        Rows scrolled down from the default.
    cursor : int | None
        Reach row being picked, or None.
    picking : str | None
        'TO' or 'FROM' while the chooser picks a stop, else None.
    destination : str | None
        Destination stop or station ID, or None.
    tag : PinTag
        The header's pin tag.
    stop_view : StopView
        The place's unscrolled Stop view, for the header and panel.
    key : StripKey | None
        Which runs feed the strip; None for no departures.
    """

    reach: Reach | None
    unavailable: str | None
    departures: tuple[LiveDeparture, ...]
    patterns: Mapping[str, TripPattern | None]
    positions: Mapping[str, PositionsSnapshot]
    now: datetime
    scroll: int
    cursor: int | None
    picking: str | None
    destination: str | None
    tag: PinTag
    stop_view: StopView = EMPTY_STOP_VIEW
    key: StripKey | None = None


@dataclass(frozen=True)
class Marks:
    """
    What the rows of a reach are marked with.

    Attributes
    ----------
    origin : int | None
        The From row, if any.
    target : int | None
        The Destination row, if it is on the strip.
    ride : frozenset[int]
        Rows from the From to the Destination.
    times : Mapping[int, str]
        Clock time by row.
    cursor : int | None
        The row being picked.
    picking : str | None
        'TO' or 'FROM' while picking.
    """

    origin: int | None
    target: int | None
    ride: frozenset[int]
    times: Mapping[int, str]
    cursor: int | None
    picking: str | None


def on_strip(*, departure: Departure, key: StripKey) -> bool:
    """
    Say whether a departure feeds a strip.

    Parameters
    ----------
    departure : Departure
        Departure.
    key : StripKey
        Strip kind and routes.

    Returns
    -------
    bool
        True when the key has no kind, or the departure is of its kind
        and, for a kind that takes only its routes, one of them.
    """
    if key.kind is None:
        return True
    if departure.facts.kind != key.kind:
        return False
    return every_route(key=key) or departure.route in key.routes


def strip_departures(
    *, departures: Sequence[Departure], key: StripKey,
) -> tuple[Departure, ...]:
    """
    Keep the departures of a strip's own kind or routes.

    Parameters
    ----------
    departures : Sequence[Departure]
        Every departure from the From.
    key : StripKey
        Strip kind and routes.

    Returns
    -------
    tuple[Departure, ...]
        The ones that feed the strip, in order.
    """
    return tuple(
        departure
        for departure in departures
        if on_strip(departure=departure, key=key)
    )


def strip_trips(
    *, departures: Sequence[Departure], key: StripKey, now: datetime,
) -> frozenset[str]:
    """
    List the trips whose vehicles the strip draws.

    Parameters
    ----------
    departures : Sequence[Departure]
        Every departure from the From.
    key : StripKey
        Strip kind and routes.
    now : datetime
        Current time.

    Returns
    -------
    frozenset[str]
        Trip IDs of the strip's departures that left in the last
        `STRIP_LOOKBACK` or leave in the next `STRIP_AHEAD`; every one.
    """
    return frozenset(
        departure.trip_id
        for departure in window_departures(
            departures=departures, key=key, now=now,
        )
    )


def window_departures(
    *, departures: Sequence[Departure], key: StripKey, now: datetime,
) -> list[Departure]:
    """
    Keep a strip's departures timetabled near now, one per trip.

    Parameters
    ----------
    departures : Sequence[Departure]
        Every departure from the From.
    key : StripKey
        Strip kind and routes.
    now : datetime
        Current time.

    Returns
    -------
    list[Departure]
        The first departure of each trip leaving from `STRIP_LOOKBACK`
        before now to `STRIP_AHEAD` after, in order.
    """
    start = (now - STRIP_LOOKBACK).timestamp()
    end = (now + STRIP_AHEAD).timestamp()
    kept: dict[str, Departure] = {}
    for departure in strip_departures(departures=departures, key=key):
        if start <= departure.departs.timestamp() <= end:
            kept.setdefault(departure.trip_id, departure)
    return list(kept.values())


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


def live_strip(*, inputs: StripInputs) -> list[LiveDeparture]:
    """
    Keep the live departures that feed the strip.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.

    Returns
    -------
    list[LiveDeparture]
        The strip's departures; none without a key.
    """
    key = inputs.key
    if key is None:
        return []
    return [
        item
        for item in inputs.departures
        if on_strip(departure=item.departure, key=key)
    ]


def from_call(*, departure: Departure, pattern: TripPattern, origin: str,
              stations: Mapping[str, str]) -> int | None:
    """
    Find the call at which a departure leaves the From.

    Parameters
    ----------
    departure : Departure
        Departure.
    pattern : TripPattern
        Its trip.
    origin : str
        The From station ID.
    stations : Mapping[str, str]
        Station ID by stop ID, for stops under a strip station.

    Returns
    -------
    int | None
        The index of the call at the From at the departure's time, else
        the first call at the From, else None.
    """
    at_from = [
        index
        for index, stop in enumerate(pattern.stops)
        if stations.get(stop.station, stop.station) == origin
    ]
    leaves = service_seconds(
        day=departure.service_day, moment=departure.departs,
    )
    timed = [i for i in at_from if pattern.stops[i].departs == leaves]
    return next(iter(timed or at_from), None)


def arrivals_after(
    *,
    item: LiveDeparture,
    pattern: TripPattern,
    origin: str,
    stations: Mapping[str, str],
) -> Iterator[tuple[str, datetime]]:
    """
    List the stations a departure calls at after the From, with times.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    pattern : TripPattern
        Its trip.
    origin : str
        The From station ID.
    stations : Mapping[str, str]
        Station ID by stop ID, for stops under a strip station.

    Yields
    ------
    tuple[str, datetime]
        Each later call's station and arrival, shifted by the delay.
    """
    departure = item.departure
    start = from_call(
        departure=departure, pattern=pattern, origin=origin, stations=stations,
    )
    if start is None:
        return
    delay = int(item.departs.timestamp() - departure.departs.timestamp())
    for stop in pattern.stops[start + 1:]:
        arrives = service_time(day=departure.service_day, seconds=stop.arrives)
        yield (
            stations.get(stop.station, stop.station),
            shifted(moment=arrives, seconds=delay),
        )


def first_arrivals(*, inputs: StripInputs, reach: Reach, origin: str
                   ) -> dict[str, datetime]:
    """
    Find the first arrival at each station after the From.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    reach : Reach
        The reach.
    origin : str
        The From station ID.

    Returns
    -------
    dict[str, datetime]
        Earliest arrival by station, among the strip's departures not yet
        gone and not cancelled whose patterns are loaded.
    """
    best: dict[str, datetime] = {}
    upcoming = not_departed(
        departures=live_strip(inputs=inputs), now=inputs.now,
    )
    for item in upcoming:
        pattern = inputs.patterns.get(item.departure.trip_id)
        if item.cancelled or pattern is None:
            continue
        for station, moment in arrivals_after(
            item=item,
            pattern=pattern,
            origin=origin,
            stations=reach.station_of,
        ):
            if station not in best or moment < best[station]:
                best[station] = moment
    return best


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
        inputs=inputs, reach=reach, origin=reach.rows[origin].place.stop_id,
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


def default_start(*, origin: int | None, scroll: int) -> int:
    """
    Find the unclamped top row for a scroll.

    Parameters
    ----------
    origin : int | None
        The From row, if any.
    scroll : int
        Rows scrolled down.

    Returns
    -------
    int
        The row that puts the From in the third slot, moved by the
        scroll; the scroll alone with no From.
    """
    return scroll + (0 if origin is None else origin - PANE_FROM_ROW)


def first_row(*, inputs: StripInputs, total: int, origin: int | None) -> int:
    """
    Choose the row in the Strip's top slot.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    total : int
        Rows in the reach.
    origin : int | None
        The From row, if any.

    Returns
    -------
    int
        The default top row moved by the scroll, then moved to keep any
        cursor `CURSOR_MARGIN` rows from the edges, clamped to the rows.
    """
    start = default_start(origin=origin, scroll=inputs.scroll)
    cursor = inputs.cursor
    if cursor is not None:
        start = min(
            max(start, cursor + CURSOR_MARGIN + 1 - STRIP_ROWS),
            cursor - CURSOR_MARGIN,
        )
    return max(0, min(start, total - STRIP_ROWS))


def turns_away(*, inputs: StripInputs, origin: int, top: bool) -> int:
    """
    Count the detents back to the From.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    origin : int
        The From row.
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
    start = default_start(origin=origin, scroll=inputs.scroll)
    if top:
        return start - origin
    return origin - (start + STRIP_ROWS - 1)


def band_of(*, inputs: StripInputs, reach: Reach, first: int
            ) -> Band | None:
    """
    Make the From band when the From is off the Strip.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.
    reach : Reach
        The reach.
    first : int
        The row in the top slot.

    Returns
    -------
    Band | None
        The band at the edge nearest the From, or None when the From is
        on the strip or there is none.
    """
    origin = origin_of(reach=reach)
    if origin is None or first <= origin < first + STRIP_ROWS:
        return None
    top = origin < first
    name = reach.rows[origin].place.name.removesuffix(' Station')
    turns = turns_away(inputs=inputs, origin=origin, top=top)
    return Band(text=f'FROM {name}{DOT}{turns} turns away', top=top)


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
    first = first_row(inputs=inputs, total=total, origin=marks.origin)
    band = band_of(inputs=inputs, reach=reach, first=first)
    indices = range(first, min(first + STRIP_ROWS, total))
    if band is not None:
        indices = indices[1:] if band.top else indices[:-1]
    rows = tuple(
        strip_row(reach=reach, index=index, first=first, marks=marks)
        for index in indices
    )
    return rows, band


def heard_trips(
    *, inputs: StripInputs,
) -> dict[str, tuple[VehiclePosition, float]]:
    """
    Find the most recently heard vehicle of each trip.

    Parameters
    ----------
    inputs : StripInputs
        The view's inputs.

    Returns
    -------
    dict[str, tuple[VehiclePosition, float]]
        Each trip's vehicle and the seconds since it reported.
    """
    best: dict[str, tuple[VehiclePosition, float]] = {}
    for snapshot in inputs.positions.values():
        for vehicle in snapshot.vehicles:
            age = position_age(
                vehicle=vehicle, snapshot=snapshot, now=inputs.now,
            )
            known = best.get(vehicle.trip_id)
            if known is None or age < known[1]:
                best[vehicle.trip_id] = (vehicle, age)
    return best


def vehicle_of(*, departure: Departure, heard: Heard
               ) -> tuple[VehiclePosition, float] | None:
    """
    Find a departure's vehicle by its trip ID or an alias.

    Parameters
    ----------
    departure : Departure
        Departure.
    heard : Heard
        Each trip's vehicle and age.

    Returns
    -------
    tuple[VehiclePosition, float] | None
        The freshest match, or None.
    """
    found = [
        heard[trip]
        for trip in (departure.trip_id, *departure.aliases)
        if trip in heard
    ]
    return min(found, key=lambda each: each[1], default=None)


def trip_marker(
    *,
    departure: Departure,
    inputs: StripInputs,
    rows: Mapping[str, StripRow],
    heard: Heard,
    track_x: int,
) -> Marker | None:
    """
    Place one trip's badge on the strip.

    Parameters
    ----------
    departure : Departure
        The trip's departure from the From.
    inputs : StripInputs
        The view's inputs.
    rows : Mapping[str, StripRow]
        Visible rows by station.
    heard : Heard
        Each trip's vehicle and age.
    track_x : int
        Track x at depth 0.

    Returns
    -------
    Marker | None
        A solid badge at a fresh position's nearest hop, else a hollow one
        at the timetable hop; None without a pattern, a hop, or both of
        the hop's rows.
    """
    pattern = inputs.patterns.get(departure.trip_id)
    if pattern is None:
        return None
    found = vehicle_of(departure=departure, heard=heard)
    fresh = found is not None and is_fresh(age=found[1])
    if found is not None and fresh:
        hop = hop_near(pattern=pattern, point=found[0].point)
    else:
        moment = timetable_moment(now=inputs.now)
        hop = hop_at(pattern=pattern, seconds=service_seconds(
            day=departure.service_day, moment=moment,
        ))
    age = None if found is None or fresh else found[1] // 60 * 60
    return hop_marker(
        pattern=pattern, hop=hop, rows=rows, track_x=track_x, age=age,
        live=fresh,
    )


def hop_marker(
    *,
    pattern: TripPattern,
    hop: tuple[int, float] | None,
    rows: Mapping[str, StripRow],
    track_x: int,
    age: float | None,
    live: bool,
) -> Marker | None:
    """
    Make a badge part way along a hop between two strip rows.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    hop : tuple[int, float] | None
        The stop last left and the share of the hop run.
    rows : Mapping[str, StripRow]
        Visible rows by station.
    track_x : int
        Track x at depth 0.
    age : float | None
        Seconds since a stale vehicle reported, or None.
    live : bool
        True for a fresh position.

    Returns
    -------
    Marker | None
        The badge on the later row's track, with a wedge down the strip
        when that row is lower and up otherwise, if live; None when either
        end of the hop is not a visible row.
    """
    if hop is None:
        return None
    index, fraction = hop
    start, end = (
        rows.get(stop.station) for stop in pattern.stops[index:index + 2]
    )
    if start is None or end is None:
        return None
    return Marker(
        route=pattern.route,
        x=track_x + INDENT * end.depth,
        y=start.y + (end.y - start.y) * fraction,
        bearing=(180.0 if end.y > start.y else 0.0) if live else None,
        live=live,
        pinned=True,
        age=age,
    )


def pushed(*, markers: Sequence[Marker]) -> tuple[Marker, ...]:
    """
    Push overlapping badges in a column down, in y order.

    Parameters
    ----------
    markers : Sequence[Marker]
        Badges.

    Returns
    -------
    tuple[Marker, ...]
        Badges by x then y, each moved down to at least `BADGE_STEP`
        below the one above it in its column.
    """
    placed: list[Marker] = []
    for marker in sorted(markers, key=lambda each: (each.x, each.y)):
        above = placed[-1] if placed else None
        y = marker.y
        if above is not None and above.x == marker.x:
            y = max(y, above.y + BADGE_STEP)
        placed.append(replace(marker, y=y))
    return tuple(placed)


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
    key = inputs.key
    if key is None or not rows:
        return ()
    by_station = {row.station: row for row in rows}
    heard = heard_trips(inputs=inputs)
    departures = window_departures(
        departures=[item.departure for item in inputs.departures],
        key=key,
        now=inputs.now,
    )
    placed = (
        trip_marker(departure=departure, inputs=inputs, rows=by_station,
                    heard=heard, track_x=track_x)
        for departure in departures
    )
    return pushed(markers=[each for each in placed if each is not None])


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
        departures: four, three under a hint, none while picking a From.
    """
    stop = inputs.stop_view
    hint = None if inputs.picking is None else HINTS.get(inputs.picking)
    shown = PANEL_ROWS - (hint is not None)
    reach = inputs.reach
    return StripPanel(
        title=stop.origin,
        subtitle=None if stop.destination is None else f'→ {stop.destination}',
        note='' if reach is None else f'{reach.stations} stations from here',
        rows=() if inputs.picking == PICK_FROM else stop.head_rows[:shown],
        no_destination=stop.destination is None,
        hint=hint,
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
    first = max(0, default_start(origin=marks.origin, scroll=0))
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
