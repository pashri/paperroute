"""Facts about the drawn map base (no golden images)."""

import math
import re
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.adapters.tiles_db import NullTiles, SqliteTileRepository
from paperroute.build.features import FeatureStore
from paperroute.build.map_style import DARK, LIGHT, STREET_FACE, SUPERSAMPLE
from paperroute.build.tile_draw import Window as DrawWindow
from paperroute.build.tile_draw import draw_window
from paperroute.build.tile_store import PlacedLabel, TileWriter
from paperroute.core.geo import Point, to_metres
from paperroute.core.map_fit import CREDIT_ROOM
from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    STRIP_MAP_WIDTH,
    MapBase,
    TripMarks,
    screen_point,
)
from paperroute.core.tiles import (
    TILE,
    LabelKind,
    StreetLabel,
    TileVariant,
    tile_of,
    tiles_covering,
    to_pixels,
)
from paperroute.render.canvas import INK, PAPER, Canvas, Face, text_width
from paperroute.render.map_base import (
    HALO,
    compose_tiles,
    draw_map_base,
    name_region,
    on_route,
    redraw_name,
    window_of,
)
from paperroute.render.map_corners import BOX_FACE
from paperroute.render.map_marks import ROUTE_STYLE
from paperroute.render.tile_codec import (
    diffuse,
    pack_bits,
    pack_grey,
    quantise4,
)

MPP = 8
CENTRE: Point = to_metres(lon=151.0036, lat=-33.8170)
OFFSET = (1000, 2000)
ROUTE: list[NDArray[np.float64]] = [
    np.array([[0.0, 100.0], [400.0, 100.0]], dtype=np.float64),
]
LABEL_SHIFT = 100.0  # map pixels right of the stop


def base(
    *,
    route: tuple[tuple[Point, ...], ...] = (),
    mpp: int = MPP,
) -> MapBase:
    """
    Make a map base centred on the test stop.

    Parameters
    ----------
    route : tuple[tuple[Point, ...], ...]
        Route lines in metres.
    mpp : int
        Metres per pixel.

    Returns
    -------
    MapBase
        The base.
    """
    return MapBase(centre=CENTRE, mpp=mpp, route=route)


def across(*, half: float) -> tuple[tuple[Point, ...], ...]:
    """
    Make a straight east-west route through the stop.

    Parameters
    ----------
    half : float
        Metres either side of the stop.

    Returns
    -------
    tuple[tuple[Point, ...], ...]
        One route line.
    """
    x, y = CENTRE
    return (((x - half, y), (x + half, y)),)


def street(
    *,
    name: str,
    x: float,
    y: float,
    angle: float = 0.0,
) -> PlacedLabel:
    """
    Make a street label in global pixels at the test zoom.

    Parameters
    ----------
    name : str
        Street name.
    x : float
        Anchor x.
    y : float
        Anchor y.
    angle : float
        Text rotation.

    Returns
    -------
    PlacedLabel
        The label, with a stretch 120 px long along its angle.
    """
    dx = 60.0 * math.cos(math.radians(angle))
    dy = 60.0 * math.sin(math.radians(angle))
    return PlacedLabel(
        zoom=MPP,
        kind=LabelKind.STREET,
        name=name,
        x=x,
        y=y,
        angle=angle,
        width=70.0,
        start=(x - dx, y - dy),
        end=(x + dx, y + dy),
    )


def build(
    *,
    out: Path,
    tiles: dict[tuple[int, int], NDArray[np.uint8]] | None = None,
    labels: Sequence[PlacedLabel] = (),
) -> SqliteTileRepository:
    """
    Write a tiny tiles file and open it.

    Parameters
    ----------
    out : Path
        Destination file.
    tiles : dict[tuple[int, int], NDArray[np.uint8]] | None
        Grey tiles by column and row at the test zoom.
    labels : Sequence[PlacedLabel]
        Labels to store.

    Returns
    -------
    SqliteTileRepository
        The opened file.
    """
    with TileWriter(out=out) as writer:
        for (col, row), grey in (tiles or {}).items():
            writer.add_tile(
                zoom=MPP,
                col=col,
                row=row,
                grey=pack_grey(grey=grey),
                bits=pack_bits(paper=grey >= 128),
            )
        writer.add_labels(labels=labels)
        writer.finish(meta={})
    return SqliteTileRepository(path=out)


