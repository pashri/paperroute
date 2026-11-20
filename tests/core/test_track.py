"""Tests for train patterns drawn along the stored track."""

from dataclasses import replace
from typing import Final

from paperroute_testkit.core.factories import calls, pattern
from paperroute_transit.core.geo import Point, make_shape
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.core.track import call_pairs, tracked_shape

POINTS: Final[dict[str, Point]] = {
    'A': (0.0, 0.0),
    'X': (1000.0, 500.0),
    'B': (2000.0, 0.0),
    'C': (4000.0, 0.0),
}
FEED: Final = make_shape(
    shape_id='feed', points=((0.0, 0.0), (2000.0, 0.0), (4000.0, 0.0)),
)
HOP: Final[tuple[Point, ...]] = (
    (20.0, 0.0), (1000.0, 300.0), (1990.0, 0.0),
)
RAIL: Final = replace(
    pattern(
        trip_id='train',
        shape_id='feed',
        stops=calls(stations='ABC', step=600, points=POINTS),
    ),
    along=(0.0, 2000.0, 4000.0),
    road=False,
)


def test_a_held_hop_follows_the_track() -> None:
    """Between two calls the train runs along the stored track."""
    shape = tracked_shape(
        pattern=RAIL, shape=FEED, hops={('A', 'B'): HOP}, version='v1',
    )
    assert shape is not None
    assert shape.points == (*HOP, (4000.0, 0.0))
    assert shape.shape_id.startswith('track:')


def test_the_id_changes_with_the_timetable() -> None:
    """A rebuilt timetable gives the same hops a new shape id."""

    def made(version: str) -> str:
        shape = tracked_shape(
            pattern=RAIL, shape=FEED, hops={('A', 'B'): HOP},
            version=version,
        )
        assert shape is not None
        return shape.shape_id

    assert made('v1') != made('v2')
    assert made('v1') == made('v1')


def test_no_held_hop_keeps_the_pattern_as_it_was() -> None:
    """With no track at all there is nothing to swap."""
    assert tracked_shape(
        pattern=RAIL, shape=FEED, hops={}, version='v1',
    ) is None


def test_passed_stations_are_not_hop_ends() -> None:
    """A passed station between two calls does not split the hop."""
    stops = calls(stations='AXBC', step=300, points=POINTS)
    passing = replace(
        RAIL,
        stops=(stops[0], replace(stops[1], passes=True), *stops[2:]),
        along=(0.0, 1100.0, 2000.0, 4000.0),
    )
    shape = tracked_shape(
        pattern=passing, shape=FEED, hops={('A', 'B'): HOP}, version='v1',
    )
    assert shape is not None and shape.points[: len(HOP)] == HOP


def test_a_missing_hop_keeps_the_feed_stretch() -> None:
    """A hop with no track is cut from the feed shape."""
    shape = tracked_shape(
        pattern=RAIL, shape=FEED, hops={('B', 'C'): HOP}, version='v1',
    )
    assert shape is not None
    assert shape.points == ((0.0, 0.0), (2000.0, 0.0), *HOP)


def test_a_hop_without_a_feed_shape_runs_straight() -> None:
    """With no feed shape a missing hop is the line between the calls."""
    shape = tracked_shape(
        pattern=replace(RAIL, along=None),
        shape=None,
        hops={('B', 'C'): ((2010.0, 0.0), (3990.0, 0.0))},
        version='v1',
    )
    assert shape is not None
    assert shape.points == ((0.0, 0.0), (2000.0, 0.0), (3990.0, 0.0))


def test_a_one_point_hop_is_not_held() -> None:
    """A track path of one point is no track."""
    assert tracked_shape(
        pattern=RAIL, shape=FEED, hops={('A', 'B'): (HOP[0],)}, version='v1',
    ) is None


def tracked_id(*, trip: TripPattern) -> str:
    """Give the id of a pattern's tracked shape, which must exist."""
    shape = tracked_shape(
        pattern=trip, shape=FEED, hops={('A', 'B'): HOP}, version='v1',
    )
    assert shape is not None
    return shape.shape_id


def test_the_id_changes_with_the_calls() -> None:
    """Two trips on one shape with the same hops but another end differ."""
    short = replace(
        RAIL, stops=RAIL.stops[:2], along=RAIL.along[:2] if RAIL.along else None,
    )
    ids = {tracked_id(trip=trip) for trip in (RAIL, short)}
    assert len(ids) == 2


def test_call_pairs_skip_passed_stations() -> None:
    """Pairs join consecutive stopping calls."""
    stops = calls(stations='AXBC', step=300, points=POINTS)
    passing = replace(
        RAIL, stops=(stops[0], replace(stops[1], passes=True), *stops[2:]),
    )
    assert list(call_pairs(pattern=passing)) == [
        (0, 2), (2, 3),
    ]
