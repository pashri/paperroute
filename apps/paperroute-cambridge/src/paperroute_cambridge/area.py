"""The Cambridge map area and the rules that trim the timetable to it."""

from typing import Final

from paperroute_transit.core.geo import DegreeBox, Projection

CAMBRIDGE_PROJECTION: Final[Projection] = Projection(
    origin_lon=0.1218,
    origin_lat=52.2053,
    grid_lon=-1.0,
    grid_lat=53.0,
)
TRIM_RADIUS: Final[float] = 5_000  # metres
COACH_BOX: Final[DegreeBox] = (-0.472, 52.015, 0.489, 52.461)
CAMBRIDGE_BBOX: Final[DegreeBox] = (-0.482, 52.005, 0.499, 52.471)
