"""The kerb across the road from a station."""

from dataclasses import dataclass

from paperroute_transit.core.model import StopPlace


@dataclass(frozen=True)
class KerbPair:
    """
    A station and the kerb across the road from it.

    Attributes
    ----------
    station : str
        ID of the station looked up.
    other : StopPlace
        The kerb across the road, with its point.
    busy : bool
        Whether both kerbs are busy enough to list apart.
    """

    station: str
    other: StopPlace
    busy: bool
