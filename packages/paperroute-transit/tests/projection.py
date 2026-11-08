"""A projection for the transit tests, with today's map numbers."""

from typing import Final

from paperroute_transit.core.geo import Projection

ORIGIN: Final[str] = '151.2093,-33.8688'
PROJECTION: Final[Projection] = Projection(
    origin_lon=151.2093,
    origin_lat=-33.8688,
    grid_lon=149.5,
    grid_lat=-32.0,
)
