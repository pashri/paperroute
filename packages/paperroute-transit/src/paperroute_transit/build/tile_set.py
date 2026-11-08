"""Choose the map tiles to build: those within half a screen of a stop."""

import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Final
from urllib.parse import quote

from paperroute_transit.core.geo import (
    DegreeBox,
    MetreBox,
    Point,
    Projection,
    to_lonlat,
    to_metres,
)
from paperroute_transit.core.tiles import (
    TILE,
    from_pixels,
    tiles_covering,
    to_pixels,
)

HALF_SCREEN: Final[tuple[int, int]] = (400, 240)
BLOCK: Final[int] = 8
SEA_MARGIN_DEG: Final[float] = 0.2
SELECT_STOPS: Final[str] = """
SELECT lon, lat FROM stops
WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?
"""


def stop_points(
    *,
    timetable: Path,
    bbox: tuple[float, float, float, float],
    projection: Projection,
) -> list[Point]:
    """
    Read the stops inside a box, in whole metres.

    Parameters
    ----------
    timetable : Path
        Timetable database, opened read-only.
    bbox : tuple[float, float, float, float]
        West, south, east and north in degrees.
    projection : Projection
        The projection to place the stops in.

    Returns
    -------
    list[Point]
        Sorted distinct stop positions, rounded to whole metres. Stops
        without coordinates are left out.
    """
    west, south, east, north = bbox
    uri = f'file:{quote(str(timetable))}?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        rows = conn.execute(
            SELECT_STOPS,
            (west, east, south, north),
        ).fetchall()
    points = (
        to_metres(lon=lon, lat=lat, projection=projection)
        for lon, lat in rows
    )
    return sorted({(float(round(x)), float(round(y))) for x, y in points})


def tile_set(
    *,
    stops: Sequence[Point],
    mpp: int,
    projection: Projection,
) -> set[tuple[int, int]]:
    """
    Find the tiles within half a screen of any stop.

    Parameters
    ----------
    stops : Sequence[Point]
        Stops in metres.
    mpp : int
        Metres per pixel.
    projection : Projection
        The projection whose pixel grid the tiles sit on.

    Returns
    -------
    set[tuple[int, int]]
        Column and row of every tile overlapping a stop's pixel position
        grown by ``HALF_SCREEN``.
    """
    half_x, half_y = HALF_SCREEN
    tiles: set[tuple[int, int]] = set()
    for stop in stops:
        x, y = to_pixels(point=stop, mpp=mpp, projection=projection)
        tiles.update(
            tiles_covering(
                left=x - half_x,
                top=y - half_y,
                right=x + half_x,
                bottom=y + half_y,
            ),
        )
    return tiles


def coverage(
    *,
    tiles: Mapping[int, set[tuple[int, int]]],
    projection: Projection,
) -> MetreBox:
    """
    Bound every tile at every zoom.

    Parameters
    ----------
    tiles : Mapping[int, set[tuple[int, int]]]
        Tiles to build, keyed by metres per pixel. At least one tile.
    projection : Projection
        The projection whose pixel grid the tiles sit on.

    Returns
    -------
    MetreBox
        West, south, east and north in metres.
    """
    corners = [
        from_pixels(
            pixel=(col * TILE + dx, row * TILE + dy),
            mpp=mpp,
            projection=projection,
        )
        for mpp, wanted in tiles.items()
        for col, row in wanted
        for dx, dy in ((0, 0), (TILE, TILE))
    ]
    xs = [x for x, _ in corners]
    ys = [y for _, y in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def coverage_lonlat(
    *,
    box: MetreBox,
    projection: Projection,
    area: DegreeBox,
) -> DegreeBox:
    """
    Turn a metre box into degrees, cut to the city's area and some sea.

    Parameters
    ----------
    box : MetreBox
        West, south, east and north in metres.
    projection : Projection
        The projection the box is in.
    area : DegreeBox
        The city's area, west, south, east and north in degrees.

    Returns
    -------
    DegreeBox
        West, south, east and north in degrees, inside ``area`` grown by
        ``SEA_MARGIN_DEG`` on every side.

    Raises
    ------
    ValueError
        If the box lies wholly outside the area and its margin of sea.
    """
    west, south = to_lonlat(point=(box[0], box[1]), projection=projection)
    east, north = to_lonlat(point=(box[2], box[3]), projection=projection)
    clipped = (
        max(west, area[0] - SEA_MARGIN_DEG),
        max(south, area[1] - SEA_MARGIN_DEG),
        min(east, area[2] + SEA_MARGIN_DEG),
        min(north, area[3] + SEA_MARGIN_DEG),
    )
    if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
        raise ValueError("the tiles lie outside the city's area")
    return clipped


def blocks(*, tiles: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """
    Group tiles into blocks of ``BLOCK`` by ``BLOCK``.

    Parameters
    ----------
    tiles : set[tuple[int, int]]
        Column and row pairs.

    Returns
    -------
    list[tuple[int, int]]
        Sorted distinct block column and row pairs.
    """
    return sorted({(col // BLOCK, row // BLOCK) for col, row in tiles})