def stop_tile() -> tuple[int, int]:
    """
    Find the tile holding the stop at the test zoom.

    Returns
    -------
    tuple[int, int]
        Column and row.
    """
    return tile_of(pixel=to_pixels(point=CENTRE, mpp=MPP))


def centre_row(*, mpp: int = MPP) -> int:
    """
    Find the map row the stop and an east-west route lie on.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    int
        Map pixel row.
    """
    return math.floor(
        screen_point(centre=CENTRE, mpp=mpp, point=CENTRE)[1],
    )


def pattern(*, values: NDArray[np.uint8]) -> str:
    """
    Spell a run of pixels as ink and paper.

    Parameters
    ----------
    values : NDArray[np.uint8]
        Pixels in a row or column.

    Returns
    -------
    str
        ``#`` for ink and ``.`` for anything else.
    """
    return ''.join('#' if value == INK else '.' for value in values)


def test_window_of_matches_screen_point() -> None:
    """The stop's global pixel minus the window is its map pixel."""
    left, top = window_of(base=base())
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    sx, sy = screen_point(centre=CENTRE, mpp=MPP, point=CENTRE)
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
    gx, gy = to_pixels(point=point, mpp=MPP)
    col, row = tile_of(pixel=(gx, gy))
    dot = np.full((TILE, TILE), PAPER, dtype=np.uint8)
    dot[math.floor(gy) - row * TILE, math.floor(gx) - col * TILE] = INK
    tiles = build(out=tmp_path / 'tiles.db', tiles={(col, row): dot})
    sx, sy = screen_point(centre=CENTRE, mpp=MPP, point=point)
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
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
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
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(route=across(half=5000.0)),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    row = centre_row()
    band = grey[row - 3 : row + 4]
    assert np.all(np.any(band[:, 20:240] == INK, axis=0))
    assert np.all(np.any(band[:, 280:500] == INK, axis=0))
    assert np.all(grey[row - 20, 20:240] == PAPER)


def test_route_wears_paper_beads_at_street_zooms(tmp_path: Path) -> None:
    """At 8 m/px the stroke's middle row holds paper beads."""
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(route=across(half=5000.0)),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    middle = grey[centre_row(), 300:500]
    assert np.count_nonzero(middle == PAPER) > 0
    assert np.count_nonzero(middle == INK) > 0


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


def draw_with(
    *,
    out: Path,
    labels: Sequence[PlacedLabel],
) -> NDArray[np.uint8]:
    """
    Draw the base with an east-west route over a tiny tiles file.

    Parameters
    ----------
    out : Path
        Tiles file to write.
    labels : Sequence[PlacedLabel]
        Labels to store.

    Returns
    -------
    NDArray[np.uint8]
        The drawn base.
    """
    with closing(build(out=out, labels=labels)) as tiles:
        return draw_map_base(
            base=base(route=across(half=5000.0)),
            tiles=tiles,
            variant=TileVariant.GREY,
        )


