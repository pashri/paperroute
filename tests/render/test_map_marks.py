"""Tests for the Map's line and ring sizes."""

import pytest

from paperroute_transit.render.map_marks import (
    LINE_CASING,
    dash_width,
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
