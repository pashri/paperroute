"""Tests for rail paths and the stations along them."""

from array import array

import pytest

from paperroute_transit.build.track_db import RailTrack
from paperroute_transit.build.track_paths import (
    TrackGraph,
    along,
    graph_of,
    rail_path,
    snapped,
)

LINE_NODES = 21
LOOP_FIRST = 21


def _track(
    *,
    points: list[tuple[float, float]],
    pairs: list[tuple[int, int]],
) -> RailTrack:
    """
    Build a track from points and node pairs.

    Parameters
    ----------
    points : list[tuple[float, float]]
        Node coordinates in metres.
    pairs : list[tuple[int, int]]
        The edges.

    Returns
    -------
    RailTrack
        A track with no stations.
    """
    return RailTrack(
        xs=array('d', (x for x, _ in points)),
        ys=array('d', (y for _, y in points)),
        edges=array('q', (node for pair in pairs for node in pair)),
        stations=(),
    )


@pytest.fixture(name='graph', scope='module')
def _graph() -> TrackGraph:
    """
    A straight track of 21 nodes 100 m apart, and a separate loop.

    Returns
    -------
    TrackGraph
        Nodes 0-20 run east along y = 0; nodes 21-24 form a loop far
        away.
    """
    points = [(100.0 * i, 0.0) for i in range(LINE_NODES)]
    points += [(10000.0, 0.0), (10100.0, 0.0), (10100.0, 100.0)]
    points += [(10000.0, 100.0)]
    pairs = [(i, i + 1) for i in range(LINE_NODES - 1)]
    pairs += [(LOOP_FIRST + i, LOOP_FIRST + (i + 1) % 4) for i in range(4)]
    return graph_of(track=_track(points=points, pairs=pairs))


def test_a_station_snaps_to_every_node_within_150_m(
    graph: TrackGraph,
) -> None:
    """Nodes 3, 4 and 5 are within 150 m of a point at x = 400."""
    assert sorted(snapped(graph=graph, point=(400.0, 100.0))) == [3, 4, 5]


def test_a_station_beyond_150_m_snaps_to_nothing(graph: TrackGraph) -> None:
    """A point 151 m from the line has no node."""
    assert not snapped(graph=graph, point=(400.0, 151.0))


def test_a_winding_path_is_refused() -> None:
    """A 4 km detour for a 1 km straight line gives None."""
    points = [(0.0, 0.0), (0.0, 1500.0), (1000.0, 1500.0), (1000.0, 0.0)]
    detour = graph_of(
        track=_track(points=points, pairs=[(0, 1), (1, 2), (2, 3)]),
    )
    refused = rail_path(graph=detour, starts=[0], ends=[3], straight=1000.0)
    allowed = rail_path(graph=detour, starts=[0], ends=[3], straight=2000.0)
    assert refused is None
    assert allowed == (0, 1, 2, 3)


def test_stations_within_60_m_are_passed_in_order(graph: TrackGraph) -> None:
    """A point 59 m off the line is kept and one 61 m off is not."""
    path = rail_path(graph=graph, starts=[0], ends=[20], straight=2000.0)
    assert path is not None
    points = {
        'far': (1550.0, 59.0),
        'near': (250.0, -59.0),
        'out': (950.0, 61.0),
    }
    assert along(graph=graph, path=path, points=points) == ('near', 'far')


def test_no_path_between_parts_is_none(graph: TrackGraph) -> None:
    """The loop is not reached from the line."""
    path = rail_path(graph=graph, starts=[0], ends=[22], straight=100.0)
    assert path is None


def test_the_shortest_combination_of_snapped_nodes_wins(
    graph: TrackGraph,
) -> None:
    """Of the four start and end pairs, 0 to 3 is the shortest."""
    path = rail_path(
        graph=graph,
        starts=[0, 15],
        ends=[3, 20],
        straight=300.0,
    )
    assert path == (0, 1, 2, 3)


def test_along_orders_by_metres_when_the_path_runs_west(
    graph: TrackGraph,
) -> None:
    """Against increasing x, the larger x comes first."""
    path = rail_path(graph=graph, starts=[20], ends=[0], straight=2000.0)
    assert path is not None
    points = {'low': (300.0, 10.0), 'high': (900.0, 10.0)}
    assert along(graph=graph, path=path, points=points) == ('high', 'low')
