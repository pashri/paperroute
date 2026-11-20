"""Write the stations each train hop passes, from the rail track."""

import json
import math
import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby, pairwise
from typing import Final

from paperroute_transit.build.track_db import RailTrack, TrackStation
from paperroute_transit.build.track_paths import (
    SLACK,
    WINDING,
    Cell,
    TrackGraph,
    along,
    cell_of,
    graph_of,
    rail_path,
    snapped,
)
from paperroute_transit.core.geo import (
    Point,
    Projection,
    make_shape,
    pack_shape,
    project_onto_segment,
    to_metres,
)

PASSED_SCHEMA: Final[str] = """
CREATE TABLE rail_hops (a TEXT NOT NULL, b TEXT NOT NULL,
  passed TEXT NOT NULL, inside INTEGER NOT NULL, path BLOB,
  PRIMARY KEY (a, b));
CREATE TABLE track_places (station_id TEXT PRIMARY KEY,
  name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL);
"""
WAYPOINT_TRAINS: Final[int] = 6  # trains a weekday
RUNS: Final[str] = """
SELECT r.stations, COALESCE(w.trains, 0) FROM runs r
LEFT JOIN weekday_runs w USING (run_id)
WHERE r.kind IN (SELECT value FROM json_each(?))
"""
CALLS: Final[str] = """
SELECT DISTINCT c.stops FROM run_calls c JOIN runs r USING (run_id)
WHERE r.kind IN (SELECT value FROM json_each(?))
"""
PATTERNS: Final[str] = """
SELECT DISTINCT p.stations FROM route_patterns p
JOIN routes r USING (route_id)
WHERE EXISTS (
  SELECT 1 FROM runs ru WHERE ru.route = r.short_name
    AND ru.kind IN (SELECT value FROM json_each(?))
)
"""
PLACES: Final[str] = """
SELECT stop_id, lon, lat FROM stops
WHERE stop_id IN (SELECT value FROM json_each(?))
  AND lon IS NOT NULL AND lat IS NOT NULL
"""
OSM_APART: Final[float] = 250.0  # m

type Hop = tuple[str, str]
type Run = tuple[list[str], int]


@dataclass(frozen=True)
class PassedReport:
    """
    What the passed phase wrote.

    Attributes
    ----------
    hops : int
        Consecutive call pairs found.
    inside : int
        Hops whose stations are all reached along the track.
    added : int
        OSM stations written to ``track_places``.
    """

    hops: int
    inside: int
    added: int


@dataclass(frozen=True)
class Route:
    """
    A hop's way along the track.

    Attributes
    ----------
    passed : tuple[str, ...]
        Station ids passed, in order.
    nodes : tuple[int, ...]
        Track nodes from the first station to the last.
    """

    passed: tuple[str, ...]
    nodes: tuple[int, ...]


@dataclass(frozen=True)
class Network:
    """
    The track and the places hops are worked out against.

    Attributes
    ----------
    graph : TrackGraph
        The track graph.
    stations : Mapping[str, Point]
        Timetable stations of the kinds, in metres.
    points : Mapping[str, Point]
        ``stations`` and the OSM stations apart from them.
    never : frozenset[str]
        Stations never passed.
    """

    graph: TrackGraph
    stations: Mapping[str, Point]
    points: Mapping[str, Point]
    never: frozenset[str]


def run_stations(
    *, conn: sqlite3.Connection, kinds: Collection[str],
) -> list[Run]:
    """
    Read the stations and regular weekday trains of every run.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the runs and weekday_runs tables.
    kinds : Collection[str]
        Run kinds to read.

    Returns
    -------
    list[tuple[list[str], int]]
        Each run's station ids in order, and its weekday trains; a run
        with no ``weekday_runs`` row has none.
    """
    rows = conn.execute(RUNS, (json.dumps(sorted(kinds)),))
    return [(json.loads(stations), trains) for stations, trains in rows]


def never_passed(
    *, conn: sqlite3.Connection, kinds: Collection[str],
) -> frozenset[str]:
    """
    Find the stations that no trip of the kinds calls at mid-trip.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the route patterns.
    kinds : Collection[str]
        Run kinds to read.

    Returns
    -------
    frozenset[str]
        Stations that every such trip starts or ends at.
    """
    rows = conn.execute(PATTERNS, (json.dumps(sorted(kinds)),))
    patterns = [json.loads(stations) for stations, in rows]
    every = {station for stations in patterns for station in stations}
    inner = {station for stations in patterns for station in stations[1:-1]}
    return frozenset(every - inner)


