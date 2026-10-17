"""Tests for streaming, simplifying and storing GTFS shapes."""

import logging
import math
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

import numpy as np
import pytest

from paperroute.build.shapes import SIMPLIFY_M, shape_runs, simplify
from paperroute.build.timetable import Source, Window, build_timetable
from paperroute.core.geo import to_metres, unpack_shape
from tests.build.feeds import shape_rows, shaped_bus_feed
from tests.helpers import write_feed

ROUTE: Final[list[tuple[float, float]]] = [
    (150.9746, -33.7924),
    (150.981, -33.7897),
    (150.9874, -33.7870),
    (151.0049, -33.8176),
]


def test_simplify_drops_collinear_points() -> None:
    """Points on a straight line simplify to the two ends."""
    points = np.array(
        [(float(index), 2.0 * index) for index in range(100)],
        dtype=np.float64,
    )
    kept = simplify(points=points, tolerance=SIMPLIFY_M)
    assert kept.tolist() == [[0.0, 0.0], [99.0, 198.0]]


@pytest.mark.parametrize(('bump', 'count'), [(2.0, 3), (1.0, 2)])
def test_simplify_keeps_a_corner_beyond_tolerance(
    bump: float,
    count: int,
) -> None:
    """A bump beyond the tolerance stays; one within it goes."""
    points = np.array(
        [(0.0, 0.0), (25.0, 0.0), (50.0, bump), (75.0, 0.0), (100.0, 0.0)],
        dtype=np.float64,
    )
    kept = simplify(points=points, tolerance=SIMPLIFY_M)
    assert len(kept) == count
    assert kept[0].tolist() == [0.0, 0.0]
    assert kept[-1].tolist() == [100.0, 0.0]


def shapes_zip(*, path: Path, rows: list[dict[str, str]]) -> Path:
    """
    Write a zip holding only shapes.txt.

    Parameters
    ----------
    path : Path
        Zip to write.
    rows : list[dict[str, str]]
        shapes.txt rows.

    Returns
    -------
    Path
        The zip.
    """
    return write_feed(path=path, files={'shapes.txt': rows})


def test_shape_runs_streams_only_wanted(tmp_path: Path) -> None:
    """Only wanted shapes come back, each sorted by sequence."""
    s1 = shape_rows(shape_id='S1', points=ROUTE[:3])
    archive = shapes_zip(path=tmp_path / 's.zip', rows=[
        s1[2], s1[0], s1[1],
        *shape_rows(shape_id='S2', points=ROUTE),
        *shape_rows(shape_id='S3', points=ROUTE[:2]),
    ])
    runs = list(shape_runs(archive=archive, wanted={'S1', 'S3'}))
    assert runs == [
        ('S1', [(1, *ROUTE[0]), (2, *ROUTE[1]), (3, *ROUTE[2])]),
        ('S3', [(1, *ROUTE[0]), (2, *ROUTE[1])]),
    ]


def test_shape_runs_skips_a_repeated_run_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A shape split into two runs keeps the first and warns once."""
    archive = shapes_zip(path=tmp_path / 's.zip', rows=[
        *shape_rows(shape_id='S1', points=ROUTE[:2]),
        *shape_rows(shape_id='S2', points=ROUTE),
        *shape_rows(shape_id='S1', points=ROUTE[2:]),
    ])
    with caplog.at_level(logging.WARNING):
        runs = list(shape_runs(archive=archive, wanted={'S1'}))
    assert runs == [('S1', [(1, *ROUTE[0]), (2, *ROUTE[1])])]
    assert [record.getMessage() for record in caplog.records] == [
        'shape S1 not contiguous',
    ]


def test_shape_runs_without_shapes_member_yields_nothing(
    tmp_path: Path,
) -> None:
    """A feed with no shapes.txt has no runs."""
    archive = write_feed(path=tmp_path / 's.zip', files={'stops.txt': []})
    assert not list(shape_runs(archive=archive, wanted={'S1'}))


def test_build_stores_shapes_with_mode_and_bounds(tmp_path: Path) -> None:
    """A bus shape is stored simplified, with its mode and bounds."""
    out = tmp_path / 'timetable.db'
    build_timetable(
        sources=[Source(
            name='bundle',
            archive=shaped_bus_feed(path=tmp_path / 'b.zip', points=ROUTE),
        )],
        window=Window(start=date(2026, 10, 16), days=2),
        out=out,
    )
    with closing(sqlite3.connect(out)) as conn:
        rows = conn.execute('SELECT * FROM shapes').fetchall()
    assert len(rows) == 1
    shape_id, mode, west, south, east, north, blob = rows[0]
    assert (shape_id, mode) == ('bundle:S1', 'bus')
    expected = simplify(
        points=np.array(
            [to_metres(lon=lon, lat=lat) for lon, lat in ROUTE],
            dtype=np.float64,
        ),
        tolerance=SIMPLIFY_M,
    )
    stored = unpack_shape(shape_id=shape_id, blob=blob).points
    assert len(stored) == len(expected) == 3
    for (x, y), (want_x, want_y) in zip(stored, expected, strict=True):
        assert math.dist((x, y), (want_x, want_y)) < 0.1
        assert west <= x <= east
        assert south <= y <= north
