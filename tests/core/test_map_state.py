"""Tests for the Map view's slice of the state."""

import pytest

from paperroute_transit.core.map_fit import resting_zoom, stepped
from paperroute_transit.core.map_state import (
    MapState,
    area_box,
    area_frame,
    zoomed,
)
from paperroute_transit.core.map_view import (
    DEFAULT_ZOOM,
    MAP_HEIGHT,
    MAP_WIDTH,
)
from paperroute_transit.core.tiles import ZOOMS

FRAMED: MapState = MapState(centre=(1.0, 2.0), frame=((5.0, 6.0), 0))


def test_map_state_defaults_are_empty() -> None:
    """A bare MapState rests at 8 m/px with nothing loaded."""
    state = MapState()
    assert state.step == 0
    assert ZOOMS[resting_zoom(step=state.step)] == 8
    assert area_frame(map_state=state) == (None, DEFAULT_ZOOM)
    assert state.centre is None
    assert not state.shapes
    assert not state.requested_shapes
    assert not state.patterns
    assert not state.pending_patterns
    assert not state.area_modes
    assert not state.pending_modes
    assert not state.positions
    assert not state.fetched_at
    assert not state.failed_at


def test_area_box_is_the_map_grown_by_ten_percent() -> None:
    """The box is the map rectangle at the zoom, 10% bigger, west first."""
    west, south, east, north = area_box(centre=(1000.0, 2000.0), mpp=8)
    assert east - west == pytest.approx(MAP_WIDTH * 8 * 1.1)
    assert north - south == pytest.approx(MAP_HEIGHT * 8 * 1.1)
    assert (west + east) / 2 == pytest.approx(1000.0)
    assert (south + north) / 2 == pytest.approx(2000.0)


def test_resting_zoom_is_default_plus_step() -> None:
    """The resting zoom is the default moved by the step, clamped."""
    assert resting_zoom(step=0) == DEFAULT_ZOOM
    assert resting_zoom(step=-1) == 1
    assert resting_zoom(step=9) == len(ZOOMS) - 1


@pytest.mark.parametrize(
    ('zoom', 'step', 'index'),
    [(2, 0, 2), (2, 1, 3), (0, -3, 0), (4, 3, 5)],
)
def test_stepped_moves_and_clamps(zoom: int, step: int, index: int) -> None:
    """A step moves the fitted index and stays within the zooms."""
    assert stepped(zoom=zoom, step=step) == index


def test_zoomed_clockwise_zooms_in() -> None:
    """A positive detent moves towards 3 m/px."""
    finer = zoomed(map_state=MapState(), steps=1)
    coarser = zoomed(map_state=MapState(), steps=-1)
    assert ZOOMS[resting_zoom(step=finer.step)] == 5
    assert ZOOMS[resting_zoom(step=coarser.step)] == 14


@pytest.mark.parametrize(
    ('steps', 'mpp'),
    [(10, 3), (-10, 45), (2, 3), (-3, 45)],
)
def test_zoomed_clamps_at_both_ends(steps: int, mpp: int) -> None:
    """The zoom stops at 3 and 45 m/px and doesn't wrap."""
    step = zoomed(map_state=MapState(), steps=steps).step
    assert ZOOMS[resting_zoom(step=step)] == mpp


def test_knob_counts_only_detents_that_move() -> None:
    """Detents past the finest zoom are not kept; the next coarser moves."""
    state = zoomed(map_state=FRAMED, steps=2)
    assert state.step == 0
    state = zoomed(map_state=state, steps=-1)
    assert state.step == 1
    assert stepped(zoom=0, step=state.step) == 1


def test_knob_without_frame_steps_from_rest() -> None:
    """With no frame the knob steps from the resting zoom."""
    state = zoomed(map_state=MapState(), steps=-1)
    assert state.step == 1
    state = zoomed(map_state=state, steps=-1)
    assert resting_zoom(step=state.step) == DEFAULT_ZOOM + 2
    state = zoomed(map_state=state, steps=-9)
    assert resting_zoom(step=state.step) == len(ZOOMS) - 1
    assert state.step == len(ZOOMS) - 1 - DEFAULT_ZOOM


def test_knob_step_is_clamped() -> None:
    """A frame stuck at the finest zoom can't build up hidden detents."""
    state = FRAMED
    for _ in range(20):
        state = zoomed(map_state=state, steps=-1)
    assert state.step == len(ZOOMS) - 1


def test_area_frame_rests_at_the_step() -> None:
    """With no frame the area is the From at the resting zoom."""
    state = MapState(centre=(1.0, 2.0), step=-1)
    assert area_frame(map_state=state) == ((1.0, 2.0), DEFAULT_ZOOM - 1)
    assert area_frame(map_state=FRAMED) == ((5.0, 6.0), 0)
