"""Where the hero's vehicle is on its run, and the words for it."""

from dataclasses import dataclass
from typing import Final

from paperroute_transit.core.patterns import TripPattern, hop_near
from paperroute_transit.core.positions import VehiclePosition
from paperroute_transit.core.stop_view import stops_text

AT_STOP: Final[str] = 'at the stop now'
WAITING: Final[str] = 'waiting here'
NOT_LEFT: Final[str] = 'not left yet'


@dataclass(frozen=True)
class BusSeen:
    """
    A vehicle's place on the way to the From.

    Attributes
    ----------
    left : int
        Pattern index of the call it last left or waits at.
    away : int
        Calls still to make, up to and including the From.
    waiting : bool
        True when it waits at the first stop of the trip.
    """

    left: int
    away: int
    waiting: bool


def bus_seen(
    *,
    pattern: TripPattern,
    start: int,
    vehicle: VehiclePosition,
    departed: bool,
) -> BusSeen | None:
    """
    Place a vehicle among the calls before the From.

    Parameters
    ----------
    pattern : TripPattern
        The trip.
    start : int
        Pattern index of the From.
    vehicle : VehiclePosition
        The vehicle's reported position.
    departed : bool
        Whether the trip has left its first stop.

    Returns
    -------
    BusSeen | None
        Its place, or None when it has no hop or is past the From.
    """
    hop = hop_near(pattern=pattern, point=vehicle.point)
    if hop is None:
        return None
    waiting = hop[0] == 0 and not departed
    if hop[0] >= start and not (start == 0 and waiting):
        return None
    seen = 0 if waiting else hop[0] + int(hop[1] >= 1.0)
    stops = pattern.stops
    left = next(
        (
            index
            for index in range(seen, -1, -1)
            if not stops[index].passes
        ),
        seen,
    )
    away = sum(not stop.passes for stop in stops[left + 1 : start + 1])
    return BusSeen(left=left, away=away, waiting=waiting)


def bus_words(*, bus: BusSeen) -> str:
    """
    Say how far a vehicle is from the From.

    Parameters
    ----------
    bus : BusSeen
        Where the vehicle is.

    Returns
    -------
    str
        Such as '2 stops away' or 'waiting here'.
    """
    if bus.away == 0:
        return WAITING if bus.waiting else AT_STOP
    words = f'{stops_text(stops=bus.away)} away'
    return f'{words}, {NOT_LEFT}' if bus.waiting else words
