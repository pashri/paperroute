"""Tests for reading the rail track from OSM files."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from paperroute_testkit.build.osm_factory import write_rail_osm
from paperroute_testkit.fake_city import TEST_PROJECTION
from paperroute_transit.build.track_osm import (
    rail_station,
    read_track,
    running_line,
)


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
    """The siding and the separate line are left out."""
    track = read_track(paths=[rail_osm], projection=TEST_PROJECTION)
    assert len(track.xs) == 3
    assert len(track.edges) == 4
    assert len(track.ys) == 3


def test_a_station_is_read_from_its_node(rail_osm: Path) -> None:
    """Only the named halt is a station, at its projected place."""
    track = read_track(paths=[rail_osm], projection=TEST_PROJECTION)
    (station,) = track.stations
    assert (station.osm_id, station.name) == (2, 'Halt One')
    assert station.lon == pytest.approx(151.010)


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
