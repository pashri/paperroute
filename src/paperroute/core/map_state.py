"""What the reducer keeps for the Map view: zoom, centre and map data."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Final

from paperroute.core.geo import MetreBox, Point, Shape
from paperroute.core.map_view import DEFAULT_ZOOM, MAP_HEIGHT, MAP_WIDTH
from paperroute.core.model import Mode
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.tiles import ZOOMS

AREA_GROWTH: Final[float] = 1.1


@dataclass(frozen=True)
class MapState:
    """The Map view's zoom, centre and loaded data.

    Attributes
    ----------
    zoom : int
        Index into `ZOOMS`.
    centre : Point | None
        The From stop in metres, or None when it has no location.
    shapes : Mapping[str, Shape]
        Loaded shapes by shape ID.
    requested_shapes : frozenset[str]
        Shape IDs already asked for.
    patterns : Mapping[str, TripPattern | None]
        Loaded trip patterns by trip ID; None when the trip is unknown.
    pending_patterns : frozenset[str]
        Trip IDs whose patterns are being loaded.
    area_modes : Mapping[int, frozenset[Mode]]
        Modes found in the map area, by metres per pixel.
    pending_modes : frozenset[int]
        Metres per pixel whose modes are being loaded.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed.
    requested_feeds : frozenset[str]
        Positions feeds fetched at least once.
    """

    zoom: int = DEFAULT_ZOOM
    centre: Point | None = None
    shapes: Mapping[str, Shape] = field(default_factory=dict)
    requested_shapes: frozenset[str] = frozenset()
    patterns: Mapping[str, TripPattern | None] = field(default_factory=dict)
    pending_patterns: frozenset[str] = frozenset()
    area_modes: Mapping[int, frozenset[Mode]] = field(default_factory=dict)
    pending_modes: frozenset[int] = frozenset()
    positions: Mapping[str, PositionsSnapshot] = field(default_factory=dict)
    requested_feeds: frozenset[str] = frozenset()


def area_box(*, centre: Point, mpp: int) -> MetreBox:
    """
    Find the map rectangle in metres, grown by 10%.

    Parameters
    ----------
    centre : Point
        The map centre in metres.
    mpp : int
        Metres per pixel.

    Returns
    -------
    MetreBox
        West, south, east and north.
    """
    half_width = MAP_WIDTH * mpp * AREA_GROWTH / 2
    half_height = MAP_HEIGHT * mpp * AREA_GROWTH / 2
    x, y = centre
    return (x - half_width, y - half_height, x + half_width, y + half_height)


def zoomed(*, map_state: MapState, steps: int) -> MapState:
    """
    Zoom by some knob detents.

    Parameters
    ----------
    map_state : MapState
        The Map state.
    steps : int
        Detents; clockwise (positive) zooms in, towards 3 m/px.

    Returns
    -------
    MapState
        The state with the zoom moved and clamped to `ZOOMS`.
    """
    zoom = min(max(map_state.zoom - steps, 0), len(ZOOMS) - 1)
    return replace(map_state, zoom=zoom)
