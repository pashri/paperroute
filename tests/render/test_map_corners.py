"""Facts about the map's boxed scale and credit (no golden images)."""

import pytest

from paperroute.build.map_style import DARK, LIGHT
from paperroute.core.map_fit import CREDIT_ROOM
from paperroute.core.map_view import MAP_HEIGHT, MAP_WIDTH, STRIP_MAP_WIDTH
from paperroute.render.map_corners import (
    credit_box,
    draw_boxed_credit,
    draw_boxed_scale,
    scale_box,
    scale_text,
)
from paperroute_device.render.canvas import INK, PAPER, Canvas


def ground(*, width: int) -> Canvas:
    """
    Make a map-sized canvas of light grey.

    Parameters
    ----------
    width : int
        Map width in pixels.

    Returns
    -------
    Canvas
        The canvas.
    """
    canvas = Canvas(width=width, height=MAP_HEIGHT)
    canvas.rect(box=(0, 0, width, MAP_HEIGHT), grey=LIGHT)
    return canvas


@pytest.mark.parametrize(
    ('mpp', 'text'),
    [(3, '200 m'), (5, '500 m'), (8, '1 km'), (14, '2 km'), (25, '2 km'),
     (45, '5 km')],
)
def test_scale_text_is_distance_only(mpp: int, text: str) -> None:
    """The scale names its length alone."""
    assert scale_text(mpp=mpp) == text


def test_scale_box_inset_8_px_with_dark_edge() -> None:
    """The scale box is paper 8 px in from the corner, edged in dark."""
    canvas = ground(width=MAP_WIDTH)
    draw_boxed_scale(canvas, mpp=8)
    grey = canvas.grey()
    left, top, right, bottom = scale_box(mpp=8)
    assert (left, bottom) == (8, MAP_HEIGHT - 8)
    middle = int(top + bottom) // 2
    assert grey[middle, 7] == DARK
    assert grey[middle, 6] == LIGHT
    assert grey[middle, 8] == PAPER
    assert grey[MAP_HEIGHT - 8, 40] == DARK
    assert grey[int(top) - 1, 40] == DARK
    assert grey[MAP_HEIGHT - 9, 40] == PAPER
    assert grey[MAP_HEIGHT - 14, 12 + 125 // 2] == INK
    assert grey[int(top) + 1, 12 + 125 // 2] == PAPER


@pytest.mark.parametrize('width', [MAP_WIDTH, STRIP_MAP_WIDTH])
def test_credit_box_inset_8_px_right(width: int) -> None:
    """The credit box is paper 8 px in from the right corner."""
    canvas = ground(width=width)
    draw_boxed_credit(canvas, width=width)
    grey = canvas.grey()
    left, top, right, bottom = credit_box(width=width)
    assert (right, bottom) == (width - 8, MAP_HEIGHT - 8)
    middle = int(top + bottom) // 2
    assert grey[middle, width - 8] == DARK
    assert grey[middle, width - 7] == LIGHT
    assert grey[middle, width - 9] == PAPER
    inside = grey[int(top) : int(bottom), int(left) + 1 : width - 9]
    assert (inside == INK).any()


def test_credit_room_matches_the_boxes() -> None:
    """The fit keeps clear exactly the boxes' height above the bottom."""
    assert MAP_HEIGHT - scale_box(mpp=14)[1] == CREDIT_ROOM
    assert MAP_HEIGHT - credit_box(width=MAP_WIDTH)[1] == CREDIT_ROOM
