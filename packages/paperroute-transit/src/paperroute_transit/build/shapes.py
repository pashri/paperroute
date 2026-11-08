"""Stream GTFS shapes, simplify them and store them in the timetable."""

import csv
import io
import logging
import math
import sqlite3
import zipfile
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from itertools import batched, groupby
from operator import itemgetter
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray

from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.geo import (
    DECIMETRES,
    Projection,
    make_shape,
    pack_shape,
    to_metres,
)

SIMPLIFY_M: Final[float] = 1.5
SHAPES_BATCH: Final[int] = 500
SMALL_RANGE: Final[int] = 16
MEMBER: Final[str] = 'shapes.txt'
COLUMNS: Final[tuple[str, ...]] = (
    'shape_id',
    'shape_pt_sequence',
    'shape_pt_lon',
    'shape_pt_lat',
)

logger = logging.getLogger(__name__)

Run = list[tuple[int, float, float]]
ShapeRow = tuple[str, str, float, float, float, float, bytes]


def shape_groups(
    *,
    archive: Path,
) -> Iterator[tuple[str, Iterator[tuple[str, str, str]]]]:
    """
    Group the rows of shapes.txt by shape ID, as they come.

    Parameters
    ----------
    archive : Path
        GTFS zip.

    Yields
    ------
    tuple[str, Iterator[tuple[str, str, str]]]
        A shape ID and its consecutive rows as (sequence, lon, lat) text.
        The rows can only be read until the next group is taken.
    """
    with zipfile.ZipFile(archive) as feed:
        if MEMBER not in feed.namelist():
            return
        with feed.open(MEMBER) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding='utf-8-sig'))
            header = next(reader, [])
            pick = itemgetter(*(header.index(name) for name in COLUMNS[1:]))
            for shape_id, rows in groupby(
                filter(None, reader),
                key=itemgetter(header.index(COLUMNS[0])),
            ):
                yield shape_id, map(pick, rows)


def noted_split(*, split: set[str], shape_id: str) -> None:
    """
    Note a shape whose rows come in more than one run, logging it once.

    Parameters
    ----------
    split : set[str]
        The split shapes so far; the shape is added.
    shape_id : str
        The shape.
    """
    if shape_id not in split:
        logger.warning('shape %s not contiguous', shape_id)
        split.add(shape_id)


def shape_runs(
    *,
    archive: Path,
    wanted: Collection[str],
    split: set[str] | None = None,
) -> Iterator[tuple[str, Run]]:
    """
    Stream the points of wanted shapes, one shape at a time.

    shapes.txt is too big to hold in memory, so only the current run is
    kept. Rows of unwanted shapes are skipped without parsing their
    numbers.

    Parameters
    ----------
    archive : Path
        GTFS zip; a missing shapes.txt yields nothing.
    wanted : Collection[str]
        Raw shape IDs to read.
    split : set[str] | None
        Filled with the wanted IDs whose rows come in more than one run.
        Only the first run of such a shape is yielded, so it is incomplete.

    Yields
    ------
    tuple[str, Run]
        A shape ID and its (sequence, lon, lat) points sorted by sequence.
    """
    split = set() if split is None else split
    done: set[str] = set()
    for shape_id, rows in shape_groups(archive=archive):
        if shape_id not in wanted:
            continue
        if shape_id in done:
            noted_split(split=split, shape_id=shape_id)
            continue
        done.add(shape_id)
        yield shape_id, sorted(
            (int(seq), float(lon), float(lat)) for seq, lon, lat in rows
        )


@dataclass(frozen=True)
class Line:
    """A line's coordinates as arrays and as plain lists."""

    xs: NDArray[np.float64]
    ys: NDArray[np.float64]
    x_list: list[float]
    y_list: list[float]


def segment_gap(
    *,
    dx: float,
    dy: float,
    cx: float,
    cy: float,
) -> float:
    """
    Distance from a point to a segment that starts at the origin.

    Parameters
    ----------
    dx : float
        Point x, relative to the segment's start.
    dy : float
        Point y, relative to the segment's start.
    cx : float
        Segment end x, relative to its start.
    cy : float
        Segment end y, relative to its start.

    Returns
    -------
    float
        The distance; to the start when the segment has no length.
    """
    length = cx * cx + cy * cy
    along = min(max((dx * cx + dy * cy) / length, 0.0), 1.0) if length else 0.0
    ex = dx - along * cx
    ey = dy - along * cy
    return math.sqrt(ex * ex + ey * ey)


def farthest_small(*, line: Line, first: int, last: int) -> tuple[int, float]:
    """
    Find the point farthest from a chord, in plain Python.

    Parameters
    ----------
    line : Line
        Points.
    first : int
        Index of the chord's start.
    last : int
        Index of the chord's end, at least two after first.

    Returns
    -------
    tuple[int, float]
        Index of the farthest point strictly between first and last, and
        its distance from the chord segment.
    """
    sx, sy = line.x_list[first], line.y_list[first]
    cx, cy = line.x_list[last] - sx, line.y_list[last] - sy
    gaps = [
        segment_gap(dx=x - sx, dy=y - sy, cx=cx, cy=cy)
        for x, y in zip(
            line.x_list[first + 1:last],
            line.y_list[first + 1:last],
            strict=True,
        )
    ]
    index = max(range(len(gaps)), key=gaps.__getitem__)
    return first + 1 + index, gaps[index]


