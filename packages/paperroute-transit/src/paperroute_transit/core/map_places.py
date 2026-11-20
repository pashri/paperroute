"""Which place names a city's map labels."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

PLACE_KINDS: Final[frozenset[str]] = frozenset(
    {'suburb', 'town', 'locality', 'village'},
)


class StationSource(StrEnum):
    """The OSM nodes a map names as stations."""

    RAIL = 'rail'
    MINOR = 'minor'
    FERRY = 'ferry'


STATION_SOURCES: Final[frozenset[StationSource]] = frozenset(StationSource)


@dataclass(frozen=True)
class MapPlaces:
    """
    The place names a city's map labels.

    Attributes
    ----------
    kinds : frozenset[str]
        OSM place kinds that are named.
    extras : tuple[tuple[str, float, float], ...]
        Places missing from OSM, as name, latitude and longitude. Each is
        named as a suburb of rank 0.
    stations : frozenset[StationSource]
        The stations that are named: railway stations, minor stops (halts
        and tram stops) and ferry terminals.
    dropped : frozenset[str]
        OSM place names that are never named, whatever their kind.
    """

    kinds: frozenset[str] = PLACE_KINDS
    extras: tuple[tuple[str, float, float], ...] = ()
    stations: frozenset[StationSource] = STATION_SOURCES
    dropped: frozenset[str] = frozenset()
