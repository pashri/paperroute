"""Tests for turning a feed's distances into metres along stored lines."""

from typing import Final

import numpy as np
import pytest

from paperroute_transit.build.feed_along import (
    FeedLine,
    feed_along,
    feed_figure,
    kept_feed,
    stop_along,
)
from paperroute_transit.core.geo import make_shape

LINE: Final[FeedLine] = FeedLine(
    shape=make_shape(
        shape_id='l', points=[(0.0, 0.0), (100.0, 0.0), (100.0, 300.0)],
    ),
    feed=(1.0, 1.1, 1.4),
)


def test_feed_figure_reads_numbers_and_rejects_the_rest() -> None:
    """A number is read; blank, text and non-finite values are not."""
    assert feed_figure(text='12.5') == 12.5
    assert [
        feed_figure(text=text) for text in ('', 'abc', 'nan', 'inf')
    ] == [None, None, None, None]


def test_kept_feed_keeps_the_kept_points_distances() -> None:
    """Only the distances of the points simplification keeps remain."""
    assert kept_feed(
        distances=[0.0, 1.0, 2.5, 4.0],
        keep=np.array([True, False, True, True]),
    ) == (0.0, 2.5, 4.0)


@pytest.mark.parametrize(
    'distances',
    [[0.0, None, 2.0], [0.0, 3.0, 2.0], [1.0, 1.0, 1.0], [None, None, None]],
)
def test_kept_feed_refuses_a_blank_falling_or_flat_line(
    distances: list[float | None],
) -> None:
    """A line with a gap, a fall or no rise has no feed distances."""
    assert kept_feed(
        distances=distances, keep=np.array([True, True, True]),
    ) is None


def test_feed_along_interpolates_between_kept_points() -> None:
    """A feed distance maps linearly between the vertices either side."""
    assert [
        feed_along(line=LINE, distance=distance)
        for distance in (1.0, 1.05, 1.25, 1.4)
    ] == pytest.approx([0.0, 50.0, 250.0, 400.0])


def test_feed_along_outside_the_line_is_none() -> None:
    """A distance before the first or after the last vertex has no place."""
    assert feed_along(line=LINE, distance=0.99) is None
    assert feed_along(line=LINE, distance=1.41) is None


def test_stop_along_rounds_to_whole_metres() -> None:
    """A converted point near its stop is stored in whole metres."""
    assert stop_along(line=LINE, distance=1.0532, stop=(53.0, 5.0)) == 53
    assert stop_along(line=LINE, distance=1.25, stop=(160.0, 150.0)) == 250


def test_stop_along_far_from_its_stop_or_without_one_is_none() -> None:
    """More than 60 m from the stop, or no stop point, stores nothing."""
    assert stop_along(line=LINE, distance=1.25, stop=(161.0, 150.0)) is None
    assert stop_along(line=LINE, distance=1.25, stop=None) is None
    assert stop_along(line=LINE, distance=1.5, stop=(100.0, 300.0)) is None
