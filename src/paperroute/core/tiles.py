"""The global pixel grid and tile addressing for the map."""

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from paperroute.core.geo import Point, to_metres

TILE: Final[int] = 256
ZOOMS: Final[tuple[int, ...]] = (3, 5, 8, 14, 45)
CITY: Final[int] = 45
OPAL_BBOX: Final[tuple[float, float, float, float]] = (
    149.5,
    -35.0,
    152.1,
    -32.0,
)
GRID_ORIGIN: Final[Point] = to_metres(lon=149.5, lat=-32.0)


class TileVariant(StrEnum):
    """The stored renderings of a tile."""

    GREY = 'grey'
    BITS = 'bits'


class LabelKind(StrEnum):
    """Kinds of map label."""

    STATION = 'station'
    SUBURB = 'suburb'
    STREET = 'street'


@dataclass(frozen=True)
class StreetLabel:
    """A street name placed along a road, in the zoom's global pixels.

    Attributes
    ----------
    name : str
        The street name.
    x : float
        Anchor x.
    y : float
        Anchor y.
    angle : float
        Text rotation.
    width : float
        Text length along the road.
    start : tuple[float, float]
        Start of the road segment.
    end : tuple[float, float]
        End of the road segment.
    """

    name: str
    x: float
    y: float
    angle: float
    width: float
    start: tuple[float, float]
    end: tuple[float, float]


def to_pixels(*, point: Point, mpp: int) -> tuple[float, float]:
    """Convert metres to global pixels at a zoom.

    Parameters
    ----------
    point : Point
        Metres east and north of the projection origin.
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[float, float]
        Pixels right of and below the grid origin.
    """
    return (
        (point[0] - GRID_ORIGIN[0]) / mpp,
        (GRID_ORIGIN[1] - point[1]) / mpp,
    )


def from_pixels(*, pixel: tuple[float, float], mpp: int) -> Point:
    """Convert global pixels at a zoom back to metres.

    Parameters
    ----------
    pixel : tuple[float, float]
        Pixels right of and below the grid origin.
    mpp : int
        Metres per pixel.

    Returns
    -------
    Point
        Metres east and north of the projection origin.
    """
    return (
        GRID_ORIGIN[0] + pixel[0] * mpp,
        GRID_ORIGIN[1] - pixel[1] * mpp,
    )


def tile_of(*, pixel: tuple[float, float]) -> tuple[int, int]:
    """Find the tile holding a pixel.

    Parameters
    ----------
    pixel : tuple[float, float]
        Global pixel.

    Returns
    -------
    tuple[int, int]
        Column and row.
    """
    return math.floor(pixel[0] / TILE), math.floor(pixel[1] / TILE)


def tiles_covering(
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> list[tuple[int, int]]:
    """List the tiles overlapping a half-open pixel box.

    Parameters
    ----------
    left : float
        Left edge, included.
    top : float
        Top edge, included.
    right : float
        Right edge, excluded.
    bottom : float
        Bottom edge, excluded.

    Returns
    -------
    list[tuple[int, int]]
        Column and row pairs in row-major order.
    """
    first_col, first_row = tile_of(pixel=(left, top))
    last_col = max(first_col, math.ceil(right / TILE) - 1)
    last_row = max(first_row, math.ceil(bottom / TILE) - 1)
    return [
        (col, row)
        for row in range(first_row, last_row + 1)
        for col in range(first_col, last_col + 1)
    ]