def stop_calls(
    *, conn: sqlite3.Connection, kinds: Collection[str],
) -> list[list[str]]:
    """
    Read the stopping calls of every run of the kinds.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the run tables.
    kinds : Collection[str]
        Run kinds to read.

    Returns
    -------
    list[list[str]]
        Each distinct list of stations a run stops at, in order.
    """
    rows = conn.execute(CALLS, (json.dumps(sorted(kinds)),))
    return [json.loads(stops) for stops, in rows]


def waypoints_of(
    *, runs: Iterable[Run], calls: Iterable[Sequence[str]] = (),
) -> dict[Hop, set[str]]:
    """
    Find each hop and the stations that steer it.

    A run counts for a hop only from a call at ``a`` to its next call at
    ``b`` with no other call at ``a`` or ``b`` between. A station
    between steers the hop only when the runs that call at it there
    carry ``WAYPOINT_TRAINS`` weekday trains in total.

    Hops are the consecutive pairs of the runs' stations and of their
    stopping calls.

    Parameters
    ----------
    runs : Iterable[Run]
        Each run's station ids and weekday trains.
    calls : Iterable[Sequence[str]]
        Each run's stopping stations.

    Returns
    -------
    dict[tuple[str, str], set[str]]
        Stations that steer, by hop.
    """
    runs = list(runs)
    found: dict[Hop, dict[str, set[int]]] = {
        (a, b): {}
        for stations in (*(stations for stations, _ in runs), *calls)
        for a, b in pairwise(stations)
        if a != b
    }
    for number, (stations, _) in enumerate(runs):
        for i, a in enumerate(stations):
            record_between(
                found=found, stations=stations, start=i, run=number,
            )
    trains = [count for _, count in runs]
    return {
        hop: {
            station
            for station, numbers in between.items()
            if sum(trains[number] for number in numbers) >= WAYPOINT_TRAINS
        }
        for hop, between in found.items()
    }


def record_between(
    *,
    found: dict[Hop, dict[str, set[int]]],
    stations: Sequence[str],
    start: int,
    run: int,
) -> None:
    """
    Add the calls between one call and each station that follows it.

    Parameters
    ----------
    found : dict[tuple[str, str], dict[str, set[int]]]
        Run numbers that call at each station between, by hop; updated.
    stations : Sequence[str]
        One run's station ids.
    start : int
        Position of the call at ``a``.
    run : int
        The run's number.
    """
    a, seen = stations[start], set()
    for j in range(start + 1, len(stations)):
        b = stations[j]
        if b == a:
            return
        if b not in seen and (a, b) in found:
            for station in stations[start + 1:j]:
                found[(a, b)].setdefault(station, set()).add(run)
        seen.add(b)


def stations_in(
    *,
    conn: sqlite3.Connection,
    ids: Collection[str],
    projection: Projection,
) -> dict[str, Point]:
    """
    Place stations in metres.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the stops table.
    ids : Collection[str]
        Station ids.
    projection : Projection
        The projection to place them in.

    Returns
    -------
    dict[str, Point]
        Metres by id; a station with no position is absent.
    """
    rows = conn.execute(PLACES, (json.dumps(sorted(ids)),))
    return {
        stop_id: to_metres(lon=lon, lat=lat, projection=projection)
        for stop_id, lon, lat in rows
    }


def osm_points(
    *, track: RailTrack, stations: Mapping[str, Point],
) -> dict[str, Point]:
    """
    Place the OSM stations more than ``OSM_APART`` from every station.

    Parameters
    ----------
    track : RailTrack
        The track and its stations.
    stations : Mapping[str, Point]
        Timetable stations in metres.

    Returns
    -------
    dict[str, Point]
        Metres by id ``osm:<osm_id>``.
    """
    buckets: dict[Cell, list[Point]] = {}
    for point in stations.values():
        buckets.setdefault(cell_of(point=point), []).append(point)
    return {
        f'osm:{station.osm_id}': (station.x, station.y)
        for station in track.stations
        if not near_any(point=(station.x, station.y), buckets=buckets)
    }


