"""The rail track as flat arrays, and the stations along it."""

import sqlite3
from array import array
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import quote

TRACK_SCHEMA: Final[str] = """
CREATE TABLE track_nodes (node INTEGER PRIMARY KEY, x REAL NOT NULL,
  y REAL NOT NULL);
CREATE TABLE track_edges (a INTEGER NOT NULL, b INTEGER NOT NULL);
CREATE TABLE track_stations (osm_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL);
"""


@dataclass(frozen=True)
class TrackStation:
    """
    A named station node.

    Attributes
    ----------
    osm_id : int
        The OSM node id.
    name : str
        The station's name.
    lat : float
        Latitude in degrees.
    lon : float
        Longitude in degrees.
    x : float
        Metres east of the projection's origin.
    y : float
        Metres north of the projection's origin.
    """

    osm_id: int
    name: str
    lat: float
    lon: float
    x: float
    y: float


@dataclass(frozen=True)
class RailTrack:
    """
    Running rail lines as a graph, and the stations near them.

    Attributes
    ----------
    xs : array[float]
        Metres east of the origin, one per node. Node ids are the
        indexes, dense from zero.
    ys : array[float]
        Metres north of the origin, one per node.
    edges : array[int]
        Node id pairs, flattened: edge i joins ``edges[2 * i]`` and
        ``edges[2 * i + 1]``.
    stations : tuple[TrackStation, ...]
        Named stations, whether or not a line reaches them.
    """

    xs: array[float]
    ys: array[float]
    edges: array[int]
    stations: tuple[TrackStation, ...]


def write_track(*, conn: sqlite3.Connection, track: RailTrack) -> None:
    """
    Create the track tables and fill them.

    Parameters
    ----------
    conn : sqlite3.Connection
        An open database without track tables. The caller commits.
    track : RailTrack
        The track to store.
    """
    conn.executescript(TRACK_SCHEMA)
    conn.executemany(
        'INSERT INTO track_nodes (node, x, y) VALUES (?, ?, ?)',
        zip(range(len(track.xs)), track.xs, track.ys, strict=True),
    )
    conn.executemany(
        'INSERT INTO track_edges (a, b) VALUES (?, ?)',
        zip(track.edges[0::2], track.edges[1::2], strict=True),
    )
    conn.executemany(
        'INSERT INTO track_stations (osm_id, name, lat, lon, x, y)'
        ' VALUES (?, ?, ?, ?, ?, ?)',
        (
            (s.osm_id, s.name, s.lat, s.lon, s.x, s.y)
            for s in track.stations
        ),
    )


def read_track_db(*, path: Path) -> RailTrack | None:
    """
    Read the track from a database, opened read-only.

    Parameters
    ----------
    path : Path
        The tiles file.

    Returns
    -------
    RailTrack | None
        The track, or None when the file is missing or has no track
        tables.
    """
    try:
        with closing(
            sqlite3.connect(f'file:{quote(str(path))}?mode=ro', uri=True),
        ) as conn:
            nodes = conn.execute(
                'SELECT x, y FROM track_nodes ORDER BY node',
            ).fetchall()
            edges = conn.execute(
                'SELECT a, b FROM track_edges ORDER BY rowid',
            ).fetchall()
            stations = conn.execute(
                'SELECT osm_id, name, lat, lon, x, y FROM track_stations'
                ' ORDER BY osm_id',
            ).fetchall()
    except sqlite3.Error:
        return None
    return RailTrack(
        xs=array('d', (x for x, _ in nodes)),
        ys=array('d', (y for _, y in nodes)),
        edges=array('q', (node for edge in edges for node in edge)),
        stations=tuple(TrackStation(*row) for row in stations),
    )