def test_names_over_the_line_are_redrawn(tmp_path: Path) -> None:
    """A street along the route is redrawn over the stroke."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    named = draw_with(out=tmp_path / 'named.db', labels=[label])
    plain = draw_with(out=tmp_path / 'plain.db', labels=[])
    row = centre_row()
    x = math.floor(MAP_WIDTH / 2 + LABEL_SHIFT)
    box = (slice(row - 3, row + 4), slice(x - 30, x + 31))
    assert np.count_nonzero(named[box] == DARK) > 0
    assert np.count_nonzero(plain[box] == DARK) == 0
    assert np.count_nonzero(named[box] == PAPER) > np.count_nonzero(
        plain[box] == PAPER,
    )
    assert set(np.unique(plain[box]).tolist()) == {INK, PAPER}


def test_route_street_names_redrawn_in_ink(tmp_path: Path) -> None:
    """A name redrawn along the route is ink letters over the stroke."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    named = draw_with(out=tmp_path / 'named.db', labels=[label])
    plain = draw_with(out=tmp_path / 'plain.db', labels=[])
    row = centre_row()
    x = math.floor(MAP_WIDTH / 2 + LABEL_SHIFT)
    edge = (slice(row - 5, row - 2), slice(x - 30, x + 31))
    assert (plain[edge] == INK).all()
    assert np.count_nonzero(named[edge] == INK) > 0
    assert np.count_nonzero(named[edge] == PAPER) > 0


def smooth_name(
    *,
    under: NDArray[np.uint8],
    label: PlacedLabel,
    offset: tuple[int, int],
) -> NDArray[np.uint8]:
    """
    Redraw a street name over a copy of map pixels.

    Parameters
    ----------
    under : NDArray[np.uint8]
        The map without the name.
    label : PlacedLabel
        The label, in global pixels.
    offset : tuple[int, int]
        The map's top-left corner in global pixels.

    Returns
    -------
    NDArray[np.uint8]
        The pixels of the name's region.
    """
    grey = under.copy()
    redraw_name(grey=grey, label=label, offset=offset)
    return grey[name_region(label=label, offset=offset)]


def test_redrawn_name_matches_the_baked_name(tmp_path: Path) -> None:
    """A name redrawn over paper equals the same label baked in a tile."""
    label = street(name='Church Street', x=1203.3, y=2140.6, angle=11.0)
    store = FeatureStore(path=tmp_path / 'features.db')
    with closing(store.conn):
        store.add_labels(labels=[label])
        baked = draw_window(
            store=store,
            window=DrawWindow(
                mpp=MPP,
                left=OFFSET[0],
                top=OFFSET[1],
                width=TILE,
                height=TILE,
            ),
        )
    redrawn = np.full((TILE, TILE), PAPER, dtype=np.uint8)
    redraw_name(grey=redrawn, label=label, offset=OFFSET)
    assert (baked == LIGHT).any()
    np.testing.assert_array_equal(redrawn, baked)


@pytest.mark.parametrize('angle', [0.0, 17.0, 45.0, 90.0])
@pytest.mark.parametrize(
    'name',
    ['Church Street', 'Jaguar Way', 'Great Western Highway Service Road'],
)
def test_name_region_holds_the_whole_smoothed_name(
    name: str,
    angle: float,
) -> None:
    """Every pixel a 4x name and its halo shade lies inside its region."""
    for shift in (0.0, 0.3, 0.6):
        label = street(name=name, x=300.0 + shift, y=300.7 - shift)
        label = replace(label, angle=angle)
        canvas = Canvas(width=600, height=600, scale=SUPERSAMPLE, smooth=True)
        canvas.rect(box=(0, 0, 600, 600), grey=LIGHT)
        canvas.text(
            at=(label.x, label.y),
            text=name,
            face=STREET_FACE,
            grey=DARK,
            anchor='mm',
            halo=HALO,
            angle=angle,
        )
        shaded = canvas.grey() != LIGHT
        shaded[name_region(label=label, offset=(0, 0))] = False
        assert not shaded.any()


