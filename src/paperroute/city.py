"""Sydney's values for the engine."""

from typing import Final
from zoneinfo import ZoneInfo

from paperroute_transit.core.city import City
from paperroute_transit.core.geo import Projection
from paperroute_transit.core.tiles import NSW_BBOX

SYDNEY_ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')
SYDNEY_PROJECTION: Final[Projection] = Projection(
    origin_lon=151.2093,
    origin_lat=-33.8688,
    grid_lon=149.5,
    grid_lat=-32.0,
)
SYDNEY: Final[City] = City(
    name='Sydney',
    zone=SYDNEY_ZONE,
    projection=SYDNEY_PROJECTION,
    bbox=NSW_BBOX,
)
