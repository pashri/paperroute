"""Tests for the aeroplane pictogram outline."""

import pytest

from paperroute_device.render.pictogram import plane_path, plane_size


def test_plane_size_is_about_four_fifths_of_the_type() -> None:
    """The pictogram is a little under the type's pixel size."""
    assert [plane_size(px=px) for px in (13, 15, 18)] == [10, 12, 14]


def test_plane_path_fills_a_square_of_its_size() -> None:
    """The outline spans its size each way about its centre."""
    bounds = plane_path(size=12.0, centre=(50.0, 50.0)).getBounds()
    assert (
        bounds.left(), bounds.top(), bounds.right(), bounds.bottom()
    ) == pytest.approx((44.0, 44.0, 56.0, 56.0))
