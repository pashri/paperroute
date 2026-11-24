"""Tests for map base: base and credit."""

import math
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from paperroute_device.render.canvas import INK, PAPER, text_width
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.adapters.tiles_db import NullTiles
from paperroute_transit.build.map_style import DARK, LIGHT
from paperroute_transit.core.map_fit import CREDIT_ROOM
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    STRIP_MAP_WIDTH,
    MapBase,
    TripMarks,
)
from paperroute_transit.core.tiles import (
    TILE,
    StreetLabel,
    TileVariant,
    to_pixels,
)
from paperroute_transit.render.map_base import (
    draw_map_base,
    name_region,
    on_route,
    redraw_name,
    window_of,
)
from paperroute_transit.render.map_corners import BOX_FACE
from paperroute_transit.render.tile_codec import diffuse
from tests.helpers import exactly_two
from tests.render.map_base.helpers import (
    CENTRE,
    LABEL_SHIFT,
    MPP,
    OFFSET,
    ROUTE,
    across,
    base,
    build,
    centre_row,
    end_screen,
    grey_drawn,
    no_centre_base,
    pattern,
    stop_tile,
    street,
    variants_drawn,
)


def test_base_has_four_levels(tmp_path: Path) -> None:
    """The grey base holds only the four panel levels."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    light = np.full((TILE, TILE), LIGHT, dtype=np.uint8)
    light[:, :40] = 0
    tiles = build(
        out=tmp_path / 'tiles.db',
        tiles={stop_tile(): light},
        labels=[street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)],
    )
    with closing(tiles):
        grey = draw_map_base(
            base=base(route=across(half=5000.0)),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    assert grey.shape == (MAP_HEIGHT, MAP_WIDTH)
    assert grey.dtype == np.uint8
    assert set(np.unique(grey).tolist()) == {INK, DARK, LIGHT, PAPER}


def test_bits_variant_is_black_and_white(tmp_path: Path) -> None:
    """The BITS base holds only ink and paper, as uint8."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    light = np.full((TILE, TILE), LIGHT, dtype=np.uint8)
    tiles = build(
        out=tmp_path / 'tiles.db',
        tiles={stop_tile(): light},
        labels=[street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)],
    )
    with closing(tiles):
        grey = draw_map_base(
            base=base(route=across(half=5000.0)),
            tiles=tiles,
            variant=TileVariant.BITS,
        )
    assert grey.dtype == np.uint8
    assert set(np.unique(grey).tolist()) == {INK, PAPER}


