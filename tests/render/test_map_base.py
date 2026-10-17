"""Facts about the drawn map base (no golden images)."""

import math
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute.adapters.tiles_db import NullTiles, SqliteTileRepository
from paperroute.build.map_style import DARK, LIGHT
from paperroute.build.tile_store import PlacedLabel, TileWriter
from paperroute.core.geo import Point, to_metres
from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MapBase,
    screen_point,
)
from paperroute.core.tiles import (
    TILE,
    LabelKind,
    StreetLabel,
    TileVariant,
    tile_of,
    to_pixels,
)
from paperroute.render.canvas import INK, PAPER
from paperroute.render.map_base import (
    compose_tiles,
    draw_map_base,
    on_route,
    scale_text,
    window_of,
)
from paperroute.render.tile_codec import pack_bits, pack_grey

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


def centre_row() -> int:
    """
    Find the map row the stop and an east-west route lie on.

    Returns
    -------
    int
        Map pixel row.
    """
    return math.floor(
        screen_point(centre=CENTRE, mpp=MPP, point=CENTRE)[1],
    )


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
    assert grey[y, x + 9] == INK
    assert grey[y, x - 9] == INK
    assert grey[y - 9, x] == INK
    assert grey[y + 9, x] == INK
    assert grey[y, x + 6] == PAPER
    assert grey[y, x + 11] == PAPER


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


def test_no_tiles_says_map_not_installed(tmp_path: Path) -> None:
    """Without a tiles file, text is drawn either side of the stop."""
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
    middle = MAP_HEIGHT // 2
    band = (slice(middle - 12, middle + 12), slice(150, 240))
    assert np.count_nonzero(missing[band] == INK) > 0
    assert np.count_nonzero(installed[band] == INK) == 0


def test_scale_bar_and_credit_are_drawn(tmp_path: Path) -> None:
    """Ink at the bottom left and the DARK credit at the bottom right."""
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        grey = draw_map_base(
            base=base(),
            tiles=tiles,
            variant=TileVariant.GREY,
        )
    bar = grey[MAP_HEIGHT - 13 : MAP_HEIGHT - 8, 14 : 14 + 125]
    assert np.all(np.any(bar == INK, axis=0))
    credit = grey[MAP_HEIGHT - 20 :, MAP_WIDTH - 120 :]
    assert np.count_nonzero(credit == DARK) > 0


@pytest.mark.parametrize(
    ('mpp', 'text'),
    [
        (3, '200 m · zoom 1 of 5'),
        (5, '500 m · zoom 2 of 5'),
        (8, '1 km · zoom 3 of 5'),
        (14, '1 km · zoom 4 of 5'),
        (45, '5 km · zoom 5 of 5'),
    ],
)
def test_scale_text_names_the_largest_fitting_step(
    mpp: int,
    text: str,
) -> None:
    """The bar is the largest step within 140 px, then the zoom."""
    assert scale_text(mpp=mpp) == text
