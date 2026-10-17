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
from numpy.typing import NDArray

from paperroute.build.gtfs_files import Source
from paperroute.build.shapes import (
    SIMPLIFY_M,
    load_shapes,
    shape_record,
    shape_runs,
    simplify,
)
from paperroute.build.timetable import Window, build_timetable, create_schema
from paperroute.core.geo import (
    METRES_PER_DEGREE_LAT,
    METRES_PER_DEGREE_LON,
    ORIGIN_LAT,
    ORIGIN_LON,
    to_metres,
    unpack_shape,
)
from paperroute.core.model import Mode
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


@pytest.mark.parametrize(('bump', 'count'), [(1.6, 3), (1.5, 2), (1.4, 2)])
def test_simplify_keeps_a_corner_beyond_tolerance(
    bump: float,
    count: int,
) -> None:
    """A bump beyond 1.5 m stays; one at or within it goes."""
    assert SIMPLIFY_M == 1.5
    points = np.array(
        [(0.0, 0.0), (25.0, 0.0), (50.0, bump), (75.0, 0.0), (100.0, 0.0)],
        dtype=np.float64,
    )
    kept = simplify(points=points, tolerance=SIMPLIFY_M)
    assert len(kept) == count
    assert kept[0].tolist() == [0.0, 0.0]
    assert kept[-1].tolist() == [100.0, 0.0]


@pytest.mark.parametrize('small_range', [0, 16])
def test_simplify_measures_from_the_chord_end(small_range: int) -> None:
    """A point past the chord's end is kept, as on a route doubling back."""
    points = np.array([(0.0, 0.0), (100.0, 0.0), (50.0, 0.0)])
    kept = simplify(
        points=points,
        tolerance=SIMPLIFY_M,
        small_range=small_range,
    )
    assert kept.tolist() == points.tolist()


@pytest.mark.parametrize('small_range', [0, 16])
def test_simplify_keeps_the_far_side_of_a_closed_loop(
    small_range: int,
) -> None:
    """A loop that ends where it starts keeps its far corners."""
    points = np.array(
        [(0.0, 0.0), (25.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 0.0)],
    )
    kept = simplify(
        points=points,
        tolerance=SIMPLIFY_M,
        small_range=small_range,
    )
    assert kept.tolist() == [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0],
                             [0.0, 0.0]]


def varied_lines() -> list[NDArray[np.float64]]:
    """
    Random walks of many lengths, a closed loop and a stalled line.

    Returns
    -------
    list[NDArray[np.float64]]
        Lines as rows of x and y.
    """
    rng = np.random.default_rng(seed=5)
    walks = [
        np.cumsum(rng.normal(scale=3.0, size=(count, 2)), axis=0)
        for count in (2, 3, 5, 16, 17, 40, 300, 2000)
    ]
    loop = np.array([(math.cos(step / 10), math.sin(step / 10))
                     for step in range(64)]) * 40.0
    stalled = np.array([(0.0, 0.0), (5.0, 3.0), (5.0, 3.0), (0.0, 0.0),
                        (0.0, 0.0), (9.0, -2.0), (0.0, 0.0)])
    return [*walks, np.vstack([loop, loop[:1]]), stalled]


def test_simplify_paths_agree_exactly() -> None:
    """The plain Python and numpy searches keep exactly the same points."""
    for line in varied_lines():
        python = simplify(points=line, tolerance=SIMPLIFY_M,
                          small_range=len(line) + 1)
        numpy = simplify(points=line, tolerance=SIMPLIFY_M, small_range=0)
        mixed = simplify(points=line, tolerance=SIMPLIFY_M)
        assert np.array_equal(python, numpy)
        assert np.array_equal(python, mixed)


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
    split: set[str] = set()
    with caplog.at_level(logging.WARNING):
        runs = list(shape_runs(archive=archive, wanted={'S1'}, split=split))
    assert runs == [('S1', [(1, *ROUTE[0]), (2, *ROUTE[1])])]
    assert split == {'S1'}
    assert [record.getMessage() for record in caplog.records] == [
        'shape S1 not contiguous',
    ]


def stored_shapes(*, archive: Path, modes: dict[str, Mode]) -> list[str]:
    """
    Load one feed's shapes into a new database and list what's stored.

    Parameters
    ----------
    archive : Path
        GTFS zip named 'bundle'.
    modes : dict[str, Mode]
        Raw shape ID to mode.

    Returns
    -------
    list[str]
        Stored shape IDs, sorted.
    """
    with closing(sqlite3.connect(':memory:')) as conn:
        create_schema(conn=conn)
        count = load_shapes(conn=conn, modes=modes,
                            source=Source(name='bundle', archive=archive))
        stored = [row[0] for row in conn.execute(
            'SELECT shape_id FROM shapes ORDER BY shape_id')]
    assert count == len(stored)
    return stored


def test_load_shapes_drops_a_split_shape(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A shape in three runs isn't stored, and warns once."""
    archive = shapes_zip(path=tmp_path / 's.zip', rows=[
        *shape_rows(shape_id='S1', points=ROUTE[:2]),
        *shape_rows(shape_id='S2', points=ROUTE),
        *shape_rows(shape_id='S1', points=ROUTE[2:]),
        *shape_rows(shape_id='S2', points=ROUTE[:1]),
        *shape_rows(shape_id='S1', points=ROUTE[:1]),
        *shape_rows(shape_id='S3', points=ROUTE),
    ])
    with caplog.at_level(logging.WARNING):
        stored = stored_shapes(
            archive=archive,
            modes={'S1': Mode.BUS, 'S3': Mode.BUS},
        )
    assert stored == ['bundle:S3']
    assert [record.getMessage() for record in caplog.records] == [
        'shape S1 not contiguous',
    ]


def test_load_shapes_drops_shapes_ordered_by_sequence(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Rows sorted by sequence across shapes store nothing."""
    a_rows = shape_rows(shape_id='A', points=ROUTE)
    b_rows = shape_rows(shape_id='B', points=ROUTE)
    archive = shapes_zip(path=tmp_path / 's.zip', rows=[
        row for pair in zip(a_rows, b_rows, strict=True) for row in pair
    ])
    with caplog.at_level(logging.WARNING):
        stored = stored_shapes(
            archive=archive,
            modes={'A': Mode.BUS, 'B': Mode.BUS},
        )
    assert not stored
    assert [record.getMessage() for record in caplog.records] == [
        'shape A not contiguous',
        'shape B not contiguous',
    ]


def lonlat(*, x: float, y: float) -> tuple[float, float]:
    """
    The longitude and latitude that project to a point.

    Parameters
    ----------
    x : float
        Metres east of the origin.
    y : float
        Metres north of the origin.

    Returns
    -------
    tuple[float, float]
        Longitude and latitude.
    """
    return (ORIGIN_LON + x / METRES_PER_DEGREE_LON,
            ORIGIN_LAT + y / METRES_PER_DEGREE_LAT)


def test_shape_record_widens_bounds_to_whole_decimetres() -> None:
    """Bounds hold points that round outward when packed."""
    run = [(1, *lonlat(x=1000.04, y=2000.04)),
           (2, *lonlat(x=1500.06, y=2500.06))]
    shape_id, mode, west, south, east, north, blob = shape_record(
        shape_id='bundle:S1', mode=Mode.BUS, run=run)
    assert (shape_id, mode) == ('bundle:S1', 'bus')
    assert (west, south, east, north) == (1000.0, 2000.0, 1500.1, 2500.1)
    points = unpack_shape(shape_id=shape_id, blob=blob).points
    assert points == ((1000.0, 2000.0), (1500.1, 2500.1))


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
