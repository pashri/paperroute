"""Tests for the Map view's slice of the state."""

import pytest

from paperroute.core.map_state import MapState, area_box, zoomed
from paperroute.core.map_view import DEFAULT_ZOOM, MAP_HEIGHT, MAP_WIDTH
from paperroute.core.tiles import ZOOMS


def test_map_state_defaults_are_empty() -> None:
    """A bare MapState opens at 8 m/px with nothing loaded."""
    state = MapState()
    assert ZOOMS[state.zoom] == 8
    assert state.zoom == DEFAULT_ZOOM
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


def test_zoomed_clockwise_zooms_in() -> None:
    """A positive step moves towards 3 m/px."""
    assert ZOOMS[zoomed(map_state=MapState(), steps=1).zoom] == 5
    assert ZOOMS[zoomed(map_state=MapState(), steps=-1).zoom] == 14


@pytest.mark.parametrize(
    ('steps', 'mpp'),
    [(10, 3), (-10, 45), (2, 3), (-2, 45)],
)
def test_zoomed_clamps_at_both_ends(steps: int, mpp: int) -> None:
    """The zoom stops at 3 and 45 m/px and doesn't wrap."""
    assert ZOOMS[zoomed(map_state=MapState(), steps=steps).zoom] == mpp
