"""Facts about place names knocked out of the map (no golden images)."""

import math
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np

from paperroute.build.map_style import DARK
from paperroute.city import SYDNEY_PROJECTION
from paperroute.render.map_base import draw_map_base, window_of
from paperroute_device.render.canvas import INK, PAPER
from paperroute_transit.adapters.tiles_db import SqliteTileRepository
from paperroute_transit.build.tile_store import PlacedLabel
from paperroute_transit.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    TripMarks,
    screen_point,
)
from paperroute_transit.core.tiles import (
    TILE,
    LabelKind,
    TileVariant,
    tiles_covering,
    to_pixels,
)
from tests.render.test_map_base import (
    CENTRE,
    MPP,
    across,
    base,
    build,
    centre_row,
)


def place_label(
    *, kind: LabelKind, name: str, at: tuple[float, float],
) -> PlacedLabel:
    """
    Make a place label in global pixels at the test zoom.

    Parameters
    ----------
    kind : LabelKind
        Suburb or station.
    name : str
        The name.
    at : tuple[float, float]
        Where the name is centred; a station's square is 20 px below.

    Returns
    -------
    PlacedLabel
        The label.
    """
    x, y = at
    return PlacedLabel(
        zoom=MPP,
        kind=kind,
        name=name,
        x=x,
        y=y,
        angle=0.0,
        width=80.0,
        start=(x, y + 20.0),
        end=(x, y + 20.0),
    )


def test_route_stays_whole_through_a_station_name(tmp_path: Path) -> None:
    """A station name under the route loses the letters the line covers."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = place_label(
        kind=LabelKind.STATION, name='Westmead', at=(gx + 120.0, gy),
    )
    drawn = []
    for name, labels in (('named.db', [label]), ('plain.db', [])):
        with closing(build(out=tmp_path / name, labels=labels)) as tiles:
            drawn.append(draw_map_base(
                base=base(route=across(half=5000.0)),
                tiles=tiles,
                variant=TileVariant.GREY,
            ))
    named, plain = drawn
    row = centre_row()
    x = math.floor(MAP_WIDTH / 2 + 120.0)
    stroke = (slice(row - 5, row + 5), slice(x - 40, x + 41))
    assert (named[stroke] == plain[stroke]).all()
    assert (named[stroke] == INK).mean() > 0.5
    outside = (slice(row - 9, row - 5), slice(x - 40, x + 41))
    assert (named[outside] == INK).any()
    assert (plain[outside] == PAPER).all()


def test_route_stays_whole_through_the_to_station_name(
    tmp_path: Path,
) -> None:
    """The To's station name is not drawn again over the route."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = place_label(
        kind=LabelKind.STATION, name='Westmead', at=(gx + 120.0, gy),
    )
    end = (CENTRE[0] + 120.0 * MPP, CENTRE[1] - 20.0 * MPP)
    trip = TripMarks(
        origin=None, second=(), walk=None, change=None, end=end,
    )
    drawn = []
    for name, labels in (('named.db', [label]), ('plain.db', [])):
        with closing(build(out=tmp_path / name, labels=labels)) as tiles:
            drawn.append(draw_map_base(
                base=replace(base(route=across(half=5000.0)), trip=trip),
                tiles=tiles,
                variant=TileVariant.GREY,
            ))
    named, plain = drawn
    row = centre_row()
    x = math.floor(MAP_WIDTH / 2 + 120.0)
    stroke = (slice(row - 4, row + 4), slice(x - 40, x + 41))
    assert (named[stroke] == plain[stroke]).all()
    assert (named[stroke] == INK).mean() > 0.5


