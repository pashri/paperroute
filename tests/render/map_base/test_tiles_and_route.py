"""Tests for map base: tiles and route."""

import math
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest

from paperroute_device.render.canvas import INK, PAPER
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.build.map_style import LIGHT
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    screen_point,
)
from paperroute_transit.core.tiles import (
    TILE,
    StreetLabel,
    TileVariant,
    tile_of,
    to_pixels,
)
from paperroute_transit.render.map_base import (
    compose_tiles,
    draw_map_base,
    on_route,
    window_of,
)
from tests.render.map_base.helpers import (
    CENTRE,
    MPP,
    OFFSET,
    ROUTE,
    across,
    base,
    build,
    centre_row,
    grey_drawn,
    stop_tile,
)


def test_window_of_matches_screen_point() -> None:
    """The stop's global pixel minus the window is its map pixel."""
    left, top = window_of(base=base())
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    sx, sy = screen_point(
        centre=CENTRE,
        mpp=MPP,
        point=CENTRE,
        projection=SYDNEY_PROJECTION,
    )
    assert (gx - left, gy - top) == pytest.approx((sx, sy))


def test_compose_puts_the_stop_tile_under_the_centre(
    tmp_path: Path,
) -> None:
    """A LIGHT tile at the stop's tile lies under the map centre."""
    light = np.full((TILE, TILE), LIGHT, dtype=np.uint8)
    tiles = build(out=tmp_path / 'tiles.db', tiles={stop_tile(): light})
    with closing(tiles):
        grey = compose_tiles(
            tiles=tiles,
            base=base(),
            variant=TileVariant.GREY,
        )
    assert grey.shape == (MAP_HEIGHT, MAP_WIDTH)
    assert grey.dtype == np.uint8
    assert grey[MAP_HEIGHT // 2, MAP_WIDTH // 2] == LIGHT


def test_missing_tiles_are_paper(tmp_path: Path) -> None:
    """With no tiles stored the composed map is all paper."""
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = compose_tiles(
            tiles=tiles,
            base=base(),
            variant=TileVariant.GREY,
        )
    assert grey.shape == (MAP_HEIGHT, MAP_WIDTH)
    assert np.all(grey == PAPER)


def test_tiles_and_route_share_one_transform(tmp_path: Path) -> None:
    """A tile's pixel at a point and the route at it land together."""
    point = (CENTRE[0] + 600.0, CENTRE[1] - 400.0)
    gx, gy = to_pixels(point=point, mpp=MPP, projection=SYDNEY_PROJECTION)
    col, row = tile_of(pixel=(gx, gy))
    dot = np.full((TILE, TILE), PAPER, dtype=np.uint8)
    dot[math.floor(gy) - row * TILE, math.floor(gx) - col * TILE] = INK
    tiles = build(out=tmp_path / 'tiles.db', tiles={(col, row): dot})
    sx, sy = screen_point(
        centre=CENTRE,
        mpp=MPP,
        point=point,
        projection=SYDNEY_PROJECTION,
    )
    with closing(tiles):
        grey = compose_tiles(
            tiles=tiles,
            base=base(),
            variant=TileVariant.GREY,
        )
    assert grey[math.floor(sy), math.floor(sx)] == INK
    assert np.count_nonzero(grey == INK) == 1


def test_stop_marker_sits_on_the_map_centre(tmp_path: Path) -> None:
    """The stop's dot, ring and paper disc are centred within 1 px."""
    grey = grey_drawn(tmp_path=tmp_path, map_base=base())
    x, y = MAP_WIDTH // 2, MAP_HEIGHT // 2
    assert grey[y, x] == INK
    assert grey[y, x + 12] == INK
    assert grey[y, x - 12] == INK
    assert grey[y - 12, x] == INK
    assert grey[y + 12, x] == INK
    assert grey[y, x + 7] == PAPER
    assert grey[y, x + 15] == PAPER


def test_route_line_is_ink_through_the_centre(tmp_path: Path) -> None:
    """An east-west route is an ink stroke across the map, bar the stop."""
    grey = grey_drawn(
        tmp_path=tmp_path,
        map_base=base(route=across(half=5000.0)),
    )
    row = centre_row()
    band = grey[row - 3 : row + 4]
    assert np.all(np.any(band[:, 20:240] == INK, axis=0))
    assert np.all(np.any(band[:, 280:500] == INK, axis=0))
    assert np.all(grey[row - 20, 20:240] == PAPER)


def test_missing_shape_draws_no_line(tmp_path: Path) -> None:
    """No route, or a one-point route, draws nothing and doesn't raise."""
    lonely = ((CENTRE,),)
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        for route in ((), lonely):
            grey = draw_map_base(
                base=base(route=route),
                tiles=tiles,
                variant=TileVariant.GREY,
            )
            assert np.all(grey[100:300, 20:200] == PAPER)


def test_on_route_accepts_a_label_along_the_route() -> None:
    """A stretch lying on the route, either way round, matches."""
    for angle in (0.0, 180.0, 10.0):
        label = StreetLabel(
            name='Church Street',
            x=1150.0,
            y=2100.0,
            angle=angle,
            width=80.0,
            start=(1050.0, 2100.0),
            end=(1250.0, 2100.0),
        )
        assert on_route(label=label, routes=ROUTE, offset=OFFSET)


def test_on_route_rejects_a_crossing_street() -> None:
    """A perpendicular street meets the route at one point only."""
    label = StreetLabel(
        name='George Street',
        x=1200.0,
        y=2100.0,
        angle=90.0,
        width=80.0,
        start=(1200.0, 2000.0),
        end=(1200.0, 2200.0),
    )
    assert not on_route(label=label, routes=ROUTE, offset=OFFSET)


def test_on_route_rejects_a_street_touched_at_one_end() -> None:
    """The route runs along only 30% of the stretch."""
    label = StreetLabel(
        name='Macquarie Street',
        x=1440.0,
        y=2100.0,
        angle=0.0,
        width=80.0,
        start=(1340.0, 2100.0),
        end=(1540.0, 2100.0),
    )
    assert not on_route(label=label, routes=ROUTE, offset=OFFSET)


def test_on_route_rejects_a_label_turned_off_the_route() -> None:
    """A stretch on the route whose angle disagrees by 30 degrees fails."""
    label = StreetLabel(
        name='Church Street',
        x=1150.0,
        y=2100.0,
        angle=30.0,
        width=80.0,
        start=(1050.0, 2100.0),
        end=(1250.0, 2100.0),
    )
    assert not on_route(label=label, routes=ROUTE, offset=OFFSET)


def test_on_route_rejects_a_parallel_street_6_px_away() -> None:
    """A street beside the route, not on it, fails the 5 px reach."""
    label = StreetLabel(
        name='Argyle Street',
        x=1150.0,
        y=2106.0,
        angle=0.0,
        width=80.0,
        start=(1050.0, 2106.0),
        end=(1250.0, 2106.0),
    )
    assert not on_route(label=label, routes=ROUTE, offset=OFFSET)


def test_on_route_without_a_route_is_false() -> None:
    """No route lines, or only single points, match nothing."""
    label = StreetLabel(
        name='Church Street',
        x=1150.0,
        y=2100.0,
        angle=0.0,
        width=80.0,
        start=(1050.0, 2100.0),
        end=(1250.0, 2100.0),
    )
    point = [np.array([[150.0, 100.0]], dtype=np.float64)]
    assert not on_route(label=label, routes=[], offset=OFFSET)
    assert not on_route(label=label, routes=point, offset=OFFSET)
