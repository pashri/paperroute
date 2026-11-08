"""Tests for the values that fit the engine to a city."""

import math

from paperroute_transit.core.geo import Projection
from paperroute_transit.core.tiles import GRID_ORIGIN


def test_projection_matches_old_constants() -> None:
    """Sydney's projection gives today's numbers and origin text."""
    projection = Projection(
        origin_lon=151.2093, origin_lat=-33.8688, grid_lon=149.5,
        grid_lat=-32.0,
    )
    assert projection.metres_per_degree_lon == 111_320.0 * math.cos(
        math.radians(-33.8688),
    )
    assert projection.grid_origin == GRID_ORIGIN
    assert projection.origin_text == '151.2093,-33.8688'