def test_no_centre_is_paper() -> None:
    """A stop with no location gives a plain paper base."""
    grey = draw_map_base(
        base=no_centre_base(),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    assert grey.shape == (MAP_HEIGHT, MAP_WIDTH)
    assert grey.dtype == np.uint8
    assert np.all(grey == PAPER)


def test_window_of_needs_a_centre() -> None:
    """A base with no centre has no window."""
    with pytest.raises(ValueError, match='centre'):
        window_of(base=MapBase(
            centre=None,
            mpp=MPP,
            route=(),
            projection=SYDNEY_PROJECTION,
        ))


def test_no_tiles_says_map_not_installed_in_the_credit_corner() -> None:
    """Without tiles, the note takes the credit's box, not the middle."""
    grey = draw_map_base(
        base=base(),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    middle = MAP_HEIGHT // 2
    assert np.all(grey[middle + 14 : middle + 60] == PAPER)
    width = text_width(text='Map not installed', face=BOX_FACE)
    corner = grey[MAP_HEIGHT - 30 :]
    ink = np.flatnonzero(np.any(corner == INK, axis=0))
    ink = ink[ink > MAP_WIDTH / 2]
    assert ink.max() <= MAP_WIDTH - 12
    assert abs((ink.max() - ink.min() + 1) - width) <= 3
    edge = np.flatnonzero(np.any(corner == DARK, axis=0))
    assert edge.max() == MAP_WIDTH - 8


def test_scale_bar_and_credit_are_drawn(tmp_path: Path) -> None:
    """The scale box at the bottom left and the credit box at the right."""
    grey = grey_drawn(tmp_path=tmp_path, map_base=base())
    scale = grey[MAP_HEIGHT - 16 : MAP_HEIGHT - 12, 12 : 12 + 125]
    assert np.all(scale == INK)
    credit = grey[MAP_HEIGHT - 30 :, MAP_WIDTH - 120 :]
    assert np.count_nonzero(credit == DARK) > 0
    assert np.count_nonzero(credit == INK) > 0


def test_no_tiles_keeps_scale_and_says_map_not_installed() -> None:
    """Without tiles the scale box stays and the note boxes the credit."""
    grey = draw_map_base(
        base=base(),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    assert np.all(
        grey[MAP_HEIGHT - 16 : MAP_HEIGHT - 12, 12 : 12 + 125] == INK,
    )
    assert grey[MAP_HEIGHT - 18, 7] == DARK
    assert np.any(grey[MAP_HEIGHT - 30 :, MAP_WIDTH - 120 :] == INK)


def test_base_is_width_wide() -> None:
    """A base 480 px wide draws a MAP_HEIGHT by 480 map."""
    grey = draw_map_base(
        base=replace(base(), width=STRIP_MAP_WIDTH),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    assert grey.shape == (MAP_HEIGHT, STRIP_MAP_WIDTH)


def test_credit_sits_at_the_narrow_edge(tmp_path: Path) -> None:
    """At 480 px the credit box is 8 px in from 480, not from 520."""
    grey = grey_drawn(
        tmp_path=tmp_path,
        map_base=replace(base(), width=STRIP_MAP_WIDTH),
    )
    credit = grey[MAP_HEIGHT - 30 :]
    right = np.flatnonzero(np.any(credit == DARK, axis=0)).max()
    assert right == STRIP_MAP_WIDTH - 8


def test_stop_sits_on_the_narrow_centre() -> None:
    """At 480 px the stop is drawn at x 240."""
    grey = draw_map_base(
        base=replace(base(), width=STRIP_MAP_WIDTH),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    assert grey[centre_row(), STRIP_MAP_WIDTH // 2] == INK


def test_not_installed_note_sits_at_the_narrow_edge() -> None:
    """At 480 px the note's box is 8 px in from 480, not from 520."""
    missing = draw_map_base(
        base=replace(base(), width=STRIP_MAP_WIDTH),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    corner = missing[MAP_HEIGHT - 30 :]
    right = np.flatnonzero(np.any(corner == DARK, axis=0)).max()
    assert right == STRIP_MAP_WIDTH - 8


def test_no_tiles_hides_the_credit(tmp_path: Path) -> None:
    """Without a tiles file there is no OpenStreetMap credit."""
    missing = draw_map_base(
        base=base(),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        installed = draw_map_base(
            base=base(),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    corner = (slice(MAP_HEIGHT - 20, MAP_HEIGHT), slice(MAP_WIDTH - 120, None))
    assert not np.array_equal(missing[corner], installed[corner])


def horizontal(
    *,
    first: float,
    y: float = 2100.0,
    angle: float = 0.0,
) -> StreetLabel:
    """
    Make a 200 px east-west stretch in global pixels.

    Parameters
    ----------
    first : float
        Global x of the stretch's west end.
    y : float
        Global y of the stretch.
    angle : float
        The label's angle.

    Returns
    -------
    StreetLabel
        The label; its nine points are 25 px apart.
    """
    return StreetLabel(
        name='Hammers Road',
        x=first + 100.0,
        y=y,
        angle=angle,
        width=80.0,
        start=(first, y),
        end=(first + 200.0, y),
    )


def test_on_route_needs_six_of_nine_points() -> None:
    """Six points on the route match; five do not."""
    six = horizontal(first=1270.0)
    five = horizontal(first=1295.0)
    assert on_route(label=six, routes=ROUTE, offset=OFFSET)
    assert not on_route(label=five, routes=ROUTE, offset=OFFSET)


def test_on_route_reach_is_5_px() -> None:
    """A stretch 5 px beside the route matches; 5.5 px does not."""
    near = horizontal(first=1050.0, y=2105.0)
    far = horizontal(first=1050.0, y=2105.5)
    assert on_route(label=near, routes=ROUTE, offset=OFFSET)
    assert not on_route(label=far, routes=ROUTE, offset=OFFSET)


def test_on_route_turn_is_25_degrees() -> None:
    """An angle 25 degrees off matches; 26 degrees does not."""
    within = horizontal(first=1050.0, angle=25.0)
    beyond = horizontal(first=1050.0, angle=-26.0)
    assert on_route(label=within, routes=ROUTE, offset=OFFSET)
    assert not on_route(label=beyond, routes=ROUTE, offset=OFFSET)


def test_on_route_uses_the_nearest_segment_direction() -> None:
    """On a bent route each point is judged by its own segment."""
    bent = [
        np.array(
            [[0.0, 300.0], [0.0, 100.0], [400.0, 100.0], [400.0, 400.0]],
            dtype=np.float64,
        ),
    ]
    along = horizontal(first=1050.0)
    down = StreetLabel(
        name='Briens Road',
        x=1400.0,
        y=2250.0,
        angle=90.0,
        width=80.0,
        start=(1400.0, 2150.0),
        end=(1400.0, 2350.0),
    )
    assert on_route(label=along, routes=bent, offset=OFFSET)
    assert on_route(label=down, routes=bent, offset=OFFSET)


def test_on_route_keeps_a_label_straddling_a_turn() -> None:
    """Two thirds along the route before it turns is 6 of 9: redrawn."""
    turning = [
        np.array(
            [[0.0, 100.0], [400.0, 100.0], [400.0, 400.0]],
            dtype=np.float64,
        ),
    ]
    label = horizontal(first=1270.0)
    assert on_route(label=label, routes=turning, offset=OFFSET)


def test_names_reaching_in_from_above_are_redrawn(tmp_path: Path) -> None:
    """A long upright name centred 60 px above the map still shows."""
    gx, _ = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    _, top = window_of(base=base())
    x = CENTRE[0] + 40 * MPP
    upright = ((x, CENTRE[1] + 5000.0), (x, CENTRE[1] - 5000.0))
    label = street(
        name='Great Western Highway Service Road',
        x=gx + 40,
        y=top - 60.0,
        angle=90.0,
    )
    drawn = []
    for out, labels in (('named.db', [label]), ('plain.db', [])):
        with closing(build(out=tmp_path / out, labels=labels)) as tiles:
            drawn.append(
                draw_map_base(
                    base=base(route=(upright,)),
                    tiles=tiles,
                    variant=TileVariant.GREY,
                ),
            )
    named, plain = exactly_two(items=drawn)
    assert np.count_nonzero(named[:40] == DARK) > 0
    assert np.count_nonzero(plain[:40] == DARK) == 0


def test_bits_names_over_the_line_are_dithered(tmp_path: Path) -> None:
    """In BITS a redrawn name is dithered like a baked one, not solid."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    drawn = variants_drawn(
        tmp_path=tmp_path,
        label=label,
        map_base=base(route=across(half=5000.0)),
    )
    text = drawn[TileVariant.GREY] == DARK
    text[MAP_HEIGHT - CREDIT_ROOM - 1 :] = False
    share = np.count_nonzero(drawn[TileVariant.BITS][text] == PAPER) / (
        np.count_nonzero(text)
    )
    assert 0.05 <= share <= 0.5


def test_bits_names_dither_the_four_level_name(tmp_path: Path) -> None:
    """In BITS a redrawn name is the dithered copy of its grey name."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    drawn = variants_drawn(
        tmp_path=tmp_path,
        label=label,
        map_base=base(route=across(half=5000.0)),
    )
    region = name_region(label=label, offset=window_of(base=base()))
    paper = diffuse(grey=drawn[TileVariant.GREY][region])
    np.testing.assert_array_equal(
        drawn[TileVariant.BITS][region],
        np.where(paper, PAPER, INK),
    )


def test_redraw_name_off_the_map_changes_nothing() -> None:
    """A name wholly above the map leaves every pixel alone."""
    grey = np.full((MAP_HEIGHT, MAP_WIDTH), LIGHT, dtype=np.uint8)
    label = street(name='Church Street', x=1100.0, y=1900.0)
    redraw_name(grey=grey, label=label, offset=OFFSET)
    assert (grey == LIGHT).all()


def test_scale_bar_is_2_km_at_14_m_per_px(tmp_path: Path) -> None:
    """At 14 m/px the 2 km bar is 143 px long, 4 px in from its box."""
    grey = grey_drawn(tmp_path=tmp_path, map_base=base(mpp=14))
    scale = grey[MAP_HEIGHT - 16 : MAP_HEIGHT - 12, : 12 + 145]
    ink = np.flatnonzero(np.all(scale == INK, axis=0))
    assert ink.min() == 12
    assert 12 + 142 <= ink.max() + 1 <= 12 + 143


def test_to_disc_is_12_px(tmp_path: Path) -> None:
    """The To is an ink disc 12 px in radius edged with paper."""
    end = (CENTRE[0] + 800.0, CENTRE[1])
    trip = TripMarks(
        origin=None, second=(), walk=None, change=None, end=end,
    )
    grey = grey_drawn(tmp_path=tmp_path, map_base=replace(base(), trip=trip))
    x, y = end_screen(end=end)
    column, row = math.floor(x), math.floor(y)
    assert grey[row, column + 11] == INK
    assert grey[row, column - 11] == INK
    assert grey[row, column + 14] == PAPER


def test_from_marker_is_16_px(tmp_path: Path) -> None:
    """The From is a 16 px paper disc, a 4 px ring at 12 and a 5 px dot."""
    grey = grey_drawn(
        tmp_path=tmp_path,
        map_base=base(route=across(half=5000.0)),
    )
    x, y = MAP_WIDTH // 2, centre_row()
    assert pattern(values=grey[y, x - 20 : x + 21]) == (
        '####..####.....##########.....####..#####'
    )
    assert pattern(values=grey[y - 20 : y + 21, x]) == (
        '......####.....##########.....####.......'
    )
