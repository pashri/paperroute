"""Where the Map's solid lines leave the road they share."""

from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from math import dist, floor
from typing import Final

from paperroute_transit.core.geo import Point, point_along
from paperroute_transit.core.map_line import MapLine

PARTING_STEP: Final[float] = 25.0  # metres between samples
PARTING_REACH: Final[float] = 16_000.0  # metres sampled from a solid start
APART: Final[float] = 60.0  # metres off every other line that is a parting
SHARED: Final[float] = 300.0  # metres shared before a parting counts


def samples(*, line: MapLine) -> tuple[Point, ...]:
    """
    Sample a line's solid stretch.

    Parameters
    ----------
    line : MapLine
        A line with a solid stretch.

    Returns
    -------
    tuple[Point, ...]
        A point every `PARTING_STEP` metres from the stretch's start, up
        to its end or `PARTING_REACH`.
    """
    start, end = line.solid or (0.0, -1.0)
    last = min(end - start, PARTING_REACH)
    count = int(last // PARTING_STEP) + 1
    return tuple(
        point_along(shape=line.shape, distance=start + step * PARTING_STEP)
        for step in range(count)
    )


def cell_of(*, point: Point) -> tuple[int, int]:
    """
    Give the `APART`-sized grid cell a point falls in.

    Parameters
    ----------
    point : Point
        The point.

    Returns
    -------
    tuple[int, int]
        The cell's column and row.
    """
    return floor(point[0] / APART), floor(point[1] / APART)


def is_near(
    *, cells: dict[tuple[int, int], list[Point]], point: Point,
) -> bool:
    """
    Tell whether any bucketed point lies within `APART` of a point.

    Parameters
    ----------
    cells : dict[tuple[int, int], list[Point]]
        Points by grid cell.
    point : Point
        The point.

    Returns
    -------
    bool
        True when a point in the 3 x 3 cells around it is within `APART`.
    """
    column, row = cell_of(point=point)
    return any(
        dist(point, other) <= APART
        for across in (column - 1, column, column + 1)
        for down in (row - 1, row, row + 1)
        for other in cells.get((across, down), ())
    )


def parting_of(
    *, own: tuple[Point, ...], cells: dict[tuple[int, int], list[Point]],
) -> Point | None:
    """
    Find where one line leaves the others.

    Parameters
    ----------
    own : tuple[Point, ...]
        The line's samples.
    cells : dict[tuple[int, int], list[Point]]
        The other lines' samples by grid cell.

    Returns
    -------
    Point | None
        The sample before the first with no other line near it, when at
        least `SHARED` metres came before that sample; else None.
    """
    for index, point in enumerate(own):
        if not is_near(cells=cells, point=point):
            if (index - 1) * PARTING_STEP >= SHARED:
                return own[index - 1]
            return None
    return None


Fingerprint = tuple[str, tuple[float, float] | None, int, float, Point]


@dataclass(frozen=True)
class Solids:
    """
    The lines `partings` reads, compared by their geometry.

    Attributes
    ----------
    key : tuple[Fingerprint, ...]
        Each line's shape id, solid stretch, point count, length and
        first point.
    lines : tuple[MapLine, ...]
        The lines; they do not take part in comparison.
    """

    key: tuple[Fingerprint, ...]
    lines: tuple[MapLine, ...] = field(compare=False)


def partings(*, lines: tuple[MapLine, ...]) -> tuple[Point, ...]:
    """
    Find where each solid line leaves the road it shares.

    Parameters
    ----------
    lines : tuple[MapLine, ...]
        The map's lines; dashed lines take no part.

    Returns
    -------
    tuple[Point, ...]
        At most one point per solid line, its earliest parting.
    """
    key = tuple(
        (
            line.shape_id,
            line.solid,
            len(line.shape.points),
            line.shape.along[-1],
            line.shape.points[0],
        )
        for line in lines
    )
    return found_partings(solids=Solids(key=key, lines=lines))


@lru_cache(maxsize=8)
def found_partings(*, solids: Solids) -> tuple[Point, ...]:
    """
    Find the partings of a set of lines, memoised on their geometry.

    Parameters
    ----------
    solids : Solids
        The lines.

    Returns
    -------
    tuple[Point, ...]
        At most one point per solid line, its earliest parting.
    """
    lines = solids.lines
    solid = [samples(line=line) for line in lines if line.solid is not None]
    found: list[Point] = []
    for index, own in enumerate(solid):
        cells: dict[tuple[int, int], list[Point]] = defaultdict(list)
        for other in solid[:index] + solid[index + 1 :]:
            for point in other:
                cells[cell_of(point=point)].append(point)
        parting = parting_of(own=own, cells=cells)
        if parting is not None:
            found.append(parting)
    return tuple(found)
