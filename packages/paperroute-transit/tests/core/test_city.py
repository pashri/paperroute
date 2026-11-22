"""Tests for the values that fit the engine to a city."""

import math
from dataclasses import fields

from paperroute_transit.core.city import City
from paperroute_transit.core.geo import Projection, to_metres
from paperroute_transit.core.names import (
    FACILITY_WORDS,
    STREET_WORDS,
    NameRules,
)


def test_projection_matches_old_constants() -> None:
    """Sydney's projection gives today's numbers and origin text."""
    projection = Projection(
        origin_lon=151.2093, origin_lat=-33.8688, grid_lon=149.5,
        grid_lat=-32.0,
    )
    assert projection.metres_per_degree_lon == 111_320.0 * math.cos(
        math.radians(-33.8688),
    )
    assert projection.metres_per_degree_lon == 92430.76354589846
    assert projection.grid_origin == to_metres(
        lon=149.5, lat=-32.0, projection=projection,
    )
    assert projection.grid_origin == (-157991.90412900544, 206640.69120000003)
    assert projection.origin_text == '151.2093,-33.8688'


def test_city_name_rules_default_to_the_shared_words() -> None:
    """A city with no rules of its own gets the shared word lists."""
    assert NameRules() == NameRules(
        street=STREET_WORDS,
        facility=FACILITY_WORDS,
        joining=(),
        centres=(),
        dash=False,
    )
    assert fields(City)[-1].name == 'name_rules'
