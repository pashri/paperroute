"""The reads that match places and read a line's stations and kerbs."""

from collections.abc import Sequence

from paperroute_transit.core.messages import (
    LineLoaded,
    LoadLine,
    PlacesMatched,
)
from paperroute_transit.core.model import StopPlace
from paperroute_transit.ports import NetworkRepository, TimetableRepository


def matches(
    *, timetable: TimetableRepository, places: Sequence[StopPlace]
) -> PlacesMatched:
    """
    Look places up in the current timetable.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    places : Sequence[StopPlace]
        Places from an earlier timetable.

    Returns
    -------
    PlacesMatched
        Each place's match, or None, by its old stop ID.
    """
    return PlacesMatched(
        found={
            place.stop_id: timetable.match_stop(place=place)
            for place in places
        }
    )


def line_loaded(*, network: NetworkRepository, effect: LoadLine) -> LineLoaded:
    """
    Read a towards row's stations.

    Parameters
    ----------
    network : NetworkRepository
        Chooser catalogue.
    effect : LoadLine
        Request.

    Returns
    -------
    LineLoaded
        The stations.
    """
    line = network.towards_line(towards=effect.towards)
    pairs = (
        ()
        if line is None
        else tuple(
            network.kerb_pairs(
                stations=[place.stop_id for place in line],
            ).values()
        )
    )
    return LineLoaded(
        towards=effect.towards,
        line=line,
        timetable=effect.timetable,
        pairs=pairs,
    )
