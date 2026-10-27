"""Connection pins: their legs and the walk between them."""

import math
from datetime import date, timedelta
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.model import Pin, StopPlace, Way

LEG_EXTRA: Final[timedelta] = timedelta(hours=2)
WALK_STEP: Final[int] = 10  # metres the walk is rounded up to
WALK_PACE: Final[int] = 80  # metres walked a minute
SPARE_MINUTES: Final[int] = 2

type TripKey = tuple[str, date]


def way_legs(*, pin: Pin, way: Way) -> tuple[Pin, ...]:
    """
    Split a way into the leg pins that are searched.

    Parameters
    ----------
    pin : Pin
        Connection pin.
    way : Way
        One of its ways.

    Returns
    -------
    tuple[Pin, ...]
        One leg pin more than the way has changes, each of any kind and
        route: From to the first alight, each board to the next alight,
        and the last board to the Destination.
    """
    starts = [pin.origin, *(change.board for change in way.changes)]
    ends = [*(change.alight for change in way.changes), pin.destination]
    return tuple(
        Pin(origin=start, destination=end, kind=None)
        for start, end in zip(starts, ends, strict=True)
    )


def direct_leg(*, pin: Pin) -> Pin:
    """
    Make the pin that searches for a trip without a change.

    Parameters
    ----------
    pin : Pin
        Connection pin.

    Returns
    -------
    Pin
        From to the Destination, of any kind and route.
    """
    return Pin(origin=pin.origin, destination=pin.destination, kind=None)


def walk_metres(*, alight: StopPlace, board: StopPlace) -> int:
    """
    Measure the walk between two stops in a straight line.

    Parameters
    ----------
    alight : StopPlace
        Stop left.
    board : StopPlace
        Stop joined.

    Returns
    -------
    int
        Metres rounded up to the next 10; 0 when either has no point.
    """
    if alight.point is None or board.point is None:
        return 0
    metres = math.dist(alight.point, board.point)
    return math.ceil(metres / WALK_STEP) * WALK_STEP


def default_allow(*, metres: int) -> int:
    """
    Suggest the minutes to allow for a change.

    Parameters
    ----------
    metres : int
        Walk in metres.

    Returns
    -------
    int
        Minutes to walk at 80 m a minute, rounded up, plus 2.
    """
    return math.ceil(metres / WALK_PACE) + SPARE_MINUTES


def trip_key(*, item: LiveDeparture) -> TripKey:
    """
    Name a departure's trip on its service day.

    Parameters
    ----------
    item : LiveDeparture
        Departure.

    Returns
    -------
    TripKey
        Trip ID and service day.
    """
    return item.departure.trip_id, item.departure.service_day
