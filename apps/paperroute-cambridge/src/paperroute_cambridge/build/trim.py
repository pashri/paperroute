"""Trim a loaded timetable to the routes and coach calls near Cambridge."""

import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from paperroute_transit.core.geo import DegreeBox, Projection, to_metres

MIN_CALLS = 2


class CoachTest(Protocol):
    """Tell whether a route is a coach service."""

    def __call__(self, *, route_type: int, agency_id: str | None) -> bool:
        """
        Tell whether a route is a coach service.

        Parameters
        ----------
        route_type : int
            The route's GTFS type.
        agency_id : str | None
            The route's agency.

        Returns
        -------
        bool
            True for a coach route.
        """


@dataclass(frozen=True)
class TrimReport:
    """
    What a trim kept and removed.

    Attributes
    ----------
    routes : int
        Routes kept.
    dropped : int
        Trips removed.
    cut : int
        Coach trips shortened and kept.
    """

    routes: int
    dropped: int
    cut: int


def kept_calls(*, inside: Sequence[bool]) -> tuple[int, ...]:
    """
    Pick the calls a coach trip keeps.

    Parameters
    ----------
    inside : Sequence[bool]
        Whether each call, in order, is inside the box.

    Returns
    -------
    tuple[int, ...]
        Indexes of the calls inside the box and of the call after the last
        of them. Empty when no call is inside.
    """
    indexes = [index for index, flag in enumerate(inside) if flag]
    if not indexes:
        return ()
    after = indexes[-1] + 1
    return (*indexes, after) if after < len(inside) else tuple(indexes)


def _near_stops(
    *,
    conn: sqlite3.Connection,
    projection: Projection,
    radius: float,
) -> list[tuple[str]]:
    """
    Give the ids of stops with coordinates within the radius.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    projection : Projection
        The projection whose origin is the centre.
    radius : float
        Distance in metres.

    Returns
    -------
    list[tuple[str]]
        One row per stop id.
    """
    rows = conn.execute(
        'SELECT stop_id, lon, lat FROM stops '
        'WHERE lon IS NOT NULL AND lat IS NOT NULL'
    )
    return [
        (stop_id,)
        for stop_id, lon, lat in rows
        if math.hypot(
            *to_metres(lon=lon, lat=lat, projection=projection),
        )
        <= radius
    ]


def _drop_far_routes(
    *,
    conn: sqlite3.Connection,
    projection: Projection,
    radius: float,
    kept_sources: frozenset[str],
) -> int:
    """
    Delete the trips of routes with no stop near.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    projection : Projection
        The projection whose origin is the centre.
    radius : float
        Distance in metres.
    kept_sources : frozenset[str]
        Sources whose trips are left alone.

    Returns
    -------
    int
        Trips deleted.

    Raises
    ------
    ValueError
        If no route has a stop within the radius.
    """
    conn.execute('CREATE TEMP TABLE near_stops (stop_id TEXT PRIMARY KEY)')
    conn.executemany(
        'INSERT INTO near_stops VALUES (?)',
        _near_stops(conn=conn, projection=projection, radius=radius),
    )
    conn.execute(
        'CREATE TEMP TABLE near_routes AS SELECT DISTINCT t.route_id '
        'FROM stop_times st JOIN trips t USING (trip_id) '
        'WHERE st.stop_id IN (SELECT stop_id FROM near_stops)'
    )
    if not conn.execute('SELECT 1 FROM near_routes').fetchone():
        conn.execute('DROP TABLE near_stops')
        conn.execute('DROP TABLE near_routes')
        message = f'no route within {radius:.0f} m of the centre'
        raise ValueError(message)
    far = (
        'SELECT trip_id FROM trips WHERE route_id NOT IN near_routes '
        'AND source NOT IN (SELECT value FROM json_each(?))'
    )
    kept = (json.dumps(sorted(kept_sources)),)
    conn.execute(f'DELETE FROM stop_times WHERE trip_id IN ({far})', kept)
    count = conn.execute(f'DELETE FROM trips WHERE trip_id IN ({far})', kept)
    conn.execute('DROP TABLE near_stops')
    conn.execute('DROP TABLE near_routes')
    return count.rowcount


def _in_box(*, lon: float | None, lat: float | None, box: DegreeBox) -> bool:
    """
    Tell whether a stop with these coordinates is inside the box.

    Parameters
    ----------
    lon : float | None
        Longitude, or None when the stop has none.
    lat : float | None
        Latitude, or None when the stop has none.
    box : DegreeBox
        The box.

    Returns
    -------
    bool
        True when both coordinates exist and lie in the box.
    """
    if lon is None or lat is None:
        return False
    west, south, east, north = box
    return west <= lon <= east and south <= lat <= north


