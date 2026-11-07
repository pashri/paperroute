"""Local metre projection and route shape geometry."""

import math
import sys
import zlib
from array import array
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

Point = tuple[float, float]
MetreBox = tuple[float, float, float, float]

ORIGIN_LON: Final[float] = 151.2093
ORIGIN_LAT: Final[float] = -33.8688
METRES_PER_DEGREE_LAT: Final[float] = 110_574.0
METRES_PER_DEGREE_LON: Final[float] = 111_320.0 * math.cos(
    math.radians(ORIGIN_LAT),
)
DECIMETRES: Final[int] = 10


@dataclass(frozen=True)
class Shape:
    """A route path in metres with cumulative distance at each point.

    Attributes
    ----------
    shape_id : str
        The GTFS shape id.
    points : tuple[Point, ...]
        Vertices as metres east and north of the projection origin.
    along : tuple[float, ...]
        Cumulative metres at each vertex; the first is zero.
    """

    shape_id: str
    points: tuple[Point, ...]
    along: tuple[float, ...]


def to_metres(*, lon: float, lat: float) -> Point:
    """Project a longitude and latitude to metres from the origin.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.

    Returns
    -------
    Point
        Metres east and north of the origin.
    """
    return (
        (lon - ORIGIN_LON) * METRES_PER_DEGREE_LON,
        (lat - ORIGIN_LAT) * METRES_PER_DEGREE_LAT,
    )


def to_lonlat(*, point: Point) -> tuple[float, float]:
    """Unproject metres from the origin to a longitude and latitude.

    Parameters
    ----------
    point : Point
        Metres east and north of the origin.

    Returns
    -------
    tuple[float, float]
        Longitude and latitude in degrees.
    """
    return (
        ORIGIN_LON + point[0] / METRES_PER_DEGREE_LON,
        ORIGIN_LAT + point[1] / METRES_PER_DEGREE_LAT,
    )


def make_shape(*, shape_id: str, points: Sequence[Point]) -> Shape:
    """Build a shape, computing the cumulative distances.

    Parameters
    ----------
    shape_id : str
        The GTFS shape id.
    points : Sequence[Point]
        Vertices in metres.

    Returns
    -------
    Shape
        The shape with `along` filled in.
    """
    along = [0.0]
    for start, end in zip(points, points[1:], strict=False):
        along.append(along[-1] + math.dist(start, end))
    return Shape(
        shape_id=shape_id,
        points=tuple(points),
        along=tuple(along[: len(points)]),
    )


def point_along(*, shape: Shape, distance: float) -> Point:
    """Find the point a distance along a shape.

    Parameters
    ----------
    shape : Shape
        The path to walk.
    distance : float
        Metres from the start; values outside the shape clamp to its ends.

    Returns
    -------
    Point
        The interpolated point in metres.
    """
    if distance <= 0.0 or len(shape.points) < 2:
        return shape.points[0]
    if distance >= shape.along[-1]:
        return shape.points[-1]
    index = bisect_right(shape.along, distance) - 1
    length = shape.along[index + 1] - shape.along[index]
    fraction = (distance - shape.along[index]) / length
    (x0, y0), (x1, y1) = shape.points[index : index + 2]
    return (x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction)


def project_onto_segment(
    *,
    point: Point,
    start: Point,
    end: Point,
) -> tuple[float, float]:
    """Project a point onto a segment.

    Parameters
    ----------
    point : Point
        The point to project.
    start : Point
        The segment start.
    end : Point
        The segment end.

    Returns
    -------
    tuple[float, float]
        The distance from the point to the segment, and the metres from
        the segment start to the nearest position on it.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0.0:
        return math.dist(point, start), 0.0
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length**2
    t = min(1.0, max(0.0, t))
    nearest = (start[0] + dx * t, start[1] + dy * t)
    return math.dist(point, nearest), t * length


def nearest_from(
    *,
    shape: Shape,
    stop: Point,
    first: int,
) -> tuple[int, float]:
    """Find the nearest position on a shape, from a segment onwards.

    Parameters
    ----------
    shape : Shape
        The path to search.
    stop : Point
        The point to place.
    first : int
        The first segment index to consider.

    Returns
    -------
    tuple[int, float]
        The chosen segment index and the metres along the shape. Ties go
        to the earliest segment.
    """
    best = (math.inf, first, shape.along[first])
    for index in range(first, len(shape.points) - 1):
        gap, offset = project_onto_segment(
            point=stop,
            start=shape.points[index],
            end=shape.points[index + 1],
        )
        if gap < best[0]:
            best = (gap, index, shape.along[index] + offset)
    return best[1], best[2]


def stop_distances(
    *,
    shape: Shape,
    stops: Sequence[Point],
) -> tuple[float, ...]:
    """Place stops along a shape, in order.

    Each stop is matched only to segments from the previous stop's segment
    onwards, so a loop that passes the same corner twice keeps its order.

    Parameters
    ----------
    shape : Shape
        The path the stops lie on.
    stops : Sequence[Point]
        Stop positions in metres, in running order.

    Returns
    -------
    tuple[float, ...]
        Metres along the shape for each stop; never decreasing.
    """
    result: list[float] = []
    segment = 0
    for stop in stops:
        if len(shape.points) < 2:
            result.append(0.0)
            continue
        segment, distance = nearest_from(
            shape=shape,
            stop=stop,
            first=min(segment, len(shape.points) - 2),
        )
        result.append(max(distance, result[-1] if result else 0.0))
    return tuple(result)


def pack_shape(*, shape: Shape) -> bytes:
    """Compress a shape to a blob.

    Parameters
    ----------
    shape : Shape
        The shape to pack.

    Returns
    -------
    bytes
        Zlib-compressed little-endian int32 triplets of x, y and distance
        along, each in rounded decimetres.
    """
    values = array('i')
    for (x, y), along in zip(shape.points, shape.along, strict=True):
        values.extend(
            (
                round(x * DECIMETRES),
                round(y * DECIMETRES),
                round(along * DECIMETRES),
            ),
        )
    if sys.byteorder != 'little':
        values.byteswap()
    return zlib.compress(values.tobytes())


def unpack_shape(*, shape_id: str, blob: bytes) -> Shape:
    """Rebuild a shape from `pack_shape` output.

    Parameters
    ----------
    shape_id : str
        The GTFS shape id.
    blob : bytes
        The packed shape.

    Returns
    -------
    Shape
        The shape, accurate to a decimetre.
    """
    values = array('i')
    values.frombytes(zlib.decompress(blob))
    if sys.byteorder != 'little':
        values.byteswap()
    scaled = [value / DECIMETRES for value in values]
    return Shape(
        shape_id=shape_id,
        points=tuple(zip(scaled[0::3], scaled[1::3], strict=True)),
        along=tuple(scaled[2::3]),
    )
