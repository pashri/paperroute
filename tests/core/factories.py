"""Departures, trip patterns and vehicle positions for core tests."""

from datetime import date, datetime, timedelta

from paperroute.core.geo import Point
from paperroute.core.model import SYDNEY, Departure, Mode
from paperroute.core.patterns import PatternStop, TripPattern
from paperroute.core.positions import PositionsSnapshot, VehiclePosition

START = datetime(2026, 10, 16, 8, 0, tzinfo=SYDNEY)
EIGHT: int = 8 * 3600  # seconds into the service day
TEN_MINUTES: int = 600  # seconds


def departure(
    *,
    departs: datetime,
    ride: int = 30,
    route: str = '606',
    mode: Mode = Mode.BUS,
    trip_id: str | None = None,
    headsign: str = 'Parramatta Station',
    platform: str = '',
) -> Departure:
    """
    A departure from stop sequence 5 to 20.

    Parameters
    ----------
    departs : datetime
        Scheduled departure.
    ride : int
        Minutes to the destination.
    route : str
        Route short name.
    mode : Mode
        Mode.
    trip_id : str | None
        Trip ID; defaults to one derived from the departure time.
    headsign : str
        Headsign.
    platform : str
        Platform or stand.

    Returns
    -------
    Departure
        The departure.
    """
    return Departure(
        trip_id=trip_id or f'trip-{departs:%H%M}',
        aliases=(),
        route=route,
        mode=mode,
        headsign=headsign,
        platform=platform,
        origin_seq=5,
        origin_stop='o',
        destination_seq=20,
        destination_stop='d',
        departs=departs,
        arrives=departs + timedelta(minutes=ride),
        service_day=departs.date(),
    )


def pattern(
    *,
    trip_id: str = 't1',
    route: str = '606',
    shape_id: str | None = None,
    stops: tuple[PatternStop, ...] | None = None,
    days: tuple[date, ...] | None = None,
    along: tuple[float, ...] | None = None,
) -> TripPattern:
    """
    A bus pattern that runs 1 km east from the origin, 8:00 to 8:10.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    route : str
        Route short name.
    shape_id : str | None
        Shape ID.
    stops : tuple[PatternStop, ...] | None
        Calls; defaults to two stops at (0, 0) and (1000, 0).
    days : tuple[date, ...] | None
        Running dates; defaults to the day of `START`.
    along : tuple[float, ...] | None
        Metres along the shape at each stop.

    Returns
    -------
    TripPattern
        The pattern.
    """
    return TripPattern(
        trip_id=trip_id,
        route=route,
        mode=Mode.BUS,
        shape_id=shape_id,
        stops=(
            stops
            if stops is not None
            else (
                PatternStop(arrives=EIGHT, departs=EIGHT, point=(0.0, 0.0)),
                PatternStop(
                    arrives=EIGHT + TEN_MINUTES,
                    departs=EIGHT + TEN_MINUTES,
                    point=(1000.0, 0.0),
                ),
            )
        ),
        days=days if days is not None else (START.date(),),
        along=along,
    )


def vehicle(
    *,
    timestamp: int,
    trip_id: str = 't1',
    label: str = '606',
    point: Point = (0.0, 0.0),
    bearing: float | None = None,
) -> VehiclePosition:
    """
    A vehicle position.

    Parameters
    ----------
    timestamp : int
        When the vehicle reported, in Unix seconds.
    trip_id : str
        Trip ID.
    label : str
        Display label.
    point : Point
        Position in metres.
    bearing : float | None
        Heading in degrees.

    Returns
    -------
    VehiclePosition
        The position.
    """
    return VehiclePosition(
        trip_id=trip_id,
        route_id=f'r_{label}',
        label=label,
        point=point,
        bearing=bearing,
        timestamp=timestamp,
        start_date=None,
    )


def snapshot(
    *,
    fetched_at: datetime,
    vehicles: tuple[VehiclePosition, ...],
) -> PositionsSnapshot:
    """
    A positions download whose header time is its fetch time.

    Parameters
    ----------
    fetched_at : datetime
        When the feed was downloaded.
    vehicles : tuple[VehiclePosition, ...]
        Vehicles in the feed.

    Returns
    -------
    PositionsSnapshot
        The snapshot.
    """
    return PositionsSnapshot(
        fetched_at=fetched_at,
        header_time=int(fetched_at.timestamp()),
        vehicles=vehicles,
    )
