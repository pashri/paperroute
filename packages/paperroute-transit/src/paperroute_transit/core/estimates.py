"""Bus times worked out from vehicle positions, as a trip-updates snapshot."""

from collections.abc import Mapping
from datetime import date, datetime, timedelta, tzinfo
from typing import Final

from paperroute_transit.core.geo import Shape
from paperroute_transit.core.lateness import Line, Placed, line_of, placed
from paperroute_transit.core.map_view import position_age
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.realtime import (
    EVERY_STOP,
    RealtimeSnapshot,
    StopUpdate,
    TripRealtime,
)

UNCERTAIN_LATE: Final[timedelta] = timedelta(minutes=10)
NEAR_END: Final[float] = 150.0  # metres from the trip's first or last stop
TURNAROUND: Final[timedelta] = timedelta(minutes=2)
BLANK_OFF: Final[timedelta] = timedelta(hours=3)  # a blank-date day's limit


def run_day(*, vehicle: VehiclePosition, zone: tzinfo) -> date:
    """
    Give the service date a vehicle is running.

    Parameters
    ----------
    vehicle : VehiclePosition
        The reported position.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    date
        Its start date, else the local date of its timestamp.
    """
    if vehicle.start_date is not None:
        return vehicle.start_date
    return datetime.fromtimestamp(vehicle.timestamp, tz=zone).date()


def run_days(*, vehicle: VehiclePosition, zone: tzinfo) -> tuple[date, ...]:
    """
    Give the service dates a vehicle could be running.

    Parameters
    ----------
    vehicle : VehiclePosition
        The reported position.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    tuple[date, ...]
        Its start date alone; with none, the local date of its
        timestamp, then the day before.
    """
    today = run_day(vehicle=vehicle, zone=zone)
    if vehicle.start_date is not None:
        return (today,)
    return (today, today - timedelta(days=1))


def heard_at(
    *, vehicle: VehiclePosition, positions: PositionsSnapshot,
) -> datetime:
    """
    Give when a vehicle reported, on the local clock.

    Parameters
    ----------
    vehicle : VehiclePosition
        The reported position.
    positions : PositionsSnapshot
        The download it came in.

    Returns
    -------
    datetime
        The fetch time less the position's age on the feed's clock.
    """
    age = position_age(
        vehicle=vehicle, snapshot=positions, now=positions.fetched_at,
    )
    return positions.fetched_at - timedelta(seconds=age)


def is_uncertain(*, place: Placed) -> bool:
    """
    Say whether a placed position gives an uncertain estimate.

    Parameters
    ----------
    place : Placed
        The position placed on its trip.

    Returns
    -------
    bool
        True when far behind the timetable or near either end.
    """
    return place.late > UNCERTAIN_LATE.total_seconds() or place.ends < NEAR_END


def fitting_days(
    *,
    vehicle: VehiclePosition,
    pattern: TripPattern,
    line: Line,
    zone: tzinfo,
) -> list[tuple[date, Placed]]:
    """
    Place a vehicle on each day its trip could be running.

    Parameters
    ----------
    vehicle : VehiclePosition
        The reported position.
    pattern : TripPattern
        The trip.
    line : Line
        The trip's line.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    list[tuple[date, Placed]]
        Each day the trip runs on that places the vehicle; with a blank
        start date, only days within the blank-date limit of timetable.
    """
    limit = BLANK_OFF.total_seconds()
    found = []
    for day in run_days(vehicle=vehicle, zone=zone):
        place = placed(
            pattern=pattern, line=line, vehicle=vehicle, day=day, zone=zone,
        )
        if day not in pattern.days or place is None:
            continue
        if vehicle.start_date is None and abs(place.late) > limit:
            continue
        found.append((day, place))
    return found


def own_estimate(
    *,
    vehicle: VehiclePosition,
    pattern: TripPattern,
    shape: Shape | None,
    positions: PositionsSnapshot,
    zone: tzinfo,
) -> TripRealtime | None:
    """
    Estimate a trip from its own vehicle's position.

    Parameters
    ----------
    vehicle : VehiclePosition
        The reported position.
    pattern : TripPattern
        The trip.
    shape : Shape | None
        The trip's shape, or None when it has none.
    positions : PositionsSnapshot
        The download the position came in.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    TripRealtime | None
        The estimate on the day that fits best; None when the position
        has no place on the trip on any day it runs.
    """
    line = line_of(pattern=pattern, shape=shape)
    if line is None:
        return None
    found = fitting_days(
        vehicle=vehicle, pattern=pattern, line=line, zone=zone,
    )
    if not found:
        return None
    day, place = min(found, key=lambda pair: abs(pair[1].late))
    update = StopUpdate(
        sequence=EVERY_STOP,
        stop_id=None,
        arrival_delay=None,
        departure_delay=place.late,
        arrival_time=None,
        departure_time=None,
    )
    return TripRealtime(
        trip_id=vehicle.trip_id,
        cancelled=False,
        updates=(update,),
        start_date=day,
        estimated=True,
        uncertain=is_uncertain(place=place),
        heard_at=heard_at(vehicle=vehicle, positions=positions),
    )