def farthest_large(*, line: Line, first: int, last: int) -> tuple[int, float]:
    """
    Find the point farthest from a chord, with numpy.

    It does the same arithmetic as farthest_small, so both give the same
    answer to the last bit.

    Parameters
    ----------
    line : Line
        Points.
    first : int
        Index of the chord's start.
    last : int
        Index of the chord's end, at least two after first.

    Returns
    -------
    tuple[int, float]
        Index of the farthest point strictly between first and last, and
        its distance from the chord segment.
    """
    sx, sy = line.x_list[first], line.y_list[first]
    cx, cy = line.x_list[last] - sx, line.y_list[last] - sy
    dx = line.xs[first + 1:last] - sx
    dy = line.ys[first + 1:last] - sy
    length = cx * cx + cy * cy
    along = (
        np.clip((dx * cx + dy * cy) / length, 0.0, 1.0)
        if length
        else np.zeros_like(dx)
    )
    gaps = np.sqrt((dx - along * cx) ** 2 + (dy - along * cy) ** 2)
    index = int(np.argmax(gaps))
    return first + 1 + index, float(gaps[index])


def farthest(
    *, line: Line, span: tuple[int, int], small_range: int,
) -> tuple[int, float]:
    """
    Find the point of a range farthest from its chord.

    Parameters
    ----------
    line : Line
        The line.
    span : tuple[int, int]
        The range's first and last index.
    small_range : int
        Ranges this short are searched in plain Python.

    Returns
    -------
    tuple[int, float]
        The point's index and its distance.
    """
    first, last = span
    search = farthest_small if last - first < small_range else farthest_large
    return search(line=line, first=first, last=last)


def simplify(
    *,
    points: NDArray[np.float64],
    tolerance: float,
    small_range: int = SMALL_RANGE,
) -> NDArray[np.float64]:
    """
    Simplify a line with Douglas-Peucker, keeping both ends.

    Distances are to the chord as a segment, so a point beyond either end
    of the chord, as on a route that doubles back, is measured from that
    end.

    Parameters
    ----------
    points : NDArray[np.float64]
        Points as rows of x and y.
    tolerance : float
        Furthest a dropped point may lie from the simplified line.
    small_range : int
        Ranges of this many points or fewer are searched in plain Python,
        which is faster than numpy for a handful of points.

    Returns
    -------
    NDArray[np.float64]
        The kept points, in order.
    """
    xs, ys = points[:, 0], points[:, 1]
    line = Line(xs=xs, ys=ys, x_list=xs.tolist(), y_list=ys.tolist())
    keep = np.isin(np.arange(len(points)), (0, len(points) - 1))
    stack = [(0, len(points) - 1)] if len(points) > 2 else []
    while stack:
        first, last = stack.pop()
        index, gap = farthest(
            line=line, span=(first, last), small_range=small_range,
        )
        if gap > tolerance:
            keep[index] = True
            stack.extend(
                span for span in ((first, index), (index, last))
                if span[1] - span[0] > 1
            )
    return points[keep]


def shape_record(
    *,
    shape_id: str,
    mode: str,
    run: Run,
    projection: Projection,
) -> ShapeRow:
    """
    Turn one shape's points into a shapes row.

    Parameters
    ----------
    shape_id : str
        Namespaced shape ID.
    mode : str
        Feed source of the trips that use it.
    run : Run
        (sequence, lon, lat) points in order.
    projection : Projection
        The projection to store the points in.

    Returns
    -------
    ShapeRow
        Values of a shapes row. The bounds are widened to whole decimetres
        so they hold the rounded points of the blob.
    """
    kept = simplify(
        points=np.array(
            [
                to_metres(lon=lon, lat=lat, projection=projection)
                for _, lon, lat in run
            ],
            dtype=np.float64,
        ),
        tolerance=SIMPLIFY_M,
    )
    shape = make_shape(
        shape_id=shape_id,
        points=[(float(x), float(y)) for x, y in kept],
    )
    low = np.floor(kept.min(axis=0) * DECIMETRES) / DECIMETRES
    high = np.ceil(kept.max(axis=0) * DECIMETRES) / DECIMETRES
    return (shape_id, str(mode), float(low[0]), float(low[1]),
            float(high[0]), float(high[1]), pack_shape(shape=shape))


def insert_shapes(
    *,
    conn: sqlite3.Connection,
    records: Iterator[ShapeRow],
) -> int:
    """
    Insert shapes rows in batches.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database with the schema.
    records : Iterator[ShapeRow]
        Rows to insert.

    Returns
    -------
    int
        Number of rows inserted.
    """
    count = 0
    for chunk in batched(records, SHAPES_BATCH):
        conn.executemany(
            'INSERT INTO shapes VALUES (?, ?, ?, ?, ?, ?, ?)',
            chunk,
        )
        count += len(chunk)
    return count


def load_shapes(
    *,
    conn: sqlite3.Connection,
    source: Source,
    modes: Mapping[str, str],
    projection: Projection,
) -> int:
    """
    Store the simplified shapes one feed's trips use.

    A shape whose rows are not all together in shapes.txt is not stored.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database with the schema.
    source : Source
        Feed; its name prefixes the stored shape IDs.
    modes : Mapping[str, str]
        Raw shape ID to feed source, for the shapes to load.
    projection : Projection
        The projection to store the points in.

    Returns
    -------
    int
        Number of shapes stored.
    """
    split: set[str] = set()
    count = insert_shapes(conn=conn, records=(
        shape_record(
            shape_id=f'{source.name}:{shape_id}',
            mode=modes[shape_id],
            run=run,
            projection=projection,
        )
        for shape_id, run in shape_runs(
            archive=source.archive,
            wanted=modes.keys(),
            split=split,
        )
    ))
    conn.executemany(
        'DELETE FROM shapes WHERE shape_id = ?',
        [(f'{source.name}:{shape_id}',) for shape_id in sorted(split)],
    )
    return count - len(split)
