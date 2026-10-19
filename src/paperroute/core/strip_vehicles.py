"""Which trips feed a strip, when they reach its stations, and where."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.map_view import (
    Marker,
    is_fresh,
    position_age,
    timetable_moment,
)
from paperroute.core.model import (
    Departure,
    service_seconds,
    service_time,
    shifted,
)
from paperroute.core.patterns import TripPattern, hop_at, hop_near
from paperroute.core.positions import PositionsSnapshot, VehiclePosition
from paperroute.core.reach import StripKey, every_route
from paperroute.core.stop_view import not_departed

BADGE_STEP: Final[int] = 22
STACK_REACH: Final[int] = BADGE_STEP  # furthest a badge is pushed down
NAME_REACH: Final[float] = 18.0  # half a strip row
BADGE_SPAN: Final[float] = 36.0  # badges nearer than this across overlap
STRIP_LOOKBACK: Final[timedelta] = timedelta(minutes=60)
STRIP_AHEAD: Final[timedelta] = timedelta(minutes=30)
type Heard = Mapping[str, tuple[VehiclePosition, float]]


@dataclass(frozen=True)
class TripInputs:
    """
    The departures and live data a strip's times and vehicles come from.

    Attributes
    ----------
    departures : tuple[LiveDeparture, ...]
        Every departure from the From, with live times applied.
    key : StripKey | None
        Which runs feed the strip; None for no departures.
    patterns : Mapping[str, TripPattern | None]
        Loaded trip patterns by trip ID; None when unknown.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed in use.
    now : datetime
        Current time.
    station_of : Mapping[str, str]
        The station ID of each stop under a strip station.
    """

    departures: tuple[LiveDeparture, ...]
    key: StripKey | None
    patterns: Mapping[str, TripPattern | None]
    positions: Mapping[str, PositionsSnapshot]
    now: datetime
    station_of: Mapping[str, str]


@dataclass(frozen=True)
class Spot:
    """
    Where a visible strip row's track is on screen.

    Attributes
    ----------
    x : float
        Track x at the row's depth.
    y : float
        The row's centre line.
    """

    x: float
    y: float


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


def live_strip(*, trips: TripInputs) -> list[LiveDeparture]:
    """
    Keep the live departures that feed the strip.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.

    Returns
    -------
    list[LiveDeparture]
        The strip's departures; none without a key.
    """
    key = trips.key
    if key is None:
        return []
    return [
        item
        for item in trips.departures
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


def first_arrivals(*, trips: TripInputs, origin: str
                   ) -> dict[str, datetime]:
    """
    Find the first arrival at each station after the From.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
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
        departures=live_strip(trips=trips), now=trips.now,
    )
    for item in upcoming:
        pattern = trips.patterns.get(item.departure.trip_id)
        if item.cancelled or pattern is None:
            continue
        for station, moment in arrivals_after(
            item=item,
            pattern=pattern,
            origin=origin,
            stations=trips.station_of,
        ):
            if station not in best or moment < best[station]:
                best[station] = moment
    return best


def heard_trips(
    *, trips: TripInputs,
) -> dict[str, tuple[VehiclePosition, float]]:
    """
    Find the most recently heard vehicle of each trip.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.

    Returns
    -------
    dict[str, tuple[VehiclePosition, float]]
        Each trip's vehicle and the seconds since it reported.
    """
    best: dict[str, tuple[VehiclePosition, float]] = {}
    for snapshot in trips.positions.values():
        for vehicle in snapshot.vehicles:
            age = position_age(
                vehicle=vehicle, snapshot=snapshot, now=trips.now,
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
    trips: TripInputs,
    spots: Mapping[str, Spot],
    heard: Heard,
) -> Marker | None:
    """
    Place one trip's badge on the strip.

    Parameters
    ----------
    departure : Departure
        The trip's departure from the From.
    trips : TripInputs
        The strip's departures and live data.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.
    heard : Heard
        Each trip's vehicle and age.

    Returns
    -------
    Marker | None
        A solid badge at a fresh position's nearest hop, else a hollow one
        at the timetable hop; None without a pattern, a hop, or both of
        the hop's rows.
    """
    pattern = trips.patterns.get(departure.trip_id)
    if pattern is None:
        return None
    found = vehicle_of(departure=departure, heard=heard)
    fresh = found is not None and is_fresh(age=found[1])
    if found is not None and fresh:
        hop = hop_near(pattern=pattern, point=found[0].point)
    else:
        moment = timetable_moment(now=trips.now)
        hop = hop_at(pattern=pattern, seconds=service_seconds(
            day=departure.service_day, moment=moment,
        ))
    ends = None if hop is None else tuple(
        spots.get(trips.station_of.get(stop.station, stop.station))
        for stop in pattern.stops[hop[0]:hop[0] + 2]
    )
    age = None if found is None or fresh else found[1] // 60 * 60
    return hop_marker(
        route=pattern.route,
        ends=ends,
        fraction=0.0 if hop is None else hop[1],
        age=age,
        live=fresh,
    )


