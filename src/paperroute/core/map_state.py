"""What the reducer keeps for the Map view: knob step, centre and data."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

from paperroute.core.geo import MetreBox, Point, Shape
from paperroute.core.map_fit import resting_zoom
from paperroute.core.map_view import MAP_HEIGHT, MAP_WIDTH
from paperroute.core.messages import (
    LoadModes,
    LoadPatterns,
    LoadShapes,
    MapData,
    MapDataFailed,
    PatternsLoaded,
)
from paperroute.core.model import Mode
from paperroute.core.patterns import TripPattern
from paperroute.core.positions import PositionsSnapshot
from paperroute.core.tiles import ZOOMS

AREA_GROWTH: Final[float] = 1.1


@dataclass(frozen=True)
class MapState:
    """The Map view's knob step, centre and loaded data.

    Attributes
    ----------
    step : int
        Knob detents from the framed zoom; negative is finer. Not saved,
        and 0 for a new place.
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
    fetched_at : Mapping[str, datetime]
        When each positions feed was last asked for.
    failed_at : Mapping[MapData, datetime]
        When each kind of map data last failed to load.
    frame : tuple[Point, int] | None
        The centre and zoom a map was last drawn at, the knob's step
        already applied; None when no fitted map is drawn, which leaves
        the From at the resting zoom.
    frame_ends : tuple[Point | None, Point | None] | None
        The pin's From and To points the frame was drawn for; None with
        no frame.
    """

    step: int = 0
    centre: Point | None = None
    shapes: Mapping[str, Shape] = field(default_factory=dict)
    requested_shapes: frozenset[str] = frozenset()
    patterns: Mapping[str, TripPattern | None] = field(default_factory=dict)
    pending_patterns: frozenset[str] = frozenset()
    area_modes: Mapping[int, frozenset[Mode]] = field(default_factory=dict)
    pending_modes: frozenset[int] = frozenset()
    positions: Mapping[str, PositionsSnapshot] = field(default_factory=dict)
    fetched_at: Mapping[str, datetime] = field(default_factory=dict)
    failed_at: Mapping[MapData, datetime] = field(default_factory=dict)
    frame: tuple[Point, int] | None = None
    frame_ends: tuple[Point | None, Point | None] | None = None


def area_frame(*, map_state: MapState) -> tuple[Point | None, int]:
    """
    Give the centre and zoom of the map area on screen.

    Parameters
    ----------
    map_state : MapState
        Map state.

    Returns
    -------
    tuple[Point | None, int]
        The frame when one is drawn, else the From at the resting zoom.
    """
    if map_state.frame is not None:
        return map_state.frame
    return map_state.centre, resting_zoom(step=map_state.step)


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
    Zoom by some knob detents, counting only those that move the map.

    Parameters
    ----------
    map_state : MapState
        The Map state.
    steps : int
        Detents; clockwise (positive) zooms in, towards 3 m/px.

    Returns
    -------
    MapState
        The state with the step moved by as many levels as the shown
        zoom moved, clamped so it never passes the number of levels.
    """
    _, zoom = area_frame(map_state=map_state)
    moved = min(max(zoom - steps, 0), len(ZOOMS) - 1) - zoom
    reach = len(ZOOMS) - 1
    step = min(max(map_state.step + moved, -reach), reach)
    return replace(map_state, step=step)


def with_patterns(*, map_state: MapState, event: PatternsLoaded) -> MapState:
    """
    Store loaded patterns and their shapes, and clear the request.

    Parameters
    ----------
    map_state : MapState
        The Map state.
    event : PatternsLoaded
        Loaded patterns.

    Returns
    -------
    MapState
        The Map state with every requested trip stored, None when it
        wasn't found, and none of them pending.
    """
    return replace(
        map_state,
        shapes={**map_state.shapes, **event.shapes},
        patterns={
            **map_state.patterns,
            **dict.fromkeys(event.trip_ids),
            **event.patterns,
        },
        pending_patterns=map_state.pending_patterns - event.trip_ids,
    )


def failed(
    *, map_state: MapState, event: MapDataFailed, now: datetime
) -> MapState:
    """
    Forget a failed map data request and start its backoff.

    Parameters
    ----------
    map_state : MapState
        The Map state.
    event : MapDataFailed
        The failure.
    now : datetime
        Current time.

    Returns
    -------
    MapState
        The Map state without the request's pending or requested entries,
        and with the failure time of its kind.
    """
    match event.request:
        case LoadShapes(shape_ids=shape_ids):
            asked = map_state.requested_shapes - shape_ids
            map_state = replace(map_state, requested_shapes=asked)
        case LoadPatterns(trip_ids=trip_ids):
            pending = map_state.pending_patterns - trip_ids
            map_state = replace(map_state, pending_patterns=pending)
        case LoadModes(mpp=mpp):
            modes = map_state.pending_modes - {mpp}
            map_state = replace(map_state, pending_modes=modes)
    failures = {**map_state.failed_at, event.kind: now}
    return replace(map_state, failed_at=failures)
