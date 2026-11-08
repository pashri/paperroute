"""The values that fit the engine to one city."""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from paperroute_transit.core.geo import DegreeBox, Projection


@dataclass(frozen=True)
class City:
    """
    One city's settings.

    Attributes
    ----------
    name : str
        The city's name.
    zone : ZoneInfo
        Local time zone: clocks, service days and auto windows use it.
    projection : Projection
        The metre projection and pixel grid for its maps.
    bbox : DegreeBox
        The area its tiles may cover.
    """

    name: str
    zone: ZoneInfo
    projection: Projection
    bbox: DegreeBox