def carried(
    *, trip: TripRealtime, pattern: TripPattern, joined: TripPattern,
) -> TripRealtime | None:
    """
    Carry a trip's lateness into the trip it runs on through as.

    Parameters
    ----------
    trip : TripRealtime
        The estimated trip.
    pattern : TripPattern
        The trip's pattern.
    joined : TripPattern
        The next trip's pattern.

    Returns
    -------
    TripRealtime | None
        The next trip, always uncertain, late by what the layover less
        the turnaround does not absorb; None when it absorbs it all.
    """
    late = trip.updates[0].departure_delay or 0
    layover = joined.stops[0].departs - pattern.stops[-1].arrives
    left = late - (layover - TURNAROUND.total_seconds())
    if late <= 0 or left <= 0:
        return None
    update = StopUpdate(
        sequence=EVERY_STOP,
        stop_id=None,
        arrival_delay=None,
        departure_delay=round(left),
        arrival_time=None,
        departure_time=None,
    )
    return TripRealtime(
        trip_id=joined.trip_id,
        cancelled=False,
        updates=(update,),
        start_date=trip.start_date,
        estimated=True,
        uncertain=True,
        heard_at=trip.heard_at,
    )


def fresher(*, trip: TripRealtime, old: TripRealtime | None) -> bool:
    """
    Say whether an estimate was heard after another.

    Parameters
    ----------
    trip : TripRealtime
        The new estimate.
    old : TripRealtime | None
        The estimate held so far for the trip.

    Returns
    -------
    bool
        True when there is no old one or the new one was heard later.
    """
    if old is None or old.heard_at is None:
        return True
    return trip.heard_at is not None and trip.heard_at > old.heard_at


def estimated_trips(
    *,
    positions: PositionsSnapshot,
    patterns: Mapping[str, TripPattern | None],
    shapes: Mapping[str, Shape],
    limit: timedelta,
    zone: tzinfo,
) -> dict[str, TripRealtime]:
    """
    Estimate every trip with a fresh vehicle on it.

    Parameters
    ----------
    positions : PositionsSnapshot
        The download.
    patterns : Mapping[str, TripPattern | None]
        Each trip's pattern by trip ID, None when unknown.
    shapes : Mapping[str, Shape]
        Shapes by shape ID.
    limit : timedelta
        The oldest a position may be on the feed's clock.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    dict[str, TripRealtime]
        Estimates by trip ID; of several vehicles on a trip, the one
        heard last.
    """
    found: dict[str, TripRealtime] = {}
    for vehicle in positions.vehicles:
        pattern = patterns.get(vehicle.trip_id)
        age = position_age(
            vehicle=vehicle, snapshot=positions, now=positions.fetched_at,
        )
        if pattern is None or age > limit.total_seconds():
            continue
        trip = own_estimate(
            vehicle=vehicle,
            pattern=pattern,
            shape=shapes.get(pattern.shape_id or ''),
            positions=positions,
            zone=zone,
        )
        if trip is None:
            continue
        if fresher(trip=trip, old=found.get(trip.trip_id)):
            found[trip.trip_id] = trip
    return found


def carried_trips(
    *,
    own: Mapping[str, TripRealtime],
    patterns: Mapping[str, TripPattern | None],
) -> dict[str, TripRealtime]:
    """
    Carry each estimate into the trip joined on after it.

    Parameters
    ----------
    own : Mapping[str, TripRealtime]
        The estimates from vehicles' own positions, by trip ID.
    patterns : Mapping[str, TripPattern | None]
        Each trip's pattern by trip ID, None when unknown.

    Returns
    -------
    dict[str, TripRealtime]
        The carried-over trips by trip ID, leaving out any trip with
        an estimate of its own.
    """
    found: dict[str, TripRealtime] = {}
    for trip_id, trip in own.items():
        pattern = patterns.get(trip_id)
        joined = patterns.get(pattern.next_trip or '') if pattern else None
        if pattern is None or joined is None or joined.trip_id in own:
            continue
        carry = carried(trip=trip, pattern=pattern, joined=joined)
        if carry is not None:
            found[joined.trip_id] = carry
    return found


def estimated_snapshot(
    *,
    positions: PositionsSnapshot,
    patterns: Mapping[str, TripPattern | None],
    shapes: Mapping[str, Shape],
    trip_ids: frozenset[str],
    limit: timedelta,
    zone: tzinfo,
) -> RealtimeSnapshot:
    """
    Turn a positions download into a snapshot of estimated trips.

    Parameters
    ----------
    positions : PositionsSnapshot
        The download.
    patterns : Mapping[str, TripPattern | None]
        Each trip's pattern by trip ID, None when unknown.
    shapes : Mapping[str, Shape]
        Shapes by shape ID.
    trip_ids : frozenset[str]
        The trips asked for.
    limit : timedelta
        The oldest a position may be on the feed's clock.
    zone : tzinfo
        Local time zone.

    Returns
    -------
    RealtimeSnapshot
        The estimates and their carry-over into joined trips that have
        no estimate of their own, cut down to the trips asked for.
    """
    own = estimated_trips(
        positions=positions,
        patterns=patterns,
        shapes=shapes,
        limit=limit,
        zone=zone,
    )
    trips = {**carried_trips(own=own, patterns=patterns), **own}
    return RealtimeSnapshot(
        fetched_at=positions.fetched_at,
        trips={k: v for k, v in trips.items() if k in trip_ids},
    )
