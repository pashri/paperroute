"""Tests for reading the rail track from OSM files."""

import math
from collections.abc import Mapping
from pathlib import Path

import osmium
import pytest
from osmium.osm.mutable import Node, Way

from paperroute_testkit.build.osm_factory import write_rail_osm
from paperroute_testkit.fake_city import TEST_PROJECTION
from paperroute_transit.build.track_db import RailTrack
from paperroute_transit.build.track_osm import (
    EDGE_LIMIT,
    TrackParts,
    build_track,
    largest_part,
    rail_station,
    read_track,
    running_line,
    track_entities,
)
from paperroute_transit.core.geo import to_metres


@pytest.fixture(name='rail_osm', scope='module')
def _rail_osm(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Write the rail OSM file once for the module.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Pytest's temporary directory factory.

    Returns
    -------
    Path
        The OSM file.
    """
    path = tmp_path_factory.mktemp('osm') / 'rail.osm.pbf'
    write_rail_osm(path=path)
    return path


def test_the_track_keeps_running_lines_of_the_largest_part(
    rail_osm: Path,
) -> None:
    """The siding and the separate line are left out.

    The two fixture edges are 600 m or more, so each is split into seven.
    """
    track = read_track(paths=[rail_osm], projection=TEST_PROJECTION)
    assert len(track.xs) == 15
    assert len(track.edges) == 28
    assert len(track.ys) == 15


def test_edges_index_nodes_at_the_fixture_points(rail_osm: Path) -> None:
    """Every edge value indexes a node; the first three are A, B and C."""
    track = read_track(paths=[rail_osm], projection=TEST_PROJECTION)
    assert all(0 <= node < len(track.xs) for node in track.edges)
    a, b, c = (
        to_metres(lon=lon, lat=-33.800, projection=TEST_PROJECTION)
        for lon in (151.000, 151.010, 151.020)
    )
    nodes = [(track.xs[i], track.ys[i]) for i in range(3)]
    assert nodes == pytest.approx([a, b, c])
    assert track.edges[0] == 0
    assert track.edges[-1] == 2


def test_a_station_is_read_from_its_node(rail_osm: Path) -> None:
    """Only the named halt is a station, at its projected place."""
    track = read_track(paths=[rail_osm], projection=TEST_PROJECTION)
    (station,) = track.stations
    assert (station.osm_id, station.name) == (2, 'Halt One')
    x, y = to_metres(
        lon=151.010,
        lat=-33.800,
        projection=TEST_PROJECTION,
    )
    assert station.lon == pytest.approx(151.010)
    assert station.lat == pytest.approx(-33.800)
    assert station.x == pytest.approx(x)
    assert station.y == pytest.approx(y)


def test_a_subway_station_is_not_a_rail_station(rail_osm: Path) -> None:
    """The subway node and the unnamed node are dropped."""
    track = read_track(paths=[rail_osm], projection=TEST_PROJECTION)
    assert {s.osm_id for s in track.stations} == {2}


def test_overlapping_extracts_count_once(rail_osm: Path) -> None:
    """Reading the same file twice changes nothing."""
    once = read_track(paths=[rail_osm], projection=TEST_PROJECTION)
    twice = read_track(
        paths=[rail_osm, rail_osm],
        projection=TEST_PROJECTION,
    )
    assert twice == once


@pytest.mark.parametrize(
    ('tags', 'expected'),
    [
        ({'railway': 'rail'}, True),
        ({'railway': 'rail', 'service': 'siding'}, False),
        ({'railway': 'rail', 'service': 'yard'}, False),
        ({'railway': 'rail', 'service': 'spur'}, False),
        ({'railway': 'rail', 'service': 'crossover'}, True),
        ({'railway': 'light_rail'}, False),
        ({}, False),
    ],
)
def test_running_line(tags: Mapping[str, str], expected: bool) -> None:
    """A way runs when it is rail and not a siding, yard or spur."""
    assert running_line(tags=tags) is expected


@pytest.mark.parametrize(
    ('tags', 'expected'),
    [
        ({'railway': 'station', 'name': 'A'}, True),
        ({'railway': 'halt', 'name': 'A'}, True),
        ({'railway': 'station'}, False),
        ({'railway': 'tram_stop', 'name': 'A'}, False),
        ({'railway': 'station', 'name': 'A', 'station': 'subway'}, False),
        ({'railway': 'station', 'name': 'A', 'station': 'funicular'}, False),
        ({'railway': 'station', 'name': 'A', 'subway': 'yes'}, False),
        ({'railway': 'station', 'name': 'A', 'light_rail': 'yes'}, False),
        (
            {
                'railway': 'station',
                'name': 'A',
                'subway': 'yes',
                'train': 'yes',
            },
            True,
        ),
    ],
)
def test_rail_station(tags: Mapping[str, str], expected: bool) -> None:
    """A station is named, and not a subway or light rail stop."""
    assert rail_station(tags=tags) is expected


def test_only_objects_with_a_rail_tag_reach_the_classifiers(
    tmp_path: Path,
) -> None:
    """Untagged nodes and ways without ``railway`` are never yielded."""
    path = tmp_path / 'mixed.osm.pbf'
    with osmium.SimpleWriter(path, overwrite=True) as writer:
        for node_id, tags in (
            (1, {}),
            (2, {'railway': 'halt', 'name': 'Halt'}),
            (3, {}),
            (4, {'amenity': 'cafe'}),
        ):
            writer.add_node(Node(
                id=node_id, location=(151.0 + node_id / 100, -33.8),
                tags=tags,
            ))
        writer.add_way(Way(id=1, nodes=[1, 2, 3], tags={'railway': 'rail'}))
        writer.add_way(Way(id=2, nodes=[1, 3], tags={'highway': 'road'}))
    found = [
        (type(entity).__name__, entity.id, 'railway' in entity.tags)
        for entity in track_entities(path=path)
    ]
    assert found == [('Node', 2, True), ('Way', 1, True)]


def test_a_filtered_way_keeps_its_untagged_nodes_located(
    rail_osm: Path,
) -> None:
    """Track nodes are untagged, yet each kept way's nodes have places."""
    located = [
        [node.location.valid() for node in entity.nodes]
        for entity in track_entities(path=rail_osm)
        if isinstance(entity, osmium.osm.Way)
    ]
    assert [len(way) for way in located] == [3, 2, 2]
    assert all(all(way) for way in located)


def long_edge_track(*, metres: float) -> RailTrack:
    """
    Build a track of one straight edge along the x axis.

    Parameters
    ----------
    metres : float
        Length of the edge.

    Returns
    -------
    RailTrack
        The track built from one way of two nodes.
    """
    west, _ = to_metres(lon=151.0, lat=-33.8, projection=TEST_PROJECTION)
    east, _ = to_metres(lon=152.0, lat=-33.8, projection=TEST_PROJECTION)
    parts = TrackParts(
        spots={1: (151.0, -33.8), 2: (151.0 + metres / (east - west), -33.8)},
        ways={1: (1, 2)},
    )
    return build_track(parts=parts, projection=TEST_PROJECTION)


def edge_lengths(*, track: RailTrack) -> list[float]:
    """
    Measure every edge of a track.

    Parameters
    ----------
    track : RailTrack
        The track.

    Returns
    -------
    list[float]
        Edge lengths in metres.
    """
    return [
        math.hypot(
            track.xs[a] - track.xs[b],
            track.ys[a] - track.ys[b],
        )
        for a, b in zip(track.edges[::2], track.edges[1::2], strict=True)
    ]


def test_a_long_edge_is_split_into_equal_pieces() -> None:
    """A 250 m edge becomes three pieces on the straight line."""
    track = long_edge_track(metres=250)
    lengths = edge_lengths(track=track)
    assert len(track.xs) == 4
    assert len(lengths) == 3
    assert lengths == pytest.approx([lengths[0]] * 3)
    assert lengths[0] < EDGE_LIMIT
    x0, y0 = track.xs[0], track.ys[0]
    x1, y1 = track.xs[1], track.ys[1]
    assert (x1 - x0) * (track.ys[3] - y0) == pytest.approx(
        (y1 - y0) * (track.xs[3] - x0), abs=1e-6,
    )


def test_an_edge_of_the_limit_is_not_split() -> None:
    """An edge no longer than the limit keeps two nodes."""
    track = long_edge_track(metres=EDGE_LIMIT)
    assert len(track.xs) == 2
    assert len(track.edges) == 2


def test_every_edge_is_short_and_the_track_stays_one_part(
    rail_osm: Path,
) -> None:
    """Splitting leaves no long edge and no gap."""
    track = read_track(paths=[rail_osm], projection=TEST_PROJECTION)
    assert max(edge_lengths(track=track)) <= EDGE_LIMIT + 1e-6
    links = list(zip(track.edges[::2], track.edges[1::2], strict=True))
    assert len(largest_part(ways=links)) == len(track.xs)
