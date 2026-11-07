"""The stops a departure is ridden between, for fitting and drawing."""

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Final

from paperroute_transit.core.geo import Point
from paperroute_transit.core.model import Departure, service_epoch
from paperroute_transit.core.patterns import TripPattern

NO_DESTINATION_WINDOW: Final[timedelta] = timedelta(minutes=20)
SECOND_RIDE_WINDOW: Final[timedelta] = timedelta(minutes=10)


def ridden_stops(
    *,
    departure: Departure,
    pattern: TripPattern | None,
    window: timedelta | None,
) -> tuple[Point, ...]:
    """
    List the points of the stops a departure is ridden between.

    Parameters
    ----------
    departure : Departure
        The departure.
    pattern : TripPattern | None
        Its pattern; None gives no stops.
    window : timedelta | None
        When set, only stops reached within this long of leaving.

    Returns
    -------
    tuple[Point, ...]
        Stop points from boarding to alighting, in running order; stops
        with no point are left out.
    """
    if pattern is None:
        return ()
    epoch = service_epoch(day=departure.service_day)
    start = (departure.departs - epoch).total_seconds()
    end = (departure.arrives - epoch).total_seconds()
    if window is not None:
        end = min(end, start + window.total_seconds())
    return tuple(
        stop.point
        for stop in pattern.stops
        if stop.point is not None
        and start <= stop.departs
        and stop.arrives <= end
    )


def stops_window(*, destination: str | None) -> timedelta | None:
    """
    Say how far along a pin's trips its map reaches.

    Parameters
    ----------
    destination : str | None
        The pin's To, or None when it has none.

    Returns
    -------
    timedelta | None
        `NO_DESTINATION_WINDOW` with no To; None (the whole ride) with one.
    """
    return NO_DESTINATION_WINDOW if destination is None else None


def route_stops(
    *,
    departures: Sequence[Departure],
    patterns: Mapping[str, TripPattern | None],
    window: timedelta | None,
) -> tuple[Point, ...]:
    """
    Collect the distinct ridden stops of some departures.

    Parameters
    ----------
    departures : Sequence[Departure]
        The departures.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns by trip ID.
    window : timedelta | None
        As for `ridden_stops`.

    Returns
    -------
    tuple[Point, ...]
        Stop points, each once, in the order first met.
    """
    found = dict.fromkeys(
        point
        for departure in departures
        for point in ridden_stops(
            departure=departure,
            pattern=patterns.get(departure.trip_id),
            window=window,
        )
    )
    return tuple(found)