def test_station_square_is_ink_in_paper(tmp_path: Path) -> None:
    """A station's square is 10 px of ink on a 14 px paper square."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = place_label(
        kind=LabelKind.STATION, name='Westmead', at=(gx + 120.0, gy - 80.0),
    )
    with closing(inked(out=tmp_path / 'tiles.db', labels=[label])) as tiles:
        grey = draw_map_base(
            base=base(), tiles=tiles, variant=TileVariant.GREY,
        )
    x, y = screen_point(
        centre=CENTRE,
        mpp=MPP,
        point=(CENTRE[0] + 120.0 * MPP, CENTRE[1] + 60.0 * MPP),
        projection=SYDNEY_PROJECTION,
    )
    column, row = math.floor(x), math.floor(y)
    assert grey[row, column] == INK
    assert grey[row, column + 4] == INK
    assert grey[row, column + 6] == PAPER
    assert grey[row, column + 8] == INK


def inked(
    *, out: Path, labels: list[PlacedLabel],
) -> SqliteTileRepository:
    """
    Write a tiles file whose map is all ink, with some labels.

    Parameters
    ----------
    out : Path
        Destination file.
    labels : list[PlacedLabel]
        Labels to store.

    Returns
    -------
    SqliteTileRepository
        The opened file.
    """
    left, top = window_of(base=base())
    ink = np.full((TILE, TILE), INK, dtype=np.uint8)
    covering = tiles_covering(
        left=left, top=top, right=left + MAP_WIDTH, bottom=top + MAP_HEIGHT,
    )
    return build(out=out, tiles={tile: ink for tile in covering}, labels=labels)


def test_suburb_name_knocked_out_off_the_route(tmp_path: Path) -> None:
    """Off the route, a suburb name is dark grey on a 3 px paper halo."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = place_label(
        kind=LabelKind.SUBURB, name='Westmead', at=(gx - 120.0, gy - 80.0),
    )
    with closing(inked(out=tmp_path / 'tiles.db', labels=[label])) as tiles:
        grey = draw_map_base(
            base=base(), tiles=tiles, variant=TileVariant.GREY,
        )
    x = math.floor(MAP_WIDTH / 2 - 120.0)
    y = centre_row() - 80
    name = grey[y - 14 : y + 15, x - 50 : x + 51]
    text = np.argwhere(name == DARK)
    paper = np.argwhere(name == PAPER)
    assert len(text) > 0
    reach = np.min(
        np.linalg.norm(paper[:, None, :] - text[None, :, :], axis=2),
        axis=1,
    )
    assert 3.0 <= float(np.max(reach)) < 4.5


def test_bits_suburb_name_is_dithered(tmp_path: Path) -> None:
    """In BITS a knocked-out dark grey suburb name is dithered, not ink."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP, projection=SYDNEY_PROJECTION)
    label = place_label(
        kind=LabelKind.SUBURB, name='Westmead', at=(gx - 120.0, gy - 80.0),
    )
    drawn = {}
    for variant in TileVariant:
        with closing(
            build(out=tmp_path / f'{variant}.db', labels=[label]),
        ) as tiles:
            drawn[variant] = draw_map_base(
                base=base(), tiles=tiles, variant=variant,
            )
    text = drawn[TileVariant.GREY] == DARK
    assert np.count_nonzero(text) > 0
    share = np.count_nonzero(drawn[TileVariant.BITS][text] == PAPER) / (
        np.count_nonzero(text)
    )
    assert 0.05 <= share <= 0.5


def test_bits_corner_box_edges_are_dithered(tmp_path: Path) -> None:
    """In BITS the boxes' 1 px dark grey edges are dithered, not ink."""
    drawn = {}
    for variant in TileVariant:
        with closing(build(out=tmp_path / f'{variant}.db')) as tiles:
            drawn[variant] = draw_map_base(
                base=base(), tiles=tiles, variant=variant,
            )
    edge = drawn[TileVariant.GREY] == DARK
    assert np.count_nonzero(edge) > 0
    share = np.count_nonzero(drawn[TileVariant.BITS][edge] == PAPER) / (
        np.count_nonzero(edge)
    )
    assert 0.05 <= share <= 0.6
