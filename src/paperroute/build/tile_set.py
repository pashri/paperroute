"""Choose the map tiles to build: those within half a screen of a stop."""

import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Final
from urllib.parse import quote

from paperroute.core.geo import MetreBox, Point, to_lonlat, to_metres
from paperroute.core.tiles import (
    OPAL_BBOX,
    TILE,
    from_pixels,
    tiles_covering,
    to_pixels,
)

HALF_SCREEN: Final[tuple[int, int]] = (400, 240)
BLOCK: Final[int] = 8
SELECT_STOPS: Final[str] = """
SELECT lon, lat FROM stops
WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?
"""


def stop_points(
    *,
    timetable: Path,
    bbox: tuple[float, float, float, float],
) -> list[Point]:
    """
    Read the stops inside a box, in whole metres.

    Parameters
    ----------
    timetable : Path
        Timetable database, opened read-only.
    bbox : tuple[float, float, float, float]
        West, south, east and north in degrees.

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
    points = (to_metres(lon=lon, lat=lat) for lon, lat in rows)
    return sorted({(float(round(x)), float(round(y))) for x, y in points})


def tile_set(*, stops: Sequence[Point], mpp: int) -> set[tuple[int, int]]:
    """
    Find the tiles within half a screen of any stop.

    Parameters
    ----------
    stops : Sequence[Point]
        Stops in metres.
    mpp : int
        Metres per pixel.

    Returns
    -------
    set[tuple[int, int]]
        Column and row of every tile overlapping a stop's pixel position
        grown by ``HALF_SCREEN``.
    """
    half_x, half_y = HALF_SCREEN
    tiles: set[tuple[int, int]] = set()
    for stop in stops:
        x, y = to_pixels(point=stop, mpp=mpp)
        tiles.update(
            tiles_covering(
                left=x - half_x,
                top=y - half_y,
                right=x + half_x,
                bottom=y + half_y,
            ),
        )
    return tiles


def coverage(*, tiles: Mapping[int, set[tuple[int, int]]]) -> MetreBox:
    """
    Bound every tile at every zoom.

    Parameters
    ----------
    tiles : Mapping[int, set[tuple[int, int]]]
        Tiles to build, keyed by metres per pixel. At least one tile.

    Returns
    -------
    MetreBox
        West, south, east and north in metres.
    """
    corners = [
        from_pixels(pixel=(col * TILE + dx, row * TILE + dy), mpp=mpp)
        for mpp, wanted in tiles.items()
        for col, row in wanted
        for dx, dy in ((0, 0), (TILE, TILE))
    ]
    xs = [x for x, _ in corners]
    ys = [y for _, y in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def coverage_lonlat(*, box: MetreBox) -> tuple[float, float, float, float]:
    """
    Turn a metre box into degrees, cut to the Opal area.

    Parameters
    ----------
    box : MetreBox
        West, south, east and north in metres.

    Returns
    -------
    tuple[float, float, float, float]
        West, south, east and north in degrees, inside ``OPAL_BBOX``.
    """
    west, south = to_lonlat(point=(box[0], box[1]))
    east, north = to_lonlat(point=(box[2], box[3]))
    return (
        max(west, OPAL_BBOX[0]),
        max(south, OPAL_BBOX[1]),
        min(east, OPAL_BBOX[2]),
        min(north, OPAL_BBOX[3]),
    )


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
