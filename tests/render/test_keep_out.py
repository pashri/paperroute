"""Facts about the boxes the overlay keeps its numbers out of."""

import math
from contextlib import closing
from pathlib import Path

from paperroute.adapters.tiles_db import NullTiles
from paperroute.build.map_style import STATION_FACE, SUBURB_FACE
from paperroute.build.tile_store import PlacedLabel
from paperroute.core.map_view import MapBase
from paperroute.core.tiles import (
    POINT_HALF_HEIGHT,
    STREET_HEIGHT,
    LabelKind,
    text_box,
    to_pixels,
)
from paperroute.render.canvas import Box
from paperroute.render.keep_out import NO_KEEP_OUT, KeepOut, keep_out
from paperroute.render.map_base import window_of
from paperroute.render.map_corners import credit_box, scale_box
from tests.render.test_map_base import CENTRE, MPP, base, build, street
from tests.render.test_map_labels import place_label


def grown(*, box: Box, by: float) -> Box:
    """
    Grow a box on every side.

    Parameters
    ----------
    box : Box
        Left, top, right, bottom.
    by : float
        Pixels added each side.

    Returns
    -------
    Box
        The grown box.
    """
    return box[0] - by, box[1] - by, box[2] + by, box[3] + by


def map_point(*, x: float, y: float, mpp: int = MPP) -> tuple[float, float]:
    """
    Move a global pixel onto the test map.

    Parameters
    ----------
    x : float
        Global pixel x.
    y : float
        Global pixel y.
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[float, float]
        Map pixel.
    """
    left, top = window_of(base=base(mpp=mpp))
    return x - left, y - top


def corners(*, mpp: int = MPP) -> tuple[Box, ...]:
    """
    Give both corner boxes with their dark edges, for installed tiles.

    Parameters
    ----------
    mpp : int
        Metres per pixel.

    Returns
    -------
    tuple[Box, ...]
        The scale box, then the credit box.
    """
    return (
        grown(box=scale_box(mpp=mpp), by=1.0),
        grown(box=credit_box(width=base().width), by=1.0),
    )


def test_station_name_and_square_boxes(tmp_path: Path) -> None:
    """A station keeps out its name on a 3 px halo and its 7 px square."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = place_label(
        kind=LabelKind.STATION, name='Westmead', at=(gx + 120.0, gy - 80.0),
    )
    with closing(build(out=tmp_path / 'tiles.db', labels=[label])) as tiles:
        boxes = keep_out(base=base(), tiles=tiles)
    name = text_box(
        centre=map_point(x=label.x, y=label.y),
        width=label.width,
        height=2 * POINT_HALF_HEIGHT * STATION_FACE.px,
        angle=0.0,
    )
    x, y = map_point(x=label.start[0], y=label.start[1])
    assert boxes.places == (
        grown(box=name, by=3.0),
        (x - 7.0, y - 7.0, x + 7.0, y + 7.0),
        *corners(),
    )
    assert boxes.streets == ()


def test_turned_street_box_bounds_the_text(tmp_path: Path) -> None:
    """A street name at 30 degrees keeps out its turned text's bounds."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = street(name='Darcy Road', x=gx + 60.0, y=gy - 60.0, angle=30.0)
    with closing(build(out=tmp_path / 'tiles.db', labels=[label])) as tiles:
        boxes = keep_out(base=base(), tiles=tiles)
    (box,) = boxes.streets
    x, y = map_point(x=label.x, y=label.y)
    turn = math.radians(30.0)
    for along, across in (
        (sign_x * label.width / 2, sign_y * STREET_HEIGHT / 2)
        for sign_x in (-1, 1)
        for sign_y in (-1, 1)
    ):
        corner_x = x + along * math.cos(turn) - across * math.sin(turn)
        corner_y = y + along * math.sin(turn) + across * math.cos(turn)
        assert box[0] + 2 - 1e-9 <= corner_x <= box[2] - 2 + 1e-9
        assert box[1] + 2 - 1e-9 <= corner_y <= box[3] - 2 + 1e-9
    assert box[2] - box[0] < label.width + STREET_HEIGHT + 4


def test_suburb_box_only_at_suburb_zooms(tmp_path: Path) -> None:
    """A suburb name keeps out a box at 8 m/px but not at 5 m/px."""
    labels = []
    for mpp in (5, 8):
        gx, gy = to_pixels(point=CENTRE, mpp=mpp)
        labels.append(PlacedLabel(
            zoom=mpp,
            kind=LabelKind.SUBURB,
            name='Westmead',
            x=gx - 120.0,
            y=gy - 80.0,
            angle=0.0,
            width=80.0,
            start=(gx - 120.0, gy - 80.0),
            end=(gx - 120.0, gy - 80.0),
        ))
    with closing(build(out=tmp_path / 'tiles.db', labels=labels)) as tiles:
        fine = keep_out(base=base(mpp=5), tiles=tiles)
        suburb = keep_out(base=base(mpp=8), tiles=tiles)
    name = text_box(
        centre=map_point(x=labels[1].x, y=labels[1].y, mpp=8),
        width=80.0,
        height=2 * POINT_HALF_HEIGHT * SUBURB_FACE.px,
        angle=0.0,
    )
    assert fine.places == corners(mpp=5)
    assert suburb.places == (grown(box=name, by=3.0), *corners(mpp=8))


def test_corner_boxes_included(tmp_path: Path) -> None:
    """With tiles installed, the scale and credit boxes are kept out."""
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        boxes = keep_out(base=base(), tiles=tiles)
    assert boxes == KeepOut(places=corners(), streets=())


def test_no_credit_box_without_tiles() -> None:
    """Without tiles only the scale box is kept out."""
    assert keep_out(base=base(), tiles=NullTiles()) == KeepOut(
        places=(grown(box=scale_box(mpp=MPP), by=1.0),), streets=(),
    )


def test_labels_off_the_map_are_left_out(tmp_path: Path) -> None:
    """A label fetched near the map but wholly off it keeps nothing out."""
    gx, gy = to_pixels(point=CENTRE, mpp=MPP)
    label = street(name='Far Road', x=gx + 600.0, y=gy)
    with closing(build(out=tmp_path / 'tiles.db', labels=[label])) as tiles:
        boxes = keep_out(base=base(), tiles=tiles)
    assert boxes.streets == ()


def test_no_centre_keeps_out_the_corners_only(tmp_path: Path) -> None:
    """A map with no location keeps out only its corner boxes."""
    with closing(build(out=tmp_path / 'tiles.db')) as tiles:
        boxes = keep_out(base=MapBase(centre=None, mpp=MPP, route=()), tiles=tiles)
    assert boxes == KeepOut(places=corners(), streets=())


def test_no_keep_out_is_empty() -> None:
    """The default keeps nothing out."""
    assert NO_KEEP_OUT == KeepOut(places=(), streets=())
