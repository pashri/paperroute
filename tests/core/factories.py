"""Departures for core tests."""

from datetime import datetime, timedelta

from paperroute.core.model import SYDNEY, Departure, Mode

START = datetime(2026, 10, 16, 8, 0, tzinfo=SYDNEY)


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
