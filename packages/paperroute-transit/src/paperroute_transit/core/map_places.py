"""Which place names a city's map labels."""

from dataclasses import dataclass
from typing import Final

PLACE_KINDS: Final[frozenset[str]] = frozenset(
    {'suburb', 'town', 'locality', 'village'},
)


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
    """

    kinds: frozenset[str] = PLACE_KINDS
    extras: tuple[tuple[str, float, float], ...] = ()
