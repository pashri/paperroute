"""The global pixel grid and tile addressing for the map."""

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from paperroute_transit.core.geo import MetreBox, Point, Projection

TILE: Final[int] = 256
LABEL_REACH: Final[int] = TILE  # pixels a baked label may reach past
ZOOMS: Final[tuple[int, ...]] = (3, 5, 8, 14, 25, 45)
CITY: Final[int] = 45
TILE_STYLE: Final[str] = 'm9d'  # bump when the tiles' look changes
SUBURB_ZOOMS: Final[frozenset[int]] = frozenset({8, 14, 25, 45})
STATION_ZOOMS: Final[frozenset[int]] = frozenset({3, 5, 8, 14, 25, 45})
STREET_HEIGHT: Final[int] = 14
POINT_HALF_HEIGHT: Final[float] = 0.7  # of the face's pixel size

PixelBox = tuple[float, float, float, float]


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


def to_pixels(
    *,
    point: Point,
    mpp: int,
    projection: Projection,
) -> tuple[float, float]:
    """Convert metres to global pixels at a zoom.

    Parameters
    ----------
    point : Point
        Metres east and north of the projection origin.
    mpp : int
        Metres per pixel.
    projection : Projection
        The projection whose grid origin is pixel zero.

    Returns
    -------
    tuple[float, float]
        Pixels right of and below the grid origin.
    """
    grid = projection.grid_origin
    return (
        (point[0] - grid[0]) / mpp,
        (grid[1] - point[1]) / mpp,
    )


def from_pixels(
    *,
    pixel: tuple[float, float],
    mpp: int,
    projection: Projection,
) -> Point:
    """Convert global pixels at a zoom back to metres.

    Parameters
    ----------
    pixel : tuple[float, float]
        Pixels right of and below the grid origin.
    mpp : int
        Metres per pixel.
    projection : Projection
        The projection whose grid origin is pixel zero.

    Returns
    -------
    Point
        Metres east and north of the projection origin.
    """
    grid = projection.grid_origin
    return (
        grid[0] + pixel[0] * mpp,
        grid[1] - pixel[1] * mpp,
    )


def metres_of(
    *,
    box: PixelBox,
    mpp: int,
    projection: Projection,
) -> MetreBox:
    """
    Convert a box of global pixels at a zoom to metres.

    Parameters
    ----------
    box : PixelBox
        Left, top, right and bottom in global pixels.
    mpp : int
        Metres per pixel.
    projection : Projection
        The projection whose grid origin is pixel zero.

    Returns
    -------
    MetreBox
        West, south, east and north.
    """
    left, top, right, bottom = box
    west, north = from_pixels(
        pixel=(left, top),
        mpp=mpp,
        projection=projection,
    )
    east, south = from_pixels(
        pixel=(right, bottom),
        mpp=mpp,
        projection=projection,
    )
    return (west, south, east, north)


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


def zooms_of(*, text: str | None) -> tuple[int, ...] | None:
    """Read a tiles file's comma-separated zoom levels.

    Parameters
    ----------
    text : str | None
        The stored levels, such as ``3,5,8``, or None when missing.

    Returns
    -------
    tuple[int, ...] | None
        The levels, or None when missing or not whole numbers.
    """
    if text is None:
        return None
    try:
        return tuple(int(part) for part in text.split(','))
    except ValueError:
        return None


def levels_text(*, zooms: tuple[int, ...] | None) -> str:
    """Write zoom levels for a message.

    Parameters
    ----------
    zooms : tuple[int, ...] | None
        The levels, or None when unknown.

    Returns
    -------
    str
        The levels joined with commas, or ``none``.
    """
    if zooms is None:
        return 'none'
    return ','.join(str(mpp) for mpp in zooms)


def text_box(
    *,
    centre: Point,
    width: float,
    height: float,
    angle: float,
) -> PixelBox:
    """
    Bound a turned text box.

    Parameters
    ----------
    centre : Point
        Centre in global pixels.
    width : float
        Text width.
    height : float
        Text height.
    angle : float
        Rotation in degrees.

    Returns
    -------
    PixelBox
        Left, top, right and bottom.
    """
    turn = math.radians(angle)
    cos, sin = abs(math.cos(turn)), abs(math.sin(turn))
    half_x = (cos * width + sin * height) / 2
    half_y = (sin * width + cos * height) / 2
    x, y = centre
    return (x - half_x, y - half_y, x + half_x, y + half_y)
