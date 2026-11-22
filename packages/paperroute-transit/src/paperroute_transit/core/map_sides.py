"""The kerb across the road on a both-sides pin's Map."""

from dataclasses import replace

from paperroute_transit.core.map_view import TripMarks
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.sides import both_sides


def kerb_across(*, pin: Pin) -> StopPlace | None:
    """
    Find the kerb across the road from a pin's From.

    Parameters
    ----------
    pin : Pin
        The pin.

    Returns
    -------
    StopPlace | None
        The opposite kerb of a both-sides pin, else None.
    """
    return pin.opposite if both_sides(pin=pin) else None


def with_across(
    *, trip: TripMarks | None, across: StopPlace | None,
) -> TripMarks | None:
    """
    Join the kerb across the road to the From's marks.

    Parameters
    ----------
    trip : TripMarks | None
        The From's marks.
    across : StopPlace | None
        The kerb across the road.

    Returns
    -------
    TripMarks | None
        The marks with `across` at the kerb's point; unchanged with no
        marks, no kerb or no point.
    """
    if trip is None or across is None or across.point is None:
        return trip
    return replace(trip, across=across.point)