def near_any(*, point: Point, buckets: Mapping[Cell, list[Point]]) -> bool:
    """
    Tell whether any bucketed point is within ``OSM_APART`` of a point.

    Parameters
    ----------
    point : Point
        Metres east and north.
    buckets : Mapping[tuple[int, int], list[Point]]
        Points by grid cell.

    Returns
    -------
    bool
        True when one is no further than ``OSM_APART``.
    """
    col, row = cell_of(point=point)
    return any(
        math.dist(point, other) <= OSM_APART
        for dc in (-1, 0, 1)
        for dr in (-1, 0, 1)
        for other in buckets.get((col + dc, row + dr), ())
    )


def via_of(
    *, network: Network, hop: Hop, between: Collection[str],
) -> list[str]:
    """
    Pick the waypoints a hop runs through, in order from a to b.

    Parameters
    ----------
    network : Network
        The places.
    hop : tuple[str, str]
        The hop's ends.
    between : Collection[str]
        Stations called between the ends.

    Returns
    -------
    list[str]
        Those within the winding limit, by metres along a to b.
    """
    start, end = (network.stations[station] for station in hop)
    limit = WINDING * math.dist(start, end) + SLACK
    kept = {
        station: network.stations[station]
        for station in between
        if station in network.stations
        and math.dist(start, network.stations[station])
        + math.dist(network.stations[station], end) <= limit
    }
    return sorted(
        kept,
        key=lambda station: (
            project_onto_segment(
                point=kept[station], start=start, end=end,
            )[1],
            station,
        ),
    )


def leg_nodes(
    *, network: Network, ends: Hop,
) -> tuple[int, ...] | None:
    """
    Find the track between two stations.

    Parameters
    ----------
    network : Network
        The places.
    ends : tuple[str, str]
        The two stations.

    Returns
    -------
    tuple[int, ...] | None
        Track nodes, or None when a station is off the track or no path
        is short enough.
    """
    p, q = (network.stations[station] for station in ends)
    starts = snapped(graph=network.graph, point=p)
    finishes = snapped(graph=network.graph, point=q)
    if not starts or not finishes:
        return None
    return rail_path(
        graph=network.graph,
        starts=starts,
        ends=finishes,
        straight=math.dist(p, q),
    )


def route_of(
    *, network: Network, hop: Hop, via: Sequence[str],
) -> Route | None:
    """
    Follow a hop along the track through its waypoints.

    Parameters
    ----------
    network : Network
        The places.
    hop : tuple[str, str]
        The hop's ends.
    via : Sequence[str]
        Waypoints in order.

    Returns
    -------
    Route | None
        The stations passed and the nodes, or None when any leg is
        outside the track.
    """
    stops = (hop[0], *via, hop[1])
    passed: list[str] = []
    nodes: list[int] = []
    for ends in pairwise(stops):
        leg = leg_nodes(network=network, ends=ends)
        if leg is None:
            return None
        skip = {*hop, *ends, *network.never}
        passed.extend(
            key
            for key in along(
                graph=network.graph, path=leg, points=network.points,
            )
            if key not in skip
        )
        passed.extend(
            station
            for station in ends[1:]
            if station != hop[1] and station not in network.never
        )
        nodes.extend(leg)
    return Route(passed=tuple(dict.fromkeys(passed)), nodes=tuple(nodes))


def path_blob(*, network: Network, hop: Hop, route: Route) -> bytes:
    """
    Pack a route's track as a shape.

    Parameters
    ----------
    network : Network
        The places.
    hop : tuple[str, str]
        The hop's ends.
    route : Route
        The route.

    Returns
    -------
    bytes
        ``pack_shape`` output for the shape ``a>b``.
    """
    nodes = [node for node, _ in groupby(route.nodes)]
    points = [(network.graph.xs[n], network.graph.ys[n]) for n in nodes]
    shape = make_shape(shape_id=f'{hop[0]}>{hop[1]}', points=points)
    return pack_shape(shape=shape)


def routes_of(
    *, network: Network | None, found: Mapping[Hop, set[str]],
) -> dict[Hop, Route | None]:
    """
    Work out every hop's route.

    Parameters
    ----------
    network : Network | None
        The places; None when there is no track.
    found : Mapping[tuple[str, str], set[str]]
        Stations called between, by hop.

    Returns
    -------
    dict[tuple[str, str], Route | None]
        The route by hop, None where the hop is outside the track.
    """
    if network is None:
        return {hop: None for hop in found}
    return {
        hop: (
            route_of(
                network=network,
                hop=hop,
                via=via_of(network=network, hop=hop, between=between),
            )
            if all(station in network.stations for station in hop)
            else None
        )
        for hop, between in sorted(found.items())
    }


