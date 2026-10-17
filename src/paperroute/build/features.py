"""A scratch SQLite store of projected map features for the tile build."""

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Final
from urllib.parse import quote

import numpy as np
from numpy.typing import NDArray

from paperroute.build.geometry import Line, Ring, clip_rings, sea_rings
from paperroute.build.osm_source import OsmArea, OsmLine, OsmPlace, read_osm
from paperroute.build.tile_store import (
    INSERT_LABEL,
    LABELS_TABLE,
    PlacedLabel,
)
from paperroute.core.geo import MetreBox, Point, to_metres
from paperroute.core.tiles import LabelKind

FEATURES_SCHEMA: Final[str] = LABELS_TABLE + """
CREATE TABLE lines (id INTEGER PRIMARY KEY, layer TEXT NOT NULL,
  name TEXT NOT NULL, points BLOB NOT NULL);
CREATE VIRTUAL TABLE line_box USING rtree(id, west, east, south, north);
CREATE TABLE areas (id INTEGER PRIMARY KEY, layer TEXT NOT NULL,
  rings BLOB NOT NULL);
CREATE VIRTUAL TABLE area_box USING rtree(id, west, east, south, north);
CREATE TABLE places (id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
  name TEXT NOT NULL, rank INTEGER NOT NULL, x REAL NOT NULL,
  y REAL NOT NULL);
CREATE INDEX places_x ON places (x);
CREATE INDEX labels_zoom_x ON labels (zoom, x);
"""
SELECT_LINES: Final[str] = """
SELECT lines.layer, lines.name, lines.points FROM lines
JOIN line_box ON line_box.id = lines.id
WHERE line_box.west <= ? AND line_box.east >= ?
  AND line_box.south <= ? AND line_box.north >= ?
ORDER BY lines.id
"""
SELECT_AREAS: Final[str] = """
SELECT areas.layer, areas.rings FROM areas
JOIN area_box ON area_box.id = areas.id
WHERE area_box.west <= ? AND area_box.east >= ?
  AND area_box.south <= ? AND area_box.north >= ?
ORDER BY areas.id
"""
SELECT_PLACES: Final[str] = """
SELECT kind, name, rank, x, y FROM places
WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ? ORDER BY id
"""
SELECT_LABELS: Final[str] = """
SELECT zoom, kind, name, rank, x, y, angle, width, ax, ay, bx, by
FROM labels WHERE zoom = ? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?
ORDER BY id
"""
SEA_CELL: Final[float] = 8192.0  # metres; the sea is stored in squares
LONLAT_ORIGIN: Final[NDArray[np.float64]] = np.array(
    to_metres(lon=0.0, lat=0.0),
)
LONLAT_SCALE: Final[NDArray[np.float64]] = (
    np.array(to_metres(lon=1.0, lat=1.0)) - LONLAT_ORIGIN
)
COUNT: Final[np.dtype[np.int32]] = np.dtype('<i4')
COORD: Final[np.dtype[np.float64]] = np.dtype('<f8')


def project(*, lonlat: Line) -> Line:
    """
    Project longitude and latitude vertices to metres.

    Parameters
    ----------
    lonlat : Line
        Vertices in degrees.

    Returns
    -------
    Line
        The same vertices as ``to_metres`` places them.
    """
    return lonlat * LONLAT_SCALE + LONLAT_ORIGIN


def pack_rings(*, rings: Sequence[Ring]) -> bytes:
    """
    Pack vertex arrays into a blob.

    Parameters
    ----------
    rings : Sequence[Ring]
        Rings or lines, each n by 2.

    Returns
    -------
    bytes
        Little-endian int32 count and lengths, then float64 x, y pairs.
    """
    header = np.array([len(rings), *(len(ring) for ring in rings)], COUNT)
    body = np.concatenate([np.empty((0, 2)), *rings]).astype(COORD)
    return header.tobytes() + body.tobytes()


def unpack_rings(*, blob: bytes) -> list[Ring]:
    """
    Rebuild the arrays from a `pack_rings` blob.

    Parameters
    ----------
    blob : bytes
        The packed blob.

    Returns
    -------
    list[Ring]
        The rings, in native float64.
    """
    count = int(np.frombuffer(blob, dtype=COUNT, count=1)[0])
    lengths = np.frombuffer(blob, dtype=COUNT, count=count, offset=4)
    body = np.frombuffer(blob, dtype=COORD, offset=4 * (count + 1))
    coords = body.astype(np.float64).reshape(-1, 2)
    return list(np.split(coords, np.cumsum(lengths)[:-1]))


