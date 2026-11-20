"""Tests for a city's declared modes."""

import pytest

from paperroute_transit.core.modes import is_road


@pytest.mark.parametrize(
    ('route_type', 'road'),
    [
        (3, True), (11, True), (200, True), (204, True), (700, True),
        (712, True), (714, True), (0, False), (1, False), (2, False),
        (4, False), (100, False), (401, False), (900, False),
    ],
)
def test_is_road(route_type: int, road: bool) -> None:
    """Buses, coaches and replacement buses are road; the rest are not."""
    assert is_road(route_type=route_type) is road