def _cut_trip(
    *,
    conn: sqlite3.Connection,
    trip_id: str,
    box: DegreeBox,
) -> bool | None:
    """
    Cut one coach trip to its kept calls.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    trip_id : str
        The trip.
    box : DegreeBox
        The box the trip is cut to.

    Returns
    -------
    bool | None
        None when the trip was deleted, else whether it lost a call.
    """
    calls = conn.execute(
        'SELECT st.seq, s.lon, s.lat FROM stop_times st '
        'JOIN stops s USING (stop_id) WHERE st.trip_id = ? ORDER BY st.seq',
        (trip_id,),
    ).fetchall()
    keep = kept_calls(
        inside=[_in_box(lon=lon, lat=lat, box=box) for _, lon, lat in calls],
    )
    if len(keep) < MIN_CALLS:
        conn.execute('DELETE FROM stop_times WHERE trip_id = ?', (trip_id,))
        conn.execute('DELETE FROM trips WHERE trip_id = ?', (trip_id,))
        return None
    gone = [(trip_id, calls[i][0]) for i in range(len(calls)) if i not in keep]
    conn.executemany(
        'DELETE FROM stop_times WHERE trip_id = ? AND seq = ?',
        gone,
    )
    return bool(gone)


def _cut_coaches(
    *,
    conn: sqlite3.Connection,
    box: DegreeBox,
    coach: CoachTest,
) -> tuple[int, int]:
    """
    Cut every coach trip.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.
    box : DegreeBox
        The box a trip is cut to.
    coach : CoachTest
        Tells which routes are coach services.

    Returns
    -------
    tuple[int, int]
        Trips deleted and trips shortened.
    """
    trips = conn.execute(
        'SELECT t.trip_id, r.type, COALESCE(t.agency_id, r.agency_id) '
        'FROM trips t JOIN routes r USING (route_id)'
    ).fetchall()
    outcomes = [
        _cut_trip(conn=conn, trip_id=trip_id, box=box)
        for trip_id, route_type, agency_id in trips
        if coach(route_type=route_type, agency_id=agency_id)
    ]
    return outcomes.count(None), outcomes.count(True)


def _drop_unused(*, conn: sqlite3.Connection) -> int:
    """
    Delete routes with no trip and stops nothing calls at.

    Parameters
    ----------
    conn : sqlite3.Connection
        The timetable.

    Returns
    -------
    int
        Routes left.
    """
    conn.execute(
        'DELETE FROM routes WHERE route_id NOT IN '
        '(SELECT route_id FROM trips)'
    )
    conn.execute(
        'DELETE FROM stops WHERE stop_id NOT IN '
        '(SELECT stop_id FROM stop_times) AND stop_id NOT IN '
        '(SELECT s.parent_station FROM stops s '
        'JOIN stop_times st ON st.stop_id = s.stop_id '
        'WHERE s.parent_station IS NOT NULL)'
    )
    return int(conn.execute('SELECT COUNT(*) FROM routes').fetchone()[0])


def trim_timetable(
    *,
    conn: sqlite3.Connection,
    projection: Projection,
    radius: float,
    box: DegreeBox,
    coach: CoachTest,
    kept_sources: frozenset[str] = frozenset(),
) -> TrimReport:
    """
    Keep the routes near the centre and cut coach trips at the box.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with every source loaded and no indexes yet.
    projection : Projection
        The projection whose origin is the centre.
    radius : float
        Distance from the centre in metres within which a stop keeps its
        routes.
    box : DegreeBox
        The box a coach trip is cut to.
    coach : CoachTest
        Tells which routes are coach services.
    kept_sources : frozenset[str]
        Sources whose routes are kept whatever their distance.

    Returns
    -------
    TrimReport
        What was kept, removed and cut.

    Raises
    ------
    ValueError
        If no route has a stop within the radius. Nothing is deleted.
    """
    dropped = _drop_far_routes(
        conn=conn,
        projection=projection,
        radius=radius,
        kept_sources=kept_sources,
    )
    deleted, cut = _cut_coaches(conn=conn, box=box, coach=coach)
    routes = _drop_unused(conn=conn)
    conn.commit()
    return TrimReport(routes=routes, dropped=dropped + deleted, cut=cut)