def envelope(*, rings: Sequence[Ring]) -> tuple[float, float, float, float]:
    """
    Bound a set of arrays.

    Parameters
    ----------
    rings : Sequence[Ring]
        Rings or lines in metres.

    Returns
    -------
    tuple[float, float, float, float]
        West, east, south and north, in R*Tree column order.
    """
    points = np.concatenate(rings)
    (west, south), (east, north) = points.min(axis=0), points.max(axis=0)
    return (float(west), float(east), float(south), float(north))


def overlap_args(*, box: MetreBox) -> tuple[float, float, float, float]:
    """
    Order a metre box for the R*Tree overlap queries.

    Parameters
    ----------
    box : MetreBox
        West, south, east and north.

    Returns
    -------
    tuple[float, float, float, float]
        East, west, north and south.
    """
    west, south, east, north = box
    return (east, west, north, south)


def chop(*, rings: Sequence[Ring], frame: MetreBox) -> Iterator[list[Ring]]:
    """
    Cut rings into ``SEA_CELL`` squares across a frame.

    Parameters
    ----------
    rings : Sequence[Ring]
        Rings to fill even-odd.
    frame : MetreBox
        West, south, east and north.

    Yields
    ------
    list[Ring]
        The non-empty rings of each square, row by row.
    """
    west, south, east, north = frame
    for bottom in np.arange(south, north, SEA_CELL):
        top = min(float(bottom) + SEA_CELL, north)
        row = clip_rings(rings=rings, box=(west, float(bottom), east, top))
        for left in np.arange(west, east, SEA_CELL):
            right = min(float(left) + SEA_CELL, east)
            cell = (float(left), float(bottom), right, top)
            if piece := clip_rings(rings=row, box=cell):
                yield piece


def to_placed(*, row: tuple[object, ...]) -> PlacedLabel:
    """
    Build a label from a labels row.

    Parameters
    ----------
    row : tuple[object, ...]
        Columns zoom, kind, name, rank, x, y, angle, width, ax, ay, bx
        and by.

    Returns
    -------
    PlacedLabel
        The label.
    """
    zoom, kind, name, rank, *rest = row
    x, y, angle, width, ax, ay, bx, by = (float(str(v)) for v in rest)
    return PlacedLabel(
        name=str(name),
        x=x,
        y=y,
        angle=angle,
        width=width,
        start=(ax, ay),
        end=(bx, by),
        zoom=int(str(zoom)),
        kind=LabelKind(str(kind)),
        rank=int(str(rank)),
    )


