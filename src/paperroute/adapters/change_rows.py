"""Timetable queries and row helpers for a connection's change choices."""

import math
from collections.abc import Iterable
from operator import itemgetter
from typing import Final

from paperroute.core.connection_model import walk_metres
from paperroute.core.geo import Point, to_lonlat
from paperroute.core.model import StopPlace
from paperroute.core.pin_choices import ChangeStop

CHANGE_M: Final[int] = 500
CHANGE_COUNT: Final[int] = 12
REACHED: Final[str] = """
SELECT stop_id, name, lon, lat FROM stops
WHERE stop_id != :station AND stop_id IN (
  SELECT j.value
  FROM run_stops s JOIN runs r ON r.run_id = s.run_id,
    json_each(r.stations) j
  WHERE s.station_id = :station AND j.key > s.pos)
ORDER BY name, stop_id
"""
BETWEEN: Final[str] = """
SELECT r.route
FROM run_stops s JOIN run_stops d ON d.run_id = s.run_id
  JOIN runs r ON r.run_id = s.run_id
  LEFT JOIN chooser_routes c ON c.kind = r.kind AND c.number = r.route
WHERE s.station_id = :origin AND d.station_id = :destination
  AND d.pos > s.pos
GROUP BY r.route
ORDER BY MIN(COALESCE(c.sort, :last)), r.route
"""


def change_box(*, point: Point) -> dict[str, object]:
    """
    Give the query parameters for stops around a point.

    Parameters
    ----------
    point : Point
        Position in metres.

    Returns
    -------
    dict[str, object]
        `CLOSE` parameters for a box `CHANGE_M` metres each way, with the
        place itself kept and any kind of route.
    """
    x, y = point
    west, south = to_lonlat(point=(x - CHANGE_M, y - CHANGE_M))
    east, north = to_lonlat(point=(x + CHANGE_M, y + CHANGE_M))
    return {
        'west': west, 'east': east, 'south': south, 'north': north,
        'own': '', 'any_kind': True, 'kind': None,
    }


def nearest_changes(
    *, place: StopPlace, point: Point, found: Iterable[StopPlace],
) -> tuple[ChangeStop, ...]:
    """
    Keep the places within a walk of a place, nearest first.

    Parameters
    ----------
    place : StopPlace
        The place walked from.
    point : Point
        The place's position in metres.
    found : Iterable[StopPlace]
        Candidate places; those without a point are left out.

    Returns
    -------
    tuple[ChangeStop, ...]
        At most `CHANGE_COUNT` places within `CHANGE_M` metres, nearest
        first and then by stop ID.
    """
    close = sorted(
        (
            (math.dist(point, each.point), each.stop_id, each)
            for each in found
            if each.point is not None
        ),
        key=itemgetter(0, 1),
    )
    return tuple(
        ChangeStop(place=each, metres=walk_metres(alight=place, board=each))
        for dist, _, each in close
        if dist <= CHANGE_M
    )[:CHANGE_COUNT]
