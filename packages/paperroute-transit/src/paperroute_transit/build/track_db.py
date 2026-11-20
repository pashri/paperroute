"""The rail track as flat arrays, and the stations along it."""

from array import array
from dataclasses import dataclass


@dataclass(frozen=True)
class TrackStation:
    """
    A named station node.

    Attributes
    ----------
    osm_id : int
        The OSM node id.
    name : str
        The station's name.
    lat : float
        Latitude in degrees.
    lon : float
        Longitude in degrees.
    x : float
        Metres east of the projection's origin.
    y : float
        Metres north of the projection's origin.
    """

    osm_id: int
    name: str
    lat: float
    lon: float
    x: float
    y: float


@dataclass(frozen=True)
class RailTrack:
    """
    Running rail lines as a graph, and the stations near them.

    Attributes
    ----------
    xs : array[float]
        Metres east of the origin, one per node. Node ids are the
        indexes, dense from zero.
    ys : array[float]
        Metres north of the origin, one per node.
    edges : array[int]
        Node id pairs, flattened: edge i joins ``edges[2 * i]`` and
        ``edges[2 * i + 1]``.
    stations : tuple[TrackStation, ...]
        Named stations, whether or not a line reaches them.
    """

    xs: array[float]
    ys: array[float]
    edges: array[int]
    stations: tuple[TrackStation, ...]