class FeatureStore:
    """
    Map features in metres, indexed for box queries.

    Parameters
    ----------
    path : Path
        The database file. A writable store creates its tables, so the
        file must not hold a store already.
    readonly : bool
        Open an existing store for reading only.

    Attributes
    ----------
    conn : sqlite3.Connection
        The open connection. Other build steps may add scratch tables.
    coast : list[Line]
        Coastline ways in metres, held until `finish`.
    """

    def __init__(self, *, path: Path, readonly: bool = False) -> None:
        if readonly:
            self.conn = sqlite3.connect(
                f'file:{quote(str(path))}?mode=ro',
                uri=True,
            )
        else:
            self.conn = sqlite3.connect(path)
            self.conn.executescript(FEATURES_SCHEMA)
        self.coast: list[Line] = []

    def add(self, *, feature: OsmLine | OsmArea | OsmPlace) -> None:
        """
        Project and store one feature.

        Parameters
        ----------
        feature : OsmLine | OsmArea | OsmPlace
            The feature in longitude and latitude. Coastline is kept
            apart until `finish`.
        """
        match feature:
            case OsmLine(layer='coastline'):
                self.coast.append(project(lonlat=feature.lonlat))
            case OsmLine():
                self.add_line(
                    layer=feature.layer,
                    name=feature.name,
                    points=project(lonlat=feature.lonlat),
                )
            case OsmArea():
                self.add_area(
                    layer=feature.layer,
                    rings=[project(lonlat=ring) for ring in feature.rings],
                )
            case OsmPlace():
                self.conn.execute(
                    'INSERT INTO places (kind, name, rank, x, y)'
                    ' VALUES (?, ?, ?, ?, ?)',
                    (
                        feature.kind,
                        feature.name,
                        feature.rank,
                        *to_metres(lon=feature.lon, lat=feature.lat),
                    ),
                )

    def add_line(self, *, layer: str, name: str, points: Line) -> None:
        """
        Store a line in metres.

        Parameters
        ----------
        layer : str
            Layer name.
        name : str
            The line's name, or an empty string.
        points : Line
            Vertices in metres.
        """
        cursor = self.conn.execute(
            'INSERT INTO lines (layer, name, points) VALUES (?, ?, ?)',
            (layer, name, pack_rings(rings=[points])),
        )
        self.conn.execute(
            'INSERT INTO line_box VALUES (?, ?, ?, ?, ?)',
            (cursor.lastrowid, *envelope(rings=[points])),
        )

    def add_area(self, *, layer: str, rings: Sequence[Ring]) -> None:
        """
        Store an area in metres.

        Parameters
        ----------
        layer : str
            Layer name.
        rings : Sequence[Ring]
            Rings in metres, to fill even-odd.
        """
        cursor = self.conn.execute(
            'INSERT INTO areas (layer, rings) VALUES (?, ?)',
            (layer, pack_rings(rings=rings)),
        )
        self.conn.execute(
            'INSERT INTO area_box VALUES (?, ?, ?, ?, ?)',
            (cursor.lastrowid, *envelope(rings=rings)),
        )

    def finish(self, *, frame: MetreBox) -> None:
        """
        Add the sea inside a frame and commit.

        Parameters
        ----------
        frame : MetreBox
            West, south, east and north of the area to fill with sea.
        """
        rings = sea_rings(coast=self.coast, frame=frame)
        for piece in chop(rings=rings, frame=frame):
            self.add_area(layer='sea', rings=piece)
        self.coast = []
        self.conn.commit()

    def lines_in(self, *, box: MetreBox) -> list[tuple[str, str, Line]]:
        """
        Find the lines whose bounds meet a box.

        Parameters
        ----------
        box : MetreBox
            West, south, east and north.

        Returns
        -------
        list[tuple[str, str, Line]]
            Layer, name and vertices in metres.
        """
        rows = self.conn.execute(SELECT_LINES, overlap_args(box=box))
        return [
            (layer, name, unpack_rings(blob=blob)[0])
            for layer, name, blob in rows
        ]

    def areas_in(self, *, box: MetreBox) -> list[tuple[str, list[Ring]]]:
        """
        Find the areas whose bounds meet a box.

        Parameters
        ----------
        box : MetreBox
            West, south, east and north.

        Returns
        -------
        list[tuple[str, list[Ring]]]
            Layer and rings in metres, to fill even-odd.
        """
        rows = self.conn.execute(SELECT_AREAS, overlap_args(box=box))
        return [(layer, unpack_rings(blob=blob)) for layer, blob in rows]

    def places_in(
        self,
        *,
        box: MetreBox,
    ) -> list[tuple[str, str, int, Point]]:
        """
        Find the places inside a box.

        Parameters
        ----------
        box : MetreBox
            West, south, east and north.

        Returns
        -------
        list[tuple[str, str, int, Point]]
            Kind, name, rank and position in metres.
        """
        west, south, east, north = box
        rows = self.conn.execute(SELECT_PLACES, (west, east, south, north))
        return [(kind, name, rank, (x, y)) for kind, name, rank, x, y in rows]

    def add_labels(self, *, labels: Iterable[PlacedLabel]) -> None:
        """
        Store placed labels and commit.

        Parameters
        ----------
        labels : Iterable[PlacedLabel]
            Labels to add.
        """
        self.conn.executemany(
            INSERT_LABEL,
            (
                (
                    item.zoom,
                    str(item.kind),
                    item.name,
                    item.rank,
                    item.x,
                    item.y,
                    item.angle,
                    item.width,
                    *item.start,
                    *item.end,
                )
                for item in labels
            ),
        )
        self.conn.commit()

    def labels_in(
        self,
        *,
        zoom: int,
        box: tuple[float, float, float, float],
    ) -> list[PlacedLabel]:
        """
        Find the labels at a zoom whose centre lies in a pixel box.

        Parameters
        ----------
        zoom : int
            Zoom in metres per pixel.
        box : tuple[float, float, float, float]
            Left, top, right and bottom in the zoom's global pixels.

        Returns
        -------
        list[PlacedLabel]
            The labels, in the order they were added.
        """
        left, top, right, bottom = box
        rows = self.conn.execute(
            SELECT_LABELS,
            (zoom, left, right, top, bottom),
        )
        return [to_placed(row=row) for row in rows]


def load_features(
    *,
    osm: Path,
    bbox: tuple[float, float, float, float],
    store: FeatureStore,
    frame: MetreBox,
    index: str,
) -> int:
    """
    Read an OSM file's features into a store, then add the sea.

    Parameters
    ----------
    osm : Path
        The ``.osm.pbf`` file.
    bbox : tuple[float, float, float, float]
        West, south, east and north in degrees; features outside it are
        skipped.
    store : FeatureStore
        A writable store.
    frame : MetreBox
        West, south, east and north in metres to fill with sea.
    index : str
        Osmium node location store, such as ``flex_mem``.

    Returns
    -------
    int
        The number of features read, before the sea is added.
    """
    count = 0
    for feature in read_osm(path=osm, bbox=bbox, index=index):
        store.add(feature=feature)
        count += 1
    store.finish(frame=frame)
    return count
