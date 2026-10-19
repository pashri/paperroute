"""Tests for the map geometry helpers."""

import numpy as np
from numpy.typing import NDArray

from paperroute.build.geometry import (
    clip_lines,
    clip_rings,
    merge_lines,
    sea_rings,
)
from tests.build.rings import inside


def line(*points: tuple[float, float]) -> NDArray[np.float64]:
    """
    Build an n by 2 vertex array.

    Parameters
    ----------
    *points : tuple[float, float]
        Vertices.

    Returns
    -------
    NDArray[np.float64]
        The array.
    """
    return np.array(points, dtype=np.float64)


def test_sea_is_right_of_the_coastline() -> None:
    """A coastline running north has the sea on its east side."""
    coast = line((0.0, -2000.0), (0.0, 2000.0))
    rings = sea_rings(coast=[coast], frame=(-1000.0, -1000.0, 1000.0, 1000.0))
    assert any(inside(rings=[ring], point=(500.0, 0.0)) for ring in rings)
    assert not any(
        inside(rings=[ring], point=(-500.0, 0.0)) for ring in rings
    )


def test_sea_reaches_the_frame_past_a_coast_cut_inside_it() -> None:
    """A coastline that stops inside the frame still splits sea and land."""
    coast = line((0.0, -500.0), (0.0, 500.0))
    rings = sea_rings(coast=[coast], frame=(-1000.0, -1000.0, 1000.0, 1000.0))
    assert any(inside(rings=[ring], point=(500.0, 0.0)) for ring in rings)
    assert any(inside(rings=[ring], point=(500.0, 900.0)) for ring in rings)
    assert not any(
        inside(rings=[ring], point=(-500.0, 0.0)) for ring in rings
    )
    assert not any(
        inside(rings=[ring], point=(-500.0, 900.0)) for ring in rings
    )


def test_sea_follows_a_bent_coast_cut_at_both_ends() -> None:
    """A coast cut at both ends runs on in the direction it was heading."""
    coast = line((-500.0, -500.0), (0.0, 0.0), (0.0, 500.0))
    rings = sea_rings(coast=[coast], frame=(-1000.0, -1000.0, 1000.0, 1000.0))
    assert any(inside(rings=[ring], point=(500.0, 0.0)) for ring in rings)
    assert any(inside(rings=[ring], point=(0.0, -900.0)) for ring in rings)
    assert not any(
        inside(rings=[ring], point=(-500.0, 500.0)) for ring in rings
    )


def test_sea_rings_without_coast_are_empty() -> None:
    """No coastline gives no sea."""
    assert not sea_rings(coast=[], frame=(0.0, 0.0, 1.0, 1.0))


def test_merge_lines_joins_touching_ends() -> None:
    """Two lines sharing an end become one."""
    merged = merge_lines(
        lines=[line((0.0, 0.0), (1.0, 0.0)), line((1.0, 0.0), (2.0, 0.0))],
    )
    assert [piece.tolist() for piece in merged] == [
        [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]],
    ]


def test_clip_lines_cuts_to_the_box() -> None:
    """A line is cut at the box edges and a far line is dropped."""
    clipped = clip_lines(
        lines=[line((0.0, 0.0), (10.0, 0.0)), line((0.0, 9.0), (1.0, 9.0))],
        box=(2.0, -1.0, 5.0, 1.0),
    )
    assert [piece.tolist() for piece in clipped] == [[[2.0, 0.0], [5.0, 0.0]]]


def test_clip_rings_keeps_holes_even_odd() -> None:
    """A clipped square with a hole still leaves the hole unfilled."""
    outer = line((0.0, 0.0), (9.0, 0.0), (9.0, 9.0), (0.0, 9.0), (0.0, 0.0))
    hole = line((4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0), (4.0, 4.0))
    rings = clip_rings(rings=[outer, hole], box=(2.0, 2.0, 20.0, 20.0))
    assert inside(rings=rings, point=(3.0, 3.0))
    assert not inside(rings=rings, point=(5.0, 5.0))
    assert not inside(rings=rings, point=(1.0, 1.0))
    assert not clip_rings(rings=[hole], box=(20.0, 20.0, 30.0, 30.0))