@pytest.mark.parametrize('angle', [0.0, 3.0])
def test_redrawn_names_are_smoothed_like_baked_ones(
    tmp_path: Path,
    angle: float,
) -> None:
    """A redrawn name is drawn at 4x, averaged and snapped to 4 levels."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = street(
        name='Church Street',
        x=gx + LABEL_SHIFT + 0.3,
        y=gy + 0.6,
        angle=angle,
    )
    named = draw_with(out=tmp_path / 'named.db', labels=[label])
    plain = draw_with(out=tmp_path / 'plain.db', labels=[])
    offset = window_of(base=base())
    region = name_region(label=label, offset=offset)
    np.testing.assert_array_equal(
        named[region],
        smooth_name(under=plain, label=label, offset=offset),
    )
    assert np.count_nonzero(named[region] == LIGHT) > 0
    outside = np.ones(named.shape, dtype=bool)
    outside[region] = False
    np.testing.assert_array_equal(named[outside], plain[outside])


def test_crossing_names_are_not_redrawn(tmp_path: Path) -> None:
    """A street crossing the route is left under the line."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = street(name='George St', x=gx + LABEL_SHIFT, y=gy, angle=90.0)
    named = draw_with(out=tmp_path / 'named.db', labels=[label])
    plain = draw_with(out=tmp_path / 'plain.db', labels=[])
    assert np.array_equal(named, plain)


def test_base_has_four_levels(tmp_path: Path) -> None:
    """The grey base holds only the four panel levels."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
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
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
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
        base=MapBase(centre=None, mpp=MPP, route=()),
        tiles=NullTiles(),
        variant=TileVariant.GREY,
    )
    assert grey.shape == (MAP_HEIGHT, MAP_WIDTH)
    assert grey.dtype == np.uint8
    assert np.all(grey == PAPER)


def test_window_of_needs_a_centre() -> None:
    """A base with no centre has no window."""
    with pytest.raises(ValueError, match='centre'):
        window_of(base=MapBase(centre=None, mpp=MPP, route=()))


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
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    bar = grey[MAP_HEIGHT - 16 : MAP_HEIGHT - 12, 12 : 12 + 125]
    assert np.all(bar == INK)
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
    assert np.all(grey[MAP_HEIGHT - 16 : MAP_HEIGHT - 12, 12 : 12 + 125] == INK)
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
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=replace(base(), width=STRIP_MAP_WIDTH),
            tiles=tiles,
            variant=TileVariant.GREY,
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


@pytest.mark.parametrize(
    ('mpp', 'outer', 'bead'),
    [
        (3, 11, 5),
        (5, 11, 5),
        (8, 10, 4),
        (14, 8, 2),
        (25, 7, 0),
        (45, 6, 0),
    ],
)
def test_route_style_at_every_zoom(
    tmp_path: Path,
    mpp: int,
    outer: int,
    bead: int,
) -> None:
    """The stroke is ``outer`` rows deep, with beads ``bead`` rows deep."""
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(route=across(half=50_000.0), mpp=mpp),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    row = centre_row(mpp=mpp)
    band = grey[row - 8 : row + 9, 20:240]
    stroke = band[np.any(band == INK, axis=1)]
    assert len(stroke) == outer
    assert int(np.max(np.sum(stroke == PAPER, axis=0))) == bead


def test_beads_are_2_px_dashes_7_px_apart(tmp_path: Path) -> None:
    """Along the stroke's middle, paper runs of 2 alternate with ink 7."""
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(route=across(half=5000.0)),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    middle = pattern(values=grey[centre_row(), 20:240])
    runs = re.findall(r'#+|\.+', middle)
    inner = runs[1:-1]
    assert {len(run) for run in inner if run[0] == '.'} == {2}
    assert {len(run) for run in inner if run[0] == '#'} == {7}


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
    gx, _ = to_pixels(point=CENTRE, mpp=MPP)
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
    named, plain = drawn
    assert np.count_nonzero(named[:40] == DARK) > 0
    assert np.count_nonzero(plain[:40] == DARK) == 0


