"""Tests for choosing the tiles to build."""

import sqlite3
from pathlib import Path

import pytest

from paperroute.build.tile_set import (
    blocks,
    coverage,
    coverage_lonlat,
    stop_points,
    tile_set,
)
from paperroute.build.timetable import SCHEMA
from paperroute.core.geo import to_lonlat, to_metres
from paperroute.core.tiles import GRID_ORIGIN, OPAL_BBOX, TILE, to_pixels


def test_tile_set_covers_half_screen() -> None:
    """One stop's tiles fill the box half a screen either side of it."""
    stop = to_metres(lon=151.0, lat=-33.8)
    x, y = to_pixels(point=stop, mpp=8)
    tiles = tile_set(stops=[stop], mpp=8)
    cols = [col for col, _ in tiles]
    rows = [row for _, row in tiles]
    assert min(cols) * TILE <= x - 400
    assert (max(cols) + 1) * TILE >= x + 400
    assert min(rows) * TILE <= y - 240
    assert (max(rows) + 1) * TILE >= y + 240
    width = max(cols) - min(cols) + 1
    assert len(tiles) == width * (max(rows) - min(rows) + 1)


def test_tile_set_joins_nearby_stops() -> None:
    """Two stops a tile apart share most of their tiles."""
    first = to_metres(lon=151.0, lat=-33.8)
    second = (first[0] + 256 * 8, first[1])
    one = tile_set(stops=[first], mpp=8)
    both = tile_set(stops=[first, second], mpp=8)
    assert one < both
    assert len(both) == len(one) + len({row for _, row in one})


def test_stop_points_reads_stops_in_the_box(tmp_path: Path) -> None:
    """Stops inside the box are projected and deduplicated to metres."""
    path = tmp_path / 'timetable.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            'INSERT INTO stops VALUES (?, ?, NULL, ?, ?, ?)',
            [
                ('a1', 'A', -33.8, 151.0, '1'),
                ('a2', 'A', -33.8, 151.0, '2'),
                ('b', 'B', -33.81, 151.01, ''),
                ('far', 'Far', -33.0, 151.0, ''),
            ],
        )
        conn.execute(
            "INSERT INTO stops VALUES ('none', 'None', NULL, NULL, NULL, '')",
        )
    conn.close()
    points = stop_points(timetable=path, bbox=(150.9, -33.9, 151.1, -33.7))
    expected = [
        to_metres(lon=151.0, lat=-33.8),
        to_metres(lon=151.01, lat=-33.81),
    ]
    assert points == sorted(
        (float(round(x)), float(round(y))) for x, y in expected
    )


def test_coverage_bounds_every_tile_at_every_zoom() -> None:
    """The coverage box holds each tile at each zoom."""
    x0, y0 = GRID_ORIGIN
    box = coverage(tiles={8: {(1, 2)}, 3: {(100, 1), (101, 9)}})
    assert box == (
        x0 + 256 * 8,
        y0 - 10 * 256 * 3,
        x0 + 102 * 256 * 3,
        y0 - 256 * 3,
    )


def test_coverage_lonlat_is_clipped_to_opal() -> None:
    """A box past the Opal area is cut back to it."""
    west, south = to_metres(lon=149.0, lat=-36.0)
    east, north = to_metres(lon=153.0, lat=-31.0)
    clipped = coverage_lonlat(box=(west, south, east, north))
    assert clipped == OPAL_BBOX


def test_coverage_lonlat_keeps_a_small_box() -> None:
    """A box inside the Opal area keeps its corners."""
    west, south = to_metres(lon=151.0, lat=-33.9)
    east, north = to_metres(lon=151.1, lat=-33.8)
    clipped = coverage_lonlat(box=(west, south, east, north))
    assert clipped == (
        *to_lonlat(point=(west, south)),
        *to_lonlat(point=(east, north)),
    )


def test_coverage_lonlat_refuses_a_box_outside_opal() -> None:
    """A box wholly outside the Opal area has nothing to build."""
    west, south = to_metres(lon=153.0, lat=-31.0)
    east, north = to_metres(lon=154.0, lat=-30.0)
    with pytest.raises(ValueError, match='outside the Opal area'):
        coverage_lonlat(box=(west, south, east, north))


def test_blocks_are_sorted_and_unique() -> None:
    """Tiles fall into sorted blocks of 8 by 8."""
    tiles = {(0, 0), (7, 7), (8, 0), (-1, 3), (15, 1)}
    assert blocks(tiles=tiles) == [(-1, 0), (0, 0), (1, 0)]
