"""The route lines the Map draws."""

from collections.abc import Sequence

from paperroute_transit.core.model import Departure


def map_line_key(*, departure: Departure) -> str:
    """
    Give the line a departure draws on the Map.

    Parameters
    ----------
    departure : Departure
        The departure.

    Returns
    -------
    str
        The route on the road; on rail, the feed route ID, else the route.
    """
    if departure.facts.road:
        return departure.route
    return departure.facts.route_id or departure.route


def first_per_line(
    *, departures: Sequence[Departure],
) -> tuple[Departure, ...]:
    """
    Keep the first departure of each line.

    Parameters
    ----------
    departures : Sequence[Departure]
        The departures, in order.

    Returns
    -------
    tuple[Departure, ...]
        The first of each `map_line_key`, in order.
    """
    seen: dict[str, Departure] = {}
    for found in departures:
        seen.setdefault(map_line_key(departure=found), found)
    return tuple(seen.values())