def test_bits_names_over_the_line_are_dithered(tmp_path: Path) -> None:
    """In BITS a redrawn name is dithered like a baked one, not solid."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    drawn = {}
    for variant in TileVariant:
        with closing(
            build(out=tmp_path / f'{variant}.db', labels=[label]),
        ) as tiles:
            drawn[variant] = draw_map_base(
                base=base(route=across(half=5000.0)),
                tiles=tiles,
                variant=variant,
            )
    text = drawn[TileVariant.GREY] == DARK
    text[MAP_HEIGHT - CREDIT_ROOM - 1 :] = False
    share = np.count_nonzero(drawn[TileVariant.BITS][text] == PAPER) / (
        np.count_nonzero(text)
    )
    assert 0.05 <= share <= 0.5


def test_bits_names_dither_the_four_level_name(tmp_path: Path) -> None:
    """In BITS a redrawn name is the dithered copy of its grey name."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = street(name='Church Street', x=gx + LABEL_SHIFT, y=gy)
    drawn = {}
    for variant in TileVariant:
        with closing(
            build(out=tmp_path / f'{variant}.db', labels=[label]),
        ) as tiles:
            drawn[variant] = draw_map_base(
                base=base(route=across(half=5000.0)),
                tiles=tiles,
                variant=variant,
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
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(mpp=14),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    bar = grey[MAP_HEIGHT - 16 : MAP_HEIGHT - 12, : 12 + 145]
    ink = np.flatnonzero(np.all(bar == INK, axis=0))
    assert ink.min() == 12
    assert 12 + 142 <= ink.max() + 1 <= 12 + 143


def test_ridden_stops_are_rings(tmp_path: Path) -> None:
    """A ridden stop is a 2 px ink ring round a paper centre."""
    stop = (CENTRE[0] + 800.0, CENTRE[1] + 400.0)
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=replace(base(), stops=(stop,)),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    x, y = screen_point(centre=CENTRE, mpp=MPP, point=stop)
    column, row = math.floor(x), math.floor(y)
    assert grey[row, column] == PAPER
    assert grey[row, column + 3] == PAPER
    assert grey[row, column + 6] == INK
    assert grey[row, column - 6] == INK
    assert grey[row + 6, column] == INK
    assert grey[row, column + 9] == PAPER


@pytest.mark.parametrize('mpp', [3, 5, 8])
def test_ridden_stop_ring_shows_outside_the_route(
    tmp_path: Path, mpp: int,
) -> None:
    """A stop on the bold route rings it 2 px outside the line."""
    stop = (CENTRE[0] + 120.0 * mpp, CENTRE[1])
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=replace(
                base(route=across(half=5000.0), mpp=mpp), stops=(stop,),
            ),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    x, y = screen_point(centre=CENTRE, mpp=mpp, point=stop)
    column, row = math.floor(x), math.floor(y)
    reach = math.floor(ROUTE_STYLE[mpp][0] / 2) + 1
    assert grey[row - reach, column] == INK
    assert grey[row + reach, column] == INK
    assert grey[row, column] == PAPER
    assert grey[row + 2, column] == PAPER


def test_to_disc_is_12_px(tmp_path: Path) -> None:
    """The To is an ink disc 12 px in radius edged with paper."""
    end = (CENTRE[0] + 800.0, CENTRE[1])
    trip = TripMarks(
        origin=None, second=(), walk=None, change=None, end=end,
    )
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=replace(base(), trip=trip),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    x, y = screen_point(centre=CENTRE, mpp=MPP, point=end)
    column, row = math.floor(x), math.floor(y)
    assert grey[row, column + 11] == INK
    assert grey[row, column - 11] == INK
    assert grey[row, column + 14] == PAPER


def test_from_marker_is_16_px(tmp_path: Path) -> None:
    """The From is a 16 px paper disc, a 4 px ring at 12 and a 5 px dot."""
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(route=across(half=5000.0)),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    x, y = MAP_WIDTH // 2, centre_row()
    assert pattern(values=grey[y, x - 20 : x + 21]) == (
        '####..####.....##########.....####..#####'
    )
    assert pattern(values=grey[y - 20 : y + 21, x]) == (
        '......####.....##########.....####.......'
    )
