"""Tests for the Map's line and ring sizes."""

import numpy as np
import pytest
from numpy.typing import NDArray

from paperroute_device.render.canvas import INK, PAPER, Canvas
from paperroute_transit.core.geo import Point
from paperroute_transit.render.map_marks import (
    LINE_CASING,
    STOP_DISC,
    STOP_RING,
    dash_width,
    draw_capsule,
    draw_stop,
    ring_radius,
)

ZOOM_TABLE = [
    (3, 13.0, 6.5, 10.0, 6.75, 2.0),
    (5, 13.0, 6.5, 10.0, 6.75, 2.0),
    (8, 10.0, 5.0, 8.5, 6.0, 2.0),
    (14, 7.0, 3.5, 7.0, 5.25, 2.0),
    (25, 4.0, 2.0, 5.5, 4.5, 1.5),
    (45, 3.0, 1.5, 5.0, 4.25, 1.5),
]


@pytest.mark.parametrize(
    ('mpp', 'solid', 'dashes', 'solid_ring', 'dashed_ring', 'casing'),
    ZOOM_TABLE,
)
def test_line_sizes_match_the_spec_at_every_zoom(
    *,
    mpp: int,
    solid: float,
    dashes: float,
    solid_ring: float,
    dashed_ring: float,
    casing: float,
) -> None:
    """Dash width, ring radii and casing follow the spec's tables."""
    assert dash_width(mpp=mpp) == dashes
    assert ring_radius(mpp=mpp, solid=True) == solid_ring
    assert ring_radius(mpp=mpp, solid=False) == dashed_ring
    assert LINE_CASING[mpp] == casing
    assert dash_width(mpp=mpp) == solid / 2


def capsule_frame(*, ends: tuple[Point, Point]) -> NDArray[np.uint8]:
    """Draw a full-size capsule on a blank canvas."""
    canvas = Canvas()
    draw_capsule(canvas, ends=ends)
    return canvas.grey()


def test_draw_capsule_draws_a_dot_at_each_kerb() -> None:
    """Two kerbs far apart: an ink dot at each, paper between."""
    frame = capsule_frame(ends=((100.0, 100.0), (100.0, 160.0)))
    assert frame[100, 100] == INK and frame[160, 100] == INK
    assert frame[130, 100] == PAPER
    assert frame[130, 100 + int(STOP_RING)] == INK
    assert frame[130, 100 + int(STOP_DISC) + 1] == PAPER


def test_draw_capsule_with_kerbs_together_draws_one_dot() -> None:
    """Kerbs closer than two dots draw one dot between them."""
    frame = capsule_frame(ends=((100.0, 100.0), (100.0, 103.0)))
    assert frame[101, 100] == INK
    assert frame[95, 100] == PAPER and frame[107, 100] == PAPER


def test_draw_capsule_on_one_point_is_the_from_mark() -> None:
    """Both kerbs at one point draw the From mark, to a pixel."""
    capsule = capsule_frame(ends=((100.0, 100.0), (100.0, 100.0)))
    canvas = Canvas()
    draw_stop(canvas, at=(100.0, 100.0))
    assert (capsule != canvas.grey()).sum() <= 1
