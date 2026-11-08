"""Tests for choosing the tiles to build."""

import sqlite3
from pathlib import Path

import pytest

from paperroute.build.timetable import SCHEMA
from paperroute.city import SYDNEY, SYDNEY_PROJECTION
from paperroute_transit.build.tile_set import (
    SEA_MARGIN_DEG,
    blocks,
    coverage,
    coverage_lonlat,
    stop_points,
    tile_set,
)
from paperroute_transit.core.geo import to_lonlat, to_metres
from paperroute_transit.core.tiles import TILE, to_pixels


def test_tile_set_covers_half_screen() -> None:
    """One stop's tiles fill the box half a screen either side of it."""
    stop = to_metres(lon=151.0, lat=-33.8, projection=SYDNEY_PROJECTION)
    x, y = to_pixels(point=stop, mpp=8, projection=SYDNEY_PROJECTION)
    tiles = tile_set(stops=[stop], mpp=8, projection=SYDNEY_PROJECTION)
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
    first = to_metres(lon=151.0, lat=-33.8, projection=SYDNEY_PROJECTION)
    second = (first[0] + 256 * 8, first[1])
    one = tile_set(stops=[first], mpp=8, projection=SYDNEY_PROJECTION)
    both = tile_set(stops=[first, second], mpp=8, projection=SYDNEY_PROJECTION)
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
    points = stop_points(
        timetable=path,
        bbox=(150.9, -33.9, 151.1, -33.7),
        projection=SYDNEY_PROJECTION,
    )
    expected = [
        to_metres(lon=151.0, lat=-33.8, projection=SYDNEY_PROJECTION),
        to_metres(lon=151.01, lat=-33.81, projection=SYDNEY_PROJECTION),
    ]
    assert points == sorted(
        (float(round(x)), float(round(y))) for x, y in expected
    )


def test_stop_points_ignore_stops_outside_nsw(tmp_path: Path) -> None:
    """Only the stops inside the NSW box are returned."""
    path = tmp_path / 'timetable.db'
    inside = {
        'Sydney': (-33.87, 151.21),
        'Broken Hill': (-31.95, 141.45),
        'Tweed Heads': (-28.18, 153.54),
    }
    outside = (
        (-37.81, 144.96),
        (-27.47, 153.03),
        (0.0, 0.0),
        (-34.9, -180.0),
        (30.5, -30.5),
    )
    rows = [
        (name, name, lat, lon, '')
        for name, (lat, lon) in inside.items()
    ] + [
        (f'out{index}', 'Out', lat, lon, '')
        for index, (lat, lon) in enumerate(outside)
    ]
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            'INSERT INTO stops VALUES (?, ?, NULL, ?, ?, ?)',
            rows,
        )
    conn.close()
    points = stop_points(
        timetable=path,
        bbox=SYDNEY.bbox,
        projection=SYDNEY_PROJECTION,
    )
    expected = sorted(
        (float(round(x)), float(round(y)))
        for x, y in (
            to_metres(lon=lon, lat=lat, projection=SYDNEY_PROJECTION)
            for lat, lon in inside.values()
        )
    )
    assert points == expected


def test_coverage_bounds_every_tile_at_every_zoom() -> None:
    """The coverage box holds each tile at each zoom."""
    x0, y0 = SYDNEY_PROJECTION.grid_origin
    box = coverage(
        tiles={8: {(1, 2)}, 3: {(100, 1), (101, 9)}},
        projection=SYDNEY_PROJECTION,
    )
    assert box == (
        x0 + 256 * 8,
        y0 - 10 * 256 * 3,
        x0 + 102 * 256 * 3,
        y0 - 256 * 3,
    )


def test_coverage_lonlat_is_clipped_to_nsw_with_a_sea_margin() -> None:
    """A huge box is cut back to NSW grown by the sea margin."""
    west, south = to_metres(lon=100.0, lat=-60.0, projection=SYDNEY_PROJECTION)
    east, north = to_metres(lon=170.0, lat=-10.0, projection=SYDNEY_PROJECTION)
    clipped = coverage_lonlat(
        box=(west, south, east, north),
        projection=SYDNEY_PROJECTION,
        area=SYDNEY.bbox,
    )
    assert clipped == pytest.approx(
        (
            SYDNEY.bbox[0] - SEA_MARGIN_DEG,
            SYDNEY.bbox[1] - SEA_MARGIN_DEG,
            SYDNEY.bbox[2] + SEA_MARGIN_DEG,
            SYDNEY.bbox[3] + SEA_MARGIN_DEG,
        ),
    )


def test_coverage_lonlat_keeps_a_small_box() -> None:
    """A box inside NSW keeps its corners."""
    west, south = to_metres(lon=151.0, lat=-33.9, projection=SYDNEY_PROJECTION)
    east, north = to_metres(lon=151.1, lat=-33.8, projection=SYDNEY_PROJECTION)
    clipped = coverage_lonlat(
        box=(west, south, east, north),
        projection=SYDNEY_PROJECTION,
        area=SYDNEY.bbox,
    )
    assert clipped == (
        *to_lonlat(point=(west, south), projection=SYDNEY_PROJECTION),
        *to_lonlat(point=(east, north), projection=SYDNEY_PROJECTION),
    )


def test_coverage_lonlat_refuses_a_box_outside_nsw() -> None:
    """A box wholly outside NSW has nothing to build."""
    west, south = to_metres(lon=130.0, lat=-31.0, projection=SYDNEY_PROJECTION)
    east, north = to_metres(lon=131.0, lat=-30.0, projection=SYDNEY_PROJECTION)
    with pytest.raises(ValueError, match="outside the city's area"):
        coverage_lonlat(
            box=(west, south, east, north),
            projection=SYDNEY_PROJECTION,
            area=SYDNEY.bbox,
        )


def test_blocks_are_sorted_and_unique() -> None:
    """Tiles fall into sorted blocks of 8 by 8."""
    tiles = {(0, 0), (7, 7), (8, 0), (-1, 3), (15, 1)}
    assert blocks(tiles=tiles) == [(-1, 0), (0, 0), (1, 0)]