def write_passed(
    *,
    conn: sqlite3.Connection,
    track: RailTrack | None,
    kinds: Collection[str],
    projection: Projection,
) -> PassedReport:
    """
    Create and fill ``rail_hops`` and ``track_places``.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the run tables. The caller commits.
    track : RailTrack | None
        The rail track; None to mark every hop outside.
    kinds : Collection[str]
        Run kinds whose hops are worked out.
    projection : Projection
        The timetable's projection.

    Returns
    -------
    PassedReport
        How many hops, how many inside the track, and how many OSM
        stations were added.
    """
    conn.executescript(PASSED_SCHEMA)
    runs = run_stations(conn=conn, kinds=kinds)
    found = waypoints_of(
        runs=runs, calls=stop_calls(conn=conn, kinds=kinds),
    )
    network = network_of(
        conn=conn,
        track=track,
        runs=[stations for stations, _ in runs],
        kinds=kinds,
        projection=projection,
    )
    routes = routes_of(network=network, found=found)
    write_hops(conn=conn, network=network, routes=routes)
    added = write_places(conn=conn, track=track, routes=routes)
    inside = sum(route is not None for route in routes.values())
    return PassedReport(hops=len(routes), inside=inside, added=added)


def network_of(
    *,
    conn: sqlite3.Connection,
    track: RailTrack | None,
    runs: Sequence[Sequence[str]],
    kinds: Collection[str],
    projection: Projection,
) -> Network | None:
    """
    Build the places hops are worked out against.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the stops table.
    track : RailTrack | None
        The rail track.
    runs : Sequence[Sequence[str]]
        Each run's station ids.
    kinds : Collection[str]
        Run kinds whose hops are worked out.
    projection : Projection
        The timetable's projection.

    Returns
    -------
    Network | None
        None when there is no track.
    """
    if track is None:
        return None
    ids = {station for stations in runs for station in stations}
    stations = stations_in(conn=conn, ids=ids, projection=projection)
    return Network(
        graph=graph_of(track=track),
        stations=stations,
        points={**stations, **osm_points(track=track, stations=stations)},
        never=never_passed(conn=conn, kinds=kinds),
    )


def write_hops(
    *,
    conn: sqlite3.Connection,
    network: Network | None,
    routes: Mapping[Hop, Route | None],
) -> None:
    """
    Write one ``rail_hops`` row per hop.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the ``rail_hops`` table.
    network : Network | None
        The places the routes were worked out against.
    routes : Mapping[tuple[str, str], Route | None]
        The route by hop.
    """
    conn.executemany(
        'INSERT INTO rail_hops (a, b, passed, inside, path)'
        ' VALUES (?, ?, ?, ?, ?)',
        (
            (
                a,
                b,
                json.dumps(list(route.passed) if route else []),
                int(route is not None),
                (
                    None
                    if route is None or network is None
                    else path_blob(network=network, hop=(a, b), route=route)
                ),
            )
            for (a, b), route in routes.items()
        ),
    )


def write_places(
    *,
    conn: sqlite3.Connection,
    track: RailTrack | None,
    routes: Mapping[Hop, Route | None],
) -> int:
    """
    Write a ``track_places`` row for each OSM station a hop passes.

    Parameters
    ----------
    conn : sqlite3.Connection
        A timetable with the ``track_places`` table.
    track : RailTrack | None
        The rail track and its stations.
    routes : Mapping[tuple[str, str], Route | None]
        The route by hop.

    Returns
    -------
    int
        The number of rows written.
    """
    named: dict[str, TrackStation] = (
        {}
        if track is None
        else {f'osm:{s.osm_id}': s for s in track.stations}
    )
    used = sorted({
        station
        for route in routes.values()
        if route is not None
        for station in route.passed
        if station in named
    })
    conn.executemany(
        'INSERT INTO track_places (station_id, name, lat, lon)'
        ' VALUES (?, ?, ?, ?)',
        (
            (key, named[key].name, named[key].lat, named[key].lon)
            for key in used
        ),
    )
    return len(used)