def hop_marker(
    *,
    route: str,
    ends: tuple[Spot | None, ...] | None,
    fraction: float,
    age: float | None,
    live: bool,
) -> Marker | None:
    """
    Make a badge part way along a hop between two strip rows.

    Parameters
    ----------
    route : str
        Route label.
    ends : tuple[Spot | None, ...] | None
        The spots of the hop's two stops, each None when not a visible
        row; None with no hop.
    fraction : float
        Share of the hop run.
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
    if ends is None:
        return None
    start, end = ends
    if start is None or end is None:
        return None
    return Marker(
        route=route,
        x=end.x,
        y=start.y + (end.y - start.y) * fraction,
        bearing=(180.0 if end.y > start.y else 0.0) if live else None,
        live=live,
        pinned=True,
        age=age,
    )


def in_gutter(*, marker: Marker, spots: Mapping[str, Spot]) -> Marker:
    """
    Keep a badge off the names of the rows level with it.

    Parameters
    ----------
    marker : Marker
        Badge.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.

    Returns
    -------
    Marker
        The badge moved left onto the track of the shallowest row within
        `NAME_REACH` of it, whose name starts right of that track; as it
        was when no row that close sits further left.
    """
    level = (
        spot.x
        for spot in spots.values()
        if abs(spot.y - marker.y) < NAME_REACH
    )
    return replace(marker, x=min((marker.x, *level)))


def counted(*, marker: Marker, extra: int) -> Marker:
    """
    Turn the last badge of a full stack into a count.

    Parameters
    ----------
    marker : Marker
        The stack's last badge.
    extra : int
        Badges folded into it.

    Returns
    -------
    Marker
        The badge as it is with none folded in, else a hollow '+<n>'
        badge counting itself and the folded ones.
    """
    if not extra:
        return marker
    return replace(
        marker,
        route=f'+{extra + 1}',
        bearing=None,
        live=False,
        age=None,
    )


def slot_below(*, placed: Sequence[Marker], marker: Marker
               ) -> tuple[int | None, float]:
    """
    Find where a badge goes under the badges already placed.

    Parameters
    ----------
    placed : Sequence[Marker]
        Badges placed so far.
    marker : Marker
        The next badge, no higher than any placed.

    Returns
    -------
    tuple[int | None, float]
        The lowest placed badge within `BADGE_SPAN` across, or None, and
        the badge's y: at least `BADGE_STEP` below that one.
    """
    near = [
        index
        for index, other in enumerate(placed)
        if abs(other.x - marker.x) < BADGE_SPAN
    ]
    above = max(near, key=lambda index: placed[index].y, default=None)
    if above is None:
        return None, marker.y
    return above, max(marker.y, placed[above].y + BADGE_STEP)


def stacked(*, markers: Sequence[Marker]) -> list[Marker]:
    """
    Push overlapping badges apart, folding a crowd into a count.

    Parameters
    ----------
    markers : Sequence[Marker]
        Badges in y order.

    Returns
    -------
    list[Marker]
        Each badge moved down to at least `BADGE_STEP` below the lowest
        badge overlapping it across. One that would move more than
        `STACK_REACH` is folded into that badge, which becomes a '+<n>'
        count.
    """
    placed: list[Marker] = []
    folded: list[int] = []
    for marker in markers:
        above, y = slot_below(placed=placed, marker=marker)
        if above is not None and y - marker.y > STACK_REACH:
            folded[above] += 1
            continue
        placed.append(replace(marker, y=y))
        folded.append(0)
    return [
        counted(marker=marker, extra=extra)
        for marker, extra in zip(placed, folded, strict=True)
    ]


def pushed(*, markers: Sequence[Marker], spots: Mapping[str, Spot]
           ) -> tuple[Marker, ...]:
    """
    Keep badges off station names and apart from each other.

    Parameters
    ----------
    markers : Sequence[Marker]
        Badges.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.

    Returns
    -------
    tuple[Marker, ...]
        Badges by x then y: moved onto the track of any shallower row
        level with them, stacked with at most one step of push, and moved
        left again if the push brought them level with a shallower row.
    """
    moved = sorted(
        (in_gutter(marker=marker, spots=spots) for marker in markers),
        key=lambda each: each.y,
    )
    placed = (
        in_gutter(marker=marker, spots=spots)
        for marker in stacked(markers=moved)
    )
    return tuple(sorted(placed, key=lambda each: (each.x, each.y)))


def vehicle_markers(*, trips: TripInputs, spots: Mapping[str, Spot]
                    ) -> tuple[Marker, ...]:
    """
    Place the strip's vehicles on its visible rows.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    spots : Mapping[str, Spot]
        Visible rows' track spots by station.

    Returns
    -------
    tuple[Marker, ...]
        One badge per strip trip in the window, not cancelled, that can
        be placed, kept off the names and stacked at most two high.
    """
    key = trips.key
    if key is None or not spots:
        return ()
    heard = heard_trips(trips=trips)
    departures = window_departures(
        departures=[
            item.departure for item in trips.departures if not item.cancelled
        ],
        key=key,
        now=trips.now,
    )
    placed = (
        trip_marker(
            departure=departure, trips=trips, spots=spots, heard=heard,
        )
        for departure in departures
    )
    return pushed(
        markers=[each for each in placed if each is not None], spots=spots,
    )
