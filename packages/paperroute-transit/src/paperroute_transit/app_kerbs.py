"""The reads that match places and read a line's stations and kerbs."""

import logging
import sqlite3
from collections.abc import Collection, Sequence

from paperroute_transit.core.messages import (
    LineLoaded,
    LoadLine,
    PlacesMatched,
)
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.parent_sides import line_pairs
from paperroute_transit.core.sides import ParentKerbs
from paperroute_transit.ports import NetworkRepository, TimetableRepository

logger = logging.getLogger(__name__)


def parents_read(
    *, network: NetworkRepository, stations: Collection[str]
) -> dict[str, ParentKerbs]:
    """
    Read the kerbs of the parent stations among some stations.

    Parameters
    ----------
    network : NetworkRepository
        Chooser catalogue.
    stations : Collection[str]
        Station IDs.

    Returns
    -------
    dict[str, ParentKerbs]
        The kerbs by station ID; empty, after a warning, when the read
        fails.
    """
    try:
        return network.parent_kerbs(stations=stations)
    except (sqlite3.Error, ValueError, TypeError):
        logger.warning('reading the parent kerbs failed', exc_info=True)
        return {}


def matches(
    *,
    timetable: TimetableRepository,
    network: NetworkRepository,
    places: Sequence[StopPlace],
) -> PlacesMatched:
    """
    Look places up in the current timetable.

    Parameters
    ----------
    timetable : TimetableRepository
        Timetable.
    network : NetworkRepository
        Chooser catalogue.
    places : Sequence[StopPlace]
        Places from an earlier timetable.

    Returns
    -------
    PlacesMatched
        Each place's match, or None, by its old stop ID, and the kerbs
        of the matches that are parent stations with them.
    """
    found = {
        place.stop_id: timetable.match_stop(place=place) for place in places
    }
    stations = [match.stop_id for match in found.values() if match is not None]
    return PlacesMatched(
        found=found, parents=parents_read(network=network, stations=stations)
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
    ids = [] if line is None else [place.stop_id for place in line]
    pairs = (
        ()
        if line is None
        else line_pairs(
            line=line,
            pairs=network.kerb_pairs(stations=ids),
            parents=network.parent_kerbs(stations=ids),
        )
    )
    return LineLoaded(
        towards=effect.towards,
        line=line,
        timetable=effect.timetable,
        pairs=pairs,
    )
