"""A trip's stops and running dates, for placing vehicles on the map."""

from dataclasses import dataclass
from datetime import date

from paperroute.core.geo import Point
from paperroute.core.model import Mode


@dataclass(frozen=True)
class PatternStop:
    """One call of a trip.

    Attributes
    ----------
    arrives : int
        Arrival in seconds from the start of the service day.
    departs : int
        Departure in seconds from the start of the service day.
    point : Point | None
        Position in metres, or None when the stop has no coordinates.
    """

    arrives: int
    departs: int
    point: Point | None


@dataclass(frozen=True)
class TripPattern:
    """A trip's route, shape, calls and running dates.

    Attributes
    ----------
    trip_id : str
        The requested trip ID, which may be an alias.
    route : str
        Route short name.
    mode : Mode
        Kind of service.
    shape_id : str | None
        Namespaced shape ID, or None when the trip has no shape.
    stops : tuple[PatternStop, ...]
        Calls in running order.
    days : tuple[date, ...]
        Service dates the trip runs on, near the date asked about.
    along : tuple[float, ...] | None
        Metres along the shape at each stop, once worked out.
    """

    trip_id: str
    route: str
    mode: Mode
    shape_id: str | None
    stops: tuple[PatternStop, ...]
    days: tuple[date, ...]
    along: tuple[float, ...] | None = None
