"""Shared helpers for the map base tests."""

import math
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK
from paperroute_sydney.city import SYDNEY_PROJECTION
from paperroute_transit.adapters.tiles_db import SqliteTileRepository
from paperroute_transit.build.tile_store import PlacedLabel, TileWriter
from paperroute_transit.core.geo import Point, to_metres
from paperroute_transit.core.map_view import MapBase, screen_point
from paperroute_transit.core.tiles import (
    LabelKind,
    TileVariant,
    tile_of,
    to_pixels,
)
from paperroute_transit.render.map_base import draw_map_base
from paperroute_transit.render.tile_codec import pack_bits, pack_grey

MPP = 8


CENTRE: Point = to_metres(
    lon=151.0036,
    lat=-33.8170,
    projection=SYDNEY_PROJECTION,
)


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
    return MapBase(
        centre=CENTRE,
        mpp=mpp,
        route=route,
        projection=SYDNEY_PROJECTION,
    )


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
    return tile_of(pixel=to_pixels(
        point=CENTRE,
        mpp=MPP,
        projection=SYDNEY_PROJECTION,
    ))


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
        screen_point(
            centre=CENTRE,
            mpp=mpp,
            point=CENTRE,
            projection=SYDNEY_PROJECTION,
        )[1],
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


def grey_drawn(*, tmp_path: Path, map_base: MapBase) -> NDArray[np.uint8]:
    """
    Draw a map base over the built tiles in the grey variant.

    Parameters
    ----------
    tmp_path : Path
        Folder to build the tiles file in.
    base : MapBase
        The base to draw.

    Returns
    -------
    NDArray[np.uint8]
        The grey map.
    """
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        return draw_map_base(
            base=map_base,
            tiles=tiles,
            variant=TileVariant.GREY,
        )


def end_screen(*, end: Point) -> Point:
    """
    Give the screen position of a metre point on the map around CENTRE.

    Parameters
    ----------
    end : Point
        The point, in metres.

    Returns
    -------
    Point
        Its screen position.
    """
    return screen_point(
        centre=CENTRE, mpp=MPP, point=end, projection=SYDNEY_PROJECTION,
    )


def no_centre_base() -> MapBase:
    """
    Make a map base with no centre.

    Returns
    -------
    MapBase
        The base.
    """
    return MapBase(
        centre=None, mpp=MPP, route=(), projection=SYDNEY_PROJECTION,
    )


def variants_drawn(
    *, tmp_path: Path, label: PlacedLabel, map_base: MapBase,
) -> dict[TileVariant, NDArray[np.uint8]]:
    """
    Draw the map base over a tile holding one label in every variant.

    Parameters
    ----------
    tmp_path : Path
        Working directory for the tile files.
    label : PlacedLabel
        The label baked into the tile.
    map_base : MapBase
        The base to draw.

    Returns
    -------
    dict[TileVariant, NDArray[np.uint8]]
        The frame of each variant.
    """
    drawn = {}
    for variant in TileVariant:
        with closing(
            build(out=tmp_path / f'{variant}.db', labels=[label]),
        ) as tiles:
            drawn[variant] = draw_map_base(
                base=map_base, tiles=tiles, variant=variant,
            )
    return drawn
