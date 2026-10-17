"""Tests for reading map features from an OSM file."""

from collections.abc import Mapping
from pathlib import Path

import osmium
import pytest
from osmium.osm.mutable import Node

from paperroute.build.osm_source import (
    OsmArea,
    OsmLine,
    OsmPlace,
    area_layer,
    header_timestamp,
    line_layer,
    place_kind,
    read_osm,
)
from tests.build.osm_factory import TIMESTAMP, TINY_BBOX, write_tiny_osm


@pytest.fixture(name='tiny_osm', scope='module')
def _tiny_osm(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Write the tiny OSM file once for the module.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Pytest's temporary directory factory.

    Returns
    -------
    Path
        The OSM file.
    """
    path = tmp_path_factory.mktemp('osm') / 'tiny.osm.pbf'
    write_tiny_osm(path=path)
    return path


@pytest.fixture(name='features', scope='module')
def _features(tiny_osm: Path) -> list[OsmLine | OsmArea | OsmPlace]:
    """
    Read every feature in the test bbox.

    Parameters
    ----------
    tiny_osm : Path
        The OSM file.

    Returns
    -------
    list[OsmLine | OsmArea | OsmPlace]
        The features.
    """
    return list(read_osm(path=tiny_osm, bbox=TINY_BBOX, index='flex_mem'))


@pytest.mark.parametrize(
    ('tags', 'layer'),
    [
        ({'highway': 'residential'}, 'road_minor'),
        ({'highway': 'motorway_link'}, 'road_motorway'),
        ({'railway': 'rail', 'service': 'yard'}, None),
        ({'railway': 'subway', 'tunnel': 'yes'}, 'subway_tunnel'),
        ({'railway': 'light_rail'}, 'light_rail'),
        ({'route': 'ferry'}, 'ferry'),
        ({'waterway': 'river'}, 'river'),
        ({'waterway': 'canal'}, 'river'),
        ({'natural': 'coastline'}, 'coastline'),
        ({'highway': 'footway'}, None),
    ],
)
def test_line_layer_rules(tags: Mapping[str, str], layer: str | None) -> None:
    """Each way's tags pick its line layer, or none."""
    assert line_layer(tags=tags) == layer


@pytest.mark.parametrize(
    ('tags', 'layer'),
    [
        ({'natural': 'water'}, 'water'),
        ({'waterway': 'riverbank'}, 'water'),
        ({'leisure': 'park'}, 'park'),
        ({'leisure': 'nature_reserve'}, 'park'),
        ({'leisure': 'golf_course'}, 'park'),
        ({'boundary': 'national_park'}, 'park'),
        ({'landuse': 'residential'}, None),
    ],
)
def test_area_layer_rules(tags: Mapping[str, str], layer: str | None) -> None:
    """Water and parks are drawn as areas; nothing else is."""
    assert area_layer(tags=tags) == layer


@pytest.mark.parametrize(
    ('tags', 'kind'),
    [
        ({'place': 'suburb'}, ('suburb', 0)),
        ({'place': 'town'}, ('town', 0)),
        ({'place': 'locality'}, ('locality', 0)),
        ({'place': 'village'}, ('village', 0)),
        ({'place': 'city'}, None),
        ({'railway': 'station'}, ('station', 0)),
        ({'railway': 'halt'}, ('station', 1)),
        ({'railway': 'tram_stop'}, ('station', 1)),
        ({'amenity': 'ferry_terminal'}, ('station', 1)),
        ({'amenity': 'cafe'}, None),
    ],
)
def test_place_kind_rules(
    tags: Mapping[str, str],
    kind: tuple[str, int] | None,
) -> None:
    """Places and stations get a kind and a rank."""
    assert place_kind(tags=tags) == kind


def test_read_osm_yields_features_in_bbox(
    features: list[OsmLine | OsmArea | OsmPlace],
) -> None:
    """The outside street and the yard rail line are skipped."""
    lines = {
        (item.layer, item.name)
        for item in features
        if isinstance(item, OsmLine)
    }
    places = {
        (item.kind, item.name, item.rank)
        for item in features
        if isinstance(item, OsmPlace)
    }
    assert lines == {
        ('road_minor', 'Hollis Street'),
        ('road_primary', 'Windsor Road'),
        ('rail', ''),
        ('coastline', ''),
    }
    assert places == {
        ('suburb', 'Constitution Hill', 0),
        ('station', 'Parramatta', 0),
    }


def test_read_osm_keeps_lon_lat(
    features: list[OsmLine | OsmArea | OsmPlace],
) -> None:
    """Lines keep their vertices as longitude and latitude."""
    street = next(
        item
        for item in features
        if isinstance(item, OsmLine) and item.name == 'Hollis Street'
    )
    assert street.lonlat.tolist() == [[150.995, -33.8], [151.005, -33.8]]


def test_closed_park_way_becomes_area(
    features: list[OsmLine | OsmArea | OsmPlace],
) -> None:
    """A closed park way is read as one park area with one ring."""
    areas = [item for item in features if isinstance(item, OsmArea)]
    assert [area.layer for area in areas] == ['park']
    (ring,) = areas[0].rings
    assert ring.shape == (5, 2)
    assert ring.min(axis=0).tolist() == [150.996, -33.796]


def test_header_timestamp_reads_replication_time(tiny_osm: Path) -> None:
    """The header's replication timestamp is returned as written."""
    assert header_timestamp(path=tiny_osm) == TIMESTAMP


def test_header_timestamp_is_empty_when_missing(tmp_path: Path) -> None:
    """A file without a replication timestamp gives an empty string."""
    path = tmp_path / 'bare.osm.pbf'
    with osmium.SimpleWriter(path) as writer:
        writer.add_node(Node(id=1, location=(151.0, -33.8)))
    assert not header_timestamp(path=path)
