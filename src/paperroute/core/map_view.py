"""The Map view's content: the base map key, vehicle markers and sidebar."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.connection_view import PairRow
from paperroute.core.geo import Point, Shape
from paperroute.core.model import Departure
from paperroute.core.patterns import TripPattern, timetable_point
from paperroute.core.positions import PositionsSnapshot, VehiclePosition
from paperroute.core.ridden import route_stops, stops_window
from paperroute.core.stop_view import NOT_PINNED, PinTag, Row, StopView
from paperroute.core.tiles import ZOOMS, to_pixels

MAP_WIDTH: Final[int] = 520
MAP_HEIGHT: Final[int] = 440
STRIP_MAP_WIDTH: Final[int] = 480
DEFAULT_ZOOM: Final[int] = 2
POSITION_STALE_AFTER: Final[timedelta] = timedelta(minutes=2)
TIMETABLE_STEP: Final[timedelta] = timedelta(seconds=30)
NO_LOCATION: Final[str] = 'No location for this stop'
VISIBLE_MARGIN: Final[float] = 20.0  # pixels
NEARBY_SCALE: Final[int] = 3


@dataclass(frozen=True)
class TripMarks:
    """The marks a connection draws beyond leg 1's line.

    Attributes
    ----------
    origin : Point | None
        The From, or None without a location.
    second : tuple[tuple[Point, ...], ...]
        Every second ride's lines in metres, drawn dashed.
    walk : tuple[Point, Point] | None
        Where the walk starts and ends, or None when staying on.
    change : Point | None
        The walk's midpoint, or None when staying on.
    end : Point | None
        The To, or None without a location.
    others : tuple[tuple[Point, Point], ...]
        Each later change's walk, drawn like the first with its own
        change mark at the midpoint.
    """

    origin: Point | None
    second: tuple[tuple[Point, ...], ...]
    walk: tuple[Point, Point] | None
    change: Point | None
    end: Point | None
    others: tuple[tuple[Point, Point], ...] = ()


@dataclass(frozen=True)
class MapBase:
    """What the base map is drawn from; it keys the render cache.

    Attributes
    ----------
    centre : Point | None
        The map centre in metres, or None when the stop has no location.
    mpp : int
        Metres per pixel.
    route : tuple[tuple[Point, ...], ...]
        The pin's route lines in metres.
    width : int
        Map width in pixels.
    trip : TripMarks | None
        The From, To and a connection's other marks, or None for a plain
        pin with no From point.
    stops : tuple[Point, ...]
        The stops the drawn departures are ridden between, each once.
    """

    centre: Point | None
    mpp: int
    route: tuple[tuple[Point, ...], ...]
    width: int = MAP_WIDTH
    trip: TripMarks | None = None
    stops: tuple[Point, ...] = ()


@dataclass(frozen=True)
class Marker:
    """One vehicle as drawn.

    Attributes
    ----------
    route : str
        Route label.
    x : float
        Map pixels from the left.
    y : float
        Map pixels from the top.
    bearing : float | None
        Heading in degrees, or None for no wedge.
    live : bool
        True at a fresh reported position, False at a timetable position.
    pinned : bool
        True when the route is one of the pin's.
    age : float | None
        Seconds since a stale vehicle was last heard, floored to whole
        minutes; None for a fresh vehicle or a trip that never reported.
    """

    route: str
    x: float
    y: float
    bearing: float | None
    live: bool
    pinned: bool
    age: float | None = None


@dataclass(frozen=True)
class MapView:
    """
    Everything the Map view draws.

    With `panes`, this map and each pane are drawn side by side across
    the frame, with no sidebar.
    """

    origin: str
    destination: str | None
    clock: str
    base: MapBase
    zoom: int
    markers: tuple[Marker, ...]
    sidebar: tuple[Row, ...]
    banner: str | None
    empty: str | None
    tag: PinTag = NOT_PINNED
    warning: bool = False
    more: int = 0
    planned: bool = False
    pairs: tuple[PairRow, ...] = ()
    panes: tuple['MapView', ...] = ()


@dataclass(frozen=True)
class MapInputs:
    """Everything the Map view is built from.

    Attributes
    ----------
    stop_view : StopView
        The pin's Stop view, for the header, banner and sidebar.
    departures : tuple[Departure, ...]
        The pin's departures.
    centre : Point | None
        The From stop in metres, or None when it has no location.
    zoom : int
        Index into `ZOOMS`.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed in use.
    patterns : Mapping[str, TripPattern | None]
        Loaded trip patterns by requested trip ID; None when unknown.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.
    now : datetime
        Current time.
    width : int
        Map width in pixels.
    vehicle_trips : frozenset[str] | None
        When set, only these trips get vehicle markers.
    ends : tuple[Point | None, Point | None]
        The pin's From and To points; None where missing.
    step : int
        Knob detents from the fitted zoom; negative is finer.
    asleep : bool
        True for the sleep screen, whose band covers the map's bottom.
    """

    stop_view: StopView
    departures: tuple[Departure, ...]
    centre: Point | None
    zoom: int
    positions: Mapping[str, PositionsSnapshot]
    patterns: Mapping[str, TripPattern | None]
    shapes: Mapping[str, Shape]
    now: datetime
    width: int = MAP_WIDTH
    vehicle_trips: frozenset[str] | None = None
    ends: tuple[Point | None, Point | None] = (None, None)
    step: int = 0
    asleep: bool = False


def map_window(
    *,
    centre: Point,
    mpp: int,
    width: int = MAP_WIDTH,
) -> tuple[int, int]:
    """
    Find the global pixel of the map's top-left corner.

    Parameters
    ----------
    centre : Point
        The map centre in metres.
    mpp : int
        Metres per pixel.
    width : int
        Map width in pixels.

    Returns
    -------
    tuple[int, int]
        Left and top, in whole global pixels.
    """
    x, y = to_pixels(point=centre, mpp=mpp)
    return math.floor(x - width / 2), math.floor(y - MAP_HEIGHT / 2)


def screen_point(
    *,
    centre: Point,
    mpp: int,
    point: Point,
    width: int = MAP_WIDTH,
) -> tuple[float, float]:
    """
    Convert metres to map pixels.

    Tiles, labels, the route line, the stop and the markers all use this,
    so they agree to the sub-pixel.

    Parameters
    ----------
    centre : Point
        The map centre in metres.
    mpp : int
        Metres per pixel.
    point : Point
        The point in metres.
    width : int
        Map width in pixels.

    Returns
    -------
    tuple[float, float]
        Pixels from the map's left and top edges.
    """
    left, top = map_window(centre=centre, mpp=mpp, width=width)
    x, y = to_pixels(point=point, mpp=mpp)
    return x - left, y - top


def visible(*, at: tuple[float, float], width: int = MAP_WIDTH) -> bool:
    """
    Check whether a map pixel is on the map, with a small margin.

    Parameters
    ----------
    at : tuple[float, float]
        Map pixels.
    width : int
        Map width in pixels.

    Returns
    -------
    bool
        True within 20 px of the map rectangle.
    """
    return (
        -VISIBLE_MARGIN <= at[0] <= width + VISIBLE_MARGIN
        and -VISIBLE_MARGIN <= at[1] <= MAP_HEIGHT + VISIBLE_MARGIN
    )


def nearby(*, centre: Point, mpp: int, point: Point) -> bool:
    """
    Check whether a point lies within three times the map rectangle.

    Parameters
    ----------
    centre : Point
        The map centre in metres.
    mpp : int
        Metres per pixel.
    point : Point
        The point in metres.

    Returns
    -------
    bool
        True inside a rectangle three map widths by three map heights,
        centred on the map.
    """
    x, y = screen_point(centre=centre, mpp=mpp, point=point)
    reach = (NEARBY_SCALE - 1) / 2
    return (
        -MAP_WIDTH * reach <= x <= MAP_WIDTH * (1 + reach)
        and -MAP_HEIGHT * reach <= y <= MAP_HEIGHT * (1 + reach)
    )


def position_age(
    *,
    vehicle: VehiclePosition,
    snapshot: PositionsSnapshot,
    now: datetime,
) -> float:
    """
    Find how long ago a vehicle reported, on the feed server's clock.

    Parameters
    ----------
    vehicle : VehiclePosition
        The vehicle; a timestamp of 0 counts as the feed header time.
    snapshot : PositionsSnapshot
        The download it came in.
    now : datetime
        Current time.

    Returns
    -------
    float
        The header time minus the vehicle's timestamp, plus the seconds
        since the download.
    """
    lag = snapshot.header_time - (vehicle.timestamp or snapshot.header_time)
    return lag + (now - snapshot.fetched_at).total_seconds()


def timetable_moment(*, now: datetime) -> datetime:
    """
    Floor a time to the timetable position step.

    Parameters
    ----------
    now : datetime
        Aware time.

    Returns
    -------
    datetime
        The time floored to `TIMETABLE_STEP`, in the same zone.
    """
    step = TIMETABLE_STEP.total_seconds()
    floored = math.floor(now.timestamp() / step) * step
    return datetime.fromtimestamp(floored, tz=now.tzinfo)


def pin_trips(*, departures: Sequence[Departure]) -> frozenset[str]:
    """
    Collect the pin's trip IDs and their aliases.

    Parameters
    ----------
    departures : Sequence[Departure]
        The pin's departures.

    Returns
    -------
    frozenset[str]
        Every trip ID and alias.
    """
    return frozenset(
        trip
        for departure in departures
        for trip in (departure.trip_id, *departure.aliases)
    )


def pinned_routes(*, departures: Sequence[Departure]) -> frozenset[str]:
    """
    Collect the pin's route short names.

    A nearby route with the same short name is styled as pinned too.

    Parameters
    ----------
    departures : Sequence[Departure]
        The pin's departures.

    Returns
    -------
    frozenset[str]
        Route short names.
    """
    return frozenset(departure.route for departure in departures)


def route_lines(
    *,
    departures: Sequence[Departure],
    shapes: Mapping[str, Shape],
) -> tuple[tuple[Point, ...], ...]:
    """
    Collect the pin's route lines.

    Parameters
    ----------
    departures : Sequence[Departure]
        The pin's departures.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.

    Returns
    -------
    tuple[tuple[Point, ...], ...]
        The points of each distinct loaded shape, in departure order. A
        shape ID with no loaded shape is skipped.
    """
    shape_ids = dict.fromkeys(
        departure.shape_id
        for departure in departures
        if departure.shape_id is not None and departure.shape_id in shapes
    )
    return tuple(shapes[shape_id].points for shape_id in shape_ids)


def shape_of(
    *,
    pattern: TripPattern,
    shapes: Mapping[str, Shape],
) -> Shape | None:
    """
    Find a trip's loaded shape.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.

    Returns
    -------
    Shape | None
        The shape, or None when the trip has none or it isn't loaded.
    """
    return shapes.get(pattern.shape_id) if pattern.shape_id else None


def heard(*, inputs: MapInputs) -> tuple[tuple[VehiclePosition, float], ...]:
    """
    List every reported vehicle with its age.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    tuple[tuple[VehiclePosition, float], ...]
        Each vehicle and the seconds since it reported. Of several vehicles
        on one trip only the most recently heard is kept; vehicles with no
        trip ID are all kept.
    """
    best: dict[tuple[str, int], tuple[VehiclePosition, float]] = {}
    for feed, snapshot in inputs.positions.items():
        for index, vehicle in enumerate(snapshot.vehicles):
            key = (vehicle.trip_id, -1) if vehicle.trip_id else (feed, index)
            age = position_age(
                vehicle=vehicle,
                snapshot=snapshot,
                now=inputs.now,
            )
            if key not in best or age < best[key][1]:
                best[key] = (vehicle, age)
    return tuple(best.values())


def is_fresh(*, age: float) -> bool:
    """
    Check whether a position is recent enough to draw where it is.

    Parameters
    ----------
    age : float
        Seconds since the vehicle reported.

    Returns
    -------
    bool
        True at 120 s or under.
    """
    return age <= POSITION_STALE_AFTER.total_seconds()


def uncovered_pins(
    *,
    departures: Sequence[Departure],
    covered: frozenset[str],
) -> tuple[Departure, ...]:
    """
    Find the departures with no vehicle under their ID or an alias.

    Parameters
    ----------
    departures : Sequence[Departure]
        The pin's departures.
    covered : frozenset[str]
        Trip IDs that already have a vehicle.

    Returns
    -------
    tuple[Departure, ...]
        The departures left over.
    """
    return tuple(
        departure
        for departure in departures
        if covered.isdisjoint((departure.trip_id, *departure.aliases))
    )


def needed_patterns(*, inputs: MapInputs) -> frozenset[str]:
    """
    List the trips whose patterns the map needs.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    frozenset[str]
        Pin trips without a fresh vehicle, and the trips of stale vehicles
        within three times the map rectangle. Empty with no centre.
    """
    if inputs.centre is None:
        return frozenset()
    centre, mpp = inputs.centre, ZOOMS[inputs.zoom]
    vehicles = heard(inputs=inputs)
    fresh = frozenset(v.trip_id for v, age in vehicles if is_fresh(age=age))
    stale = frozenset(
        vehicle.trip_id
        for vehicle, age in vehicles
        if not is_fresh(age=age)
        and nearby(centre=centre, mpp=mpp, point=vehicle.point)
    )
    pins = uncovered_pins(departures=inputs.departures, covered=fresh)
    return stale | {departure.trip_id for departure in pins}


def live_marker(
    *,
    vehicle: VehiclePosition,
    inputs: MapInputs,
    centre: Point,
    pinned: frozenset[str],
) -> Marker:
    """
    Mark a fresh vehicle where it reported.

    Parameters
    ----------
    vehicle : VehiclePosition
        The vehicle.
    inputs : MapInputs
        The map's inputs.
    centre : Point
        The map centre in metres.
    pinned : frozenset[str]
        The pin's route short names.

    Returns
    -------
    Marker
        A live marker with the vehicle's bearing.
    """
    x, y = screen_point(
        centre=centre,
        mpp=ZOOMS[inputs.zoom],
        point=vehicle.point,
        width=inputs.width,
    )
    return Marker(
        route=vehicle.label,
        x=x,
        y=y,
        bearing=vehicle.bearing,
        live=True,
        pinned=vehicle.label in pinned,
    )


def timetable_marker(
    *,
    pattern: TripPattern | None,
    inputs: MapInputs,
    centre: Point,
    age: float | None,
    pinned: frozenset[str],
) -> Marker | None:
    """
    Mark a trip at its timetable position.

    Parameters
    ----------
    pattern : TripPattern | None
        The trip, or None when it isn't in the timetable.
    inputs : MapInputs
        The map's inputs.
    centre : Point
        The map centre in metres.
    age : float | None
        Seconds since the trip's vehicle reported, or None if it never did.
    pinned : frozenset[str]
        The pin's route short names.

    Returns
    -------
    Marker | None
        A hollow marker without a wedge, or None when the trip is unknown
        or not running.
    """
    if pattern is None:
        return None
    point = timetable_point(
        pattern=pattern,
        shape=shape_of(pattern=pattern, shapes=inputs.shapes),
        now=timetable_moment(now=inputs.now),
    )
    if point is None:
        return None
    x, y = screen_point(
        centre=centre,
        mpp=ZOOMS[inputs.zoom],
        point=point,
        width=inputs.width,
    )
    return Marker(
        route=pattern.route,
        x=x,
        y=y,
        bearing=None,
        live=False,
        pinned=pattern.route in pinned,
        age=age,
    )


def stale_marker(
    *,
    vehicle: VehiclePosition,
    age: float,
    inputs: MapInputs,
    centre: Point,
    pinned: frozenset[str],
) -> Marker | None:
    """
    Mark a stale vehicle at its trip's timetable position.

    Parameters
    ----------
    vehicle : VehiclePosition
        The vehicle.
    age : float
        Seconds since it reported.
    inputs : MapInputs
        The map's inputs.
    centre : Point
        The map centre in metres.
    pinned : frozenset[str]
        The pin's route short names.

    Returns
    -------
    Marker | None
        A hollow marker carrying the age floored to whole minutes, so the
        view only changes when the shown age does, or None when the trip
        is unknown or not running.
    """
    return timetable_marker(
        pattern=inputs.patterns.get(vehicle.trip_id),
        inputs=inputs,
        centre=centre,
        age=age // 60 * 60,
        pinned=pinned,
    )


def vehicle_markers(
    *,
    inputs: MapInputs,
    centre: Point,
    pinned: frozenset[str],
) -> tuple[tuple[str, Marker], ...]:
    """
    Mark every reported vehicle that can be drawn.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    centre : Point
        The map centre in metres.
    pinned : frozenset[str]
        The pin's route short names.

    Returns
    -------
    tuple[tuple[str, Marker], ...]
        Each drawn vehicle's trip ID and marker; with `vehicle_trips`
        set, only vehicles on those trips.
    """
    marked = (
        (
            vehicle.trip_id,
            live_marker(
                vehicle=vehicle,
                inputs=inputs,
                centre=centre,
                pinned=pinned,
            )
            if is_fresh(age=age)
            else stale_marker(
                vehicle=vehicle,
                age=age,
                inputs=inputs,
                centre=centre,
                pinned=pinned,
            ),
        )
        for vehicle, age in heard(inputs=inputs)
        if inputs.vehicle_trips is None
        or vehicle.trip_id in inputs.vehicle_trips
    )
    return tuple(
        (trip, marker) for trip, marker in marked if marker is not None
    )


def pin_markers(
    *,
    inputs: MapInputs,
    centre: Point,
    covered: frozenset[str],
    pinned: frozenset[str],
) -> tuple[Marker, ...]:
    """
    Mark the pin's running trips that no vehicle stands for.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    centre : Point
        The map centre in metres.
    covered : frozenset[str]
        Trip IDs that already have a marker.
    pinned : frozenset[str]
        The pin's route short names.

    Returns
    -------
    tuple[Marker, ...]
        Hollow markers without an age.
    """
    marked = (
        timetable_marker(
            pattern=inputs.patterns.get(departure.trip_id),
            inputs=inputs,
            centre=centre,
            age=None,
            pinned=pinned,
        )
        for departure in uncovered_pins(
            departures=inputs.departures,
            covered=covered,
        )
    )
    return tuple(marker for marker in marked if marker is not None)


def markers_for(*, inputs: MapInputs, centre: Point) -> tuple[Marker, ...]:
    """
    Mark every vehicle and pin trip on the map, in drawing order.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.
    centre : Point
        The map centre in metres.

    Returns
    -------
    tuple[Marker, ...]
        The visible markers, pinned routes first, then by distance from
        the centre.
    """
    pinned = pinned_routes(departures=inputs.departures)
    drawn = vehicle_markers(inputs=inputs, centre=centre, pinned=pinned)
    pins = pin_markers(
        inputs=inputs,
        centre=centre,
        covered=frozenset(trip for trip, _ in drawn),
        pinned=pinned,
    )
    middle = screen_point(
        centre=centre,
        mpp=ZOOMS[inputs.zoom],
        point=centre,
        width=inputs.width,
    )
    shown = [
        marker
        for marker in (*(marker for _, marker in drawn), *pins)
        if visible(at=(marker.x, marker.y), width=inputs.width)
    ]
    return tuple(
        sorted(
            shown,
            key=lambda marker: (
                not marker.pinned,
                math.dist((marker.x, marker.y), middle),
            ),
        ),
    )


def map_departures(
    *, stop_view: StopView, departures: Sequence[Departure]
) -> tuple[Departure, ...]:
    """
    Choose the departures the Map draws and asks about.

    Parameters
    ----------
    stop_view : StopView
        The unscrolled Stop view.
    departures : Sequence[Departure]
        Every departure loaded.

    Returns
    -------
    tuple[Departure, ...]
        All of them with a destination; with none, only those behind the
        sidebar's rows.
    """
    if stop_view.destination is None:
        return stop_view.head_departures
    return tuple(departures)


def build_map_view(*, inputs: MapInputs) -> MapView:
    """
    Build the Map view.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    MapView
        The view.
    """
    stop = inputs.stop_view
    centre = inputs.centre
    return MapView(
        origin=stop.origin,
        destination=stop.destination,
        clock=stop.clock,
        base=MapBase(
            centre=centre,
            mpp=ZOOMS[inputs.zoom],
            route=route_lines(
                departures=inputs.departures,
                shapes=inputs.shapes,
            ),
            width=inputs.width,
            trip=plain_marks(inputs=inputs),
            stops=route_stops(
                departures=inputs.departures,
                patterns=inputs.patterns,
                window=stops_window(destination=stop.destination),
            ),
        ),
        zoom=inputs.zoom,
        markers=(
            ()
            if centre is None
            else markers_for(inputs=inputs, centre=centre)
        ),
        sidebar=stop.head_rows,
        banner=stop.banner,
        empty=NO_LOCATION if centre is None else stop.empty,
        tag=stop.tag,
        warning=stop.warning,
        more=stop.more,
        planned=stop.planned,
    )


def plain_marks(*, inputs: MapInputs) -> TripMarks | None:
    """
    Mark a pin's From and To.

    Parameters
    ----------
    inputs : MapInputs
        The map's inputs.

    Returns
    -------
    TripMarks | None
        The From as the origin and the To as the end; None with no From
        point.
    """
    origin, end = inputs.ends
    if origin is None:
        return None
    return TripMarks(origin=origin, second=(), walk=None, change=None, end=end)
