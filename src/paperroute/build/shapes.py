"""Stream GTFS shapes, simplify them and store them in the timetable."""

import csv
import io
import logging
import sqlite3
import zipfile
from collections.abc import Collection, Iterator, Mapping
from itertools import batched, groupby
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np
from numpy.typing import NDArray

from paperroute.core.geo import DECIMETRES, make_shape, pack_shape, to_metres
from paperroute.core.model import Mode

if TYPE_CHECKING:
    from paperroute.build.timetable import Source

SIMPLIFY_M: Final[float] = 1.5
SHAPES_BATCH: Final[int] = 500
MEMBER: Final[str] = 'shapes.txt'
COLUMNS: Final[tuple[str, ...]] = (
    'shape_id',
    'shape_pt_sequence',
    'shape_pt_lon',
    'shape_pt_lat',
)

logger = logging.getLogger(__name__)

Run = list[tuple[int, float, float]]


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
            header = [name.lstrip('\ufeff') for name in next(reader, [])]
            pick = itemgetter(*(header.index(name) for name in COLUMNS[1:]))
            for shape_id, rows in groupby(
                filter(None, reader),
                key=itemgetter(header.index(COLUMNS[0])),
            ):
                yield shape_id, map(pick, rows)


def shape_runs(
    *,
    archive: Path,
    wanted: Collection[str],
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

    Yields
    ------
    tuple[str, Run]
        A shape ID and its (sequence, lon, lat) points sorted by sequence.
        A shape whose rows come back after another shape's is read only
        from its first run.
    """
    done: set[str] = set()
    for shape_id, rows in shape_groups(archive=archive):
        if shape_id not in wanted:
            continue
        if shape_id in done:
            logger.warning('shape %s not contiguous', shape_id)
            continue
        done.add(shape_id)
        yield shape_id, sorted(
            (int(seq), float(lon), float(lat)) for seq, lon, lat in rows
        )


def farthest(
    *,
    points: NDArray[np.float64],
    first: int,
    last: int,
) -> tuple[int, float]:
    """
    Find the point between two others farthest from the chord joining them.

    Parameters
    ----------
    points : NDArray[np.float64]
        Points as rows of x and y.
    first : int
        Index of the chord's start.
    last : int
        Index of the chord's end, after first.

    Returns
    -------
    tuple[int, float]
        Index of the farthest point strictly between first and last, and
        its distance from the chord segment; (first, 0.0) when none.
    """
    if last - first < 2:
        return first, 0.0
    start = points[first]
    chord = points[last] - start
    offsets = points[first + 1:last] - start
    length = float(chord @ chord)
    along = (
        np.clip(offsets @ chord / length, 0.0, 1.0)
        if length
        else np.zeros(len(offsets))
    )
    gaps = np.linalg.norm(offsets - np.outer(along, chord), axis=1)
    index = int(np.argmax(gaps))
    return first + 1 + index, float(gaps[index])


def simplify(
    *,
    points: NDArray[np.float64],
    tolerance: float,
) -> NDArray[np.float64]:
    """
    Simplify a line with Douglas-Peucker, keeping both ends.

    Parameters
    ----------
    points : NDArray[np.float64]
        Points as rows of x and y.
    tolerance : float
        Furthest a dropped point may lie from the simplified line.

    Returns
    -------
    NDArray[np.float64]
        The kept points, in order.
    """
    keep = np.zeros(len(points), dtype=bool)
    keep[[0, -1]] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        index, gap = farthest(points=points, first=first, last=last)
        if gap > tolerance:
            keep[index] = True
            stack.extend(((first, index), (index, last)))
    return points[keep]


def shape_record(
    *,
    shape_id: str,
    mode: Mode,
    run: Run,
) -> tuple[object, ...]:
    """
    Turn one shape's points into a shapes row.

    Parameters
    ----------
    shape_id : str
        Namespaced shape ID.
    mode : Mode
        Mode of the trips that use it.
    run : Run
        (sequence, lon, lat) points in order.

    Returns
    -------
    tuple[object, ...]
        Values of a shapes row. The bounds are widened to whole decimetres so
        they hold the rounded points of the blob.
    """
    kept = simplify(
        points=np.array(
            [to_metres(lon=lon, lat=lat) for _, lon, lat in run],
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


def load_shapes(
    *,
    conn: sqlite3.Connection,
    source: Source,
    modes: Mapping[str, Mode],
) -> int:
    """
    Store the simplified shapes one feed's trips use.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database with the schema.
    source : Source
        Feed; its name prefixes the stored shape IDs.
    modes : Mapping[str, Mode]
        Raw shape ID to mode, for the shapes to load.

    Returns
    -------
    int
        Number of shapes stored.
    """
    records = (
        shape_record(
            shape_id=f'{source.name}:{shape_id}',
            mode=modes[shape_id],
            run=run,
        )
        for shape_id, run in shape_runs(
            archive=source.archive,
            wanted=modes.keys(),
        )
    )
    count = 0
    for chunk in batched(records, SHAPES_BATCH):
        conn.executemany(
            'INSERT INTO shapes VALUES (?, ?, ?, ?, ?, ?, ?)',
            chunk,
        )
        count += len(chunk)
    return count
