"""Sydney's values for the engine."""

from typing import Final
from zoneinfo import ZoneInfo

from paperroute_transit.core.city import City
from paperroute_transit.core.geo import DegreeBox, Projection

SYDNEY_ZONE: Final[ZoneInfo] = ZoneInfo('Australia/Sydney')
SYDNEY_PROJECTION: Final[Projection] = Projection(
    origin_lon=151.2093,
    origin_lat=-33.8688,
    grid_lon=149.5,
    grid_lat=-32.0,
)
SYDNEY_BBOX: Final[DegreeBox] = (140.99, -37.51, 153.64, -28.15)
SYDNEY: Final[City] = City(
    name='Sydney',
    zone=SYDNEY_ZONE,
    projection=SYDNEY_PROJECTION,
    bbox=SYDNEY_BBOX,
)
