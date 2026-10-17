"""Map features read from an OpenStreetMap PBF file."""

import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import osmium
from osmium.osm.types import OSMEntity

from paperroute.build.geometry import Line, Ring, wkb_lines, wkb_rings

logger = logging.getLogger(__name__)

ROAD_CLASSES: Final[Mapping[str, str]] = {
    'motorway': 'motorway',
    'motorway_link': 'motorway',
    'trunk': 'trunk',
    'trunk_link': 'trunk',
    'primary': 'primary',
    'primary_link': 'primary',
    'secondary': 'secondary',
    'secondary_link': 'secondary',
    'tertiary': 'tertiary',
    'tertiary_link': 'tertiary',
    'unclassified': 'minor',
    'residential': 'minor',
    'living_street': 'minor',
    'pedestrian': 'minor',
}
RAIL_KINDS: Final[frozenset[str]] = frozenset({'rail', 'light_rail', 'subway'})
RIVER_KINDS: Final[frozenset[str]] = frozenset({'river', 'canal'})
PARK_KINDS: Final[frozenset[str]] = frozenset(
    {'park', 'nature_reserve', 'golf_course'},
)
PLACE_KINDS: Final[frozenset[str]] = frozenset(
    {'suburb', 'town', 'locality', 'village'},
)
MINOR_STATIONS: Final[frozenset[str]] = frozenset({'halt', 'tram_stop'})
FILTER_KEYS: Final[tuple[str, ...]] = (
    'highway',
    'railway',
    'route',
    'natural',
    'waterway',
    'leisure',
    'boundary',
    'place',
    'amenity',
)


@dataclass
class Skips:
    """
    Counts of drawable objects dropped because osmium could not build them.

    Attributes
    ----------
    ways : int
        Ways with a missing node location or fewer than two points.
    areas : int
        Areas that failed to assemble.
    """

    ways: int = 0
    areas: int = 0


@dataclass(frozen=True)
class OsmLine:
    """
    A drawable way.

    Attributes
    ----------
    layer : str
        Layer name from `line_layer`.
    name : str
        The way's name, or an empty string.
    lonlat : Line
        Vertices as longitude and latitude.
    """

    layer: str
    name: str
    lonlat: Line


@dataclass(frozen=True)
class OsmArea:
    """
    A drawable area.

    Attributes
    ----------
    layer : str
        Layer name from `area_layer`.
    rings : tuple[Ring, ...]
        Exterior and interior rings in longitude and latitude, to fill
        even-odd.
    """

    layer: str
    rings: tuple[Ring, ...]


@dataclass(frozen=True)
class OsmPlace:
    """
    A named place or station.

    Attributes
    ----------
    kind : str
        Place kind, or ``station``.
    name : str
        The name.
    rank : int
        0 for suburbs and full stations, 1 for minor stops.
    lon : float
        Longitude.
    lat : float
        Latitude.
    """

    kind: str
    name: str
    rank: int
    lon: float
    lat: float


def line_layer(*, tags: Mapping[str, str]) -> str | None:
    """
    Pick the line layer for a way's tags.

    Parameters
    ----------
    tags : Mapping[str, str]
        The way's tags.

    Returns
    -------
    str | None
        Layer name, or None when the way is not drawn as a line.
    """
    highway = tags.get('highway', '')
    railway = tags.get('railway', '')
    if highway in ROAD_CLASSES:
        return f'road_{ROAD_CLASSES[highway]}'
    if railway in RAIL_KINDS and 'service' not in tags:
        tunnel = '_tunnel' if tags.get('tunnel') == 'yes' else ''
        return f'{railway}{tunnel}'
    if tags.get('route') == 'ferry':
        return 'ferry'
    if tags.get('natural') == 'coastline':
        return 'coastline'
    if tags.get('waterway') in RIVER_KINDS:
        return 'river'
    return None


def area_layer(*, tags: Mapping[str, str]) -> str | None:
    """
    Pick the area layer for an area's tags.

    Parameters
    ----------
    tags : Mapping[str, str]
        The area's tags.

    Returns
    -------
    str | None
        ``water``, ``park``, or None when the area is not drawn.
    """
    if tags.get('natural') == 'water' or tags.get('waterway') == 'riverbank':
        return 'water'
    if tags.get('leisure') in PARK_KINDS:
        return 'park'
    if tags.get('boundary') == 'national_park':
        return 'park'
    return None


def place_kind(*, tags: Mapping[str, str]) -> tuple[str, int] | None:
    """
    Pick the kind and rank of a named node.

    Parameters
    ----------
    tags : Mapping[str, str]
        The node's tags.

    Returns
    -------
    tuple[str, int] | None
        The kind and rank, or None when the node is not labelled.
    """
    place = tags.get('place', '')
    if place in PLACE_KINDS:
        return (place, 0)
    if tags.get('railway') == 'station':
        return ('station', 0)
    if tags.get('railway') in MINOR_STATIONS:
        return ('station', 1)
    if tags.get('amenity') == 'ferry_terminal':
        return ('station', 1)
    return None


def node_place(*, node: osmium.osm.Node) -> OsmPlace | None:
    """
    Read a node as a place.

    Parameters
    ----------
    node : osmium.osm.Node
        The node.

    Returns
    -------
    OsmPlace | None
        The place, or None when it is unnamed or not a place.
    """
    tags = dict(node.tags)
    kind = place_kind(tags=tags)
    if kind is None or not tags.get('name'):
        return None
    return OsmPlace(
        kind=kind[0],
        name=tags['name'],
        rank=kind[1],
        lon=node.location.lon,
        lat=node.location.lat,
    )


def way_line(
    *,
    way: osmium.osm.Way,
    factory: osmium.geom.WKBFactory,
    skips: Skips,
) -> OsmLine | None:
    """
    Read a way as a line.

    Parameters
    ----------
    way : osmium.osm.Way
        The way, with node locations.
    factory : osmium.geom.WKBFactory
        Geometry factory.
    skips : Skips
        Counts that a dropped way adds to.

    Returns
    -------
    OsmLine | None
        The line, or None when it is not drawn or has no usable
        geometry.
    """
    tags = dict(way.tags)
    layer = line_layer(tags=tags)
    if layer is None:
        return None
    try:
        wkb = bytes.fromhex(factory.create_linestring(way))
    except (osmium.InvalidLocationError, RuntimeError):
        skips.ways += 1
        return None
    (lonlat,) = wkb_lines(wkb=wkb)
    return OsmLine(layer=layer, name=tags.get('name', ''), lonlat=lonlat)


def area_rings(
    *,
    area: osmium.osm.Area,
    factory: osmium.geom.WKBFactory,
    skips: Skips,
) -> OsmArea | None:
    """
    Read an area as rings.

    Parameters
    ----------
    area : osmium.osm.Area
        The assembled area.
    factory : osmium.geom.WKBFactory
        Geometry factory.
    skips : Skips
        Counts that a failed area adds to.

    Returns
    -------
    OsmArea | None
        The area, or None when it is not drawn or fails to assemble.
    """
    layer = area_layer(tags=dict(area.tags))
    if layer is None:
        return None
    try:
        wkb = bytes.fromhex(factory.create_multipolygon(area))
    except (osmium.InvalidLocationError, RuntimeError):
        skips.areas += 1
        return None
    rings = wkb_rings(wkb=wkb)
    return OsmArea(layer=layer, rings=tuple(rings)) if rings else None


def to_feature(
    *,
    entity: OSMEntity,
    factory: osmium.geom.WKBFactory,
    skips: Skips,
) -> OsmLine | OsmArea | OsmPlace | None:
    """
    Read one OSM object as a feature.

    Parameters
    ----------
    entity : OSMEntity
        A node, way, relation, area or changeset.
    factory : osmium.geom.WKBFactory
        Geometry factory.
    skips : Skips
        Counts of objects osmium could not build.

    Returns
    -------
    OsmLine | OsmArea | OsmPlace | None
        The feature, or None when the object is not drawn.
    """
    if isinstance(entity, osmium.osm.Node):
        return node_place(node=entity)
    if isinstance(entity, osmium.osm.Way):
        return way_line(way=entity, factory=factory, skips=skips)
    if isinstance(entity, osmium.osm.Area):
        return area_rings(area=entity, factory=factory, skips=skips)
    return None


def overlaps(
    *,
    feature: OsmLine | OsmArea | OsmPlace,
    bbox: tuple[float, float, float, float],
) -> bool:
    """
    Check whether a feature's envelope meets a bounding box.

    Parameters
    ----------
    feature : OsmLine | OsmArea | OsmPlace
        The feature.
    bbox : tuple[float, float, float, float]
        West, south, east and north in degrees.

    Returns
    -------
    bool
        True when they overlap.
    """
    match feature:
        case OsmPlace():
            points = np.array([[feature.lon, feature.lat]])
        case OsmLine():
            points = feature.lonlat
        case OsmArea():
            points = np.concatenate(feature.rings)
    (west, south), (east, north) = points.min(axis=0), points.max(axis=0)
    return bool(
        west <= bbox[2]
        and east >= bbox[0]
        and south <= bbox[3]
        and north >= bbox[1],
    )


def read_osm(
    *,
    path: Path,
    bbox: tuple[float, float, float, float],
    index: str,
) -> Iterator[OsmLine | OsmArea | OsmPlace]:
    """
    Read the drawable features of an OSM file that meet a bounding box.

    Parameters
    ----------
    path : Path
        The ``.osm.pbf`` file. A missing file makes osmium raise
        RuntimeError, so callers check it exists first.
    bbox : tuple[float, float, float, float]
        West, south, east and north in degrees.
    index : str
        Osmium node location store, such as ``flex_mem``.

    Yields
    ------
    OsmLine | OsmArea | OsmPlace
        Each feature, in file order. Once the file is read, the counts of
        dropped ways and areas are logged.
    """
    processor = (
        osmium.FileProcessor(path)
        .with_locations(index)
        .with_areas()
        .with_filter(osmium.filter.KeyFilter(*FILTER_KEYS))
    )
    factory = osmium.geom.WKBFactory()
    skips = Skips()
    for entity in processor:
        feature = to_feature(entity=entity, factory=factory, skips=skips)
        if feature is not None and overlaps(feature=feature, bbox=bbox):
            yield feature
    logger.info('skipped %d ways without a usable line', skips.ways)
    logger.info('skipped %d areas that failed to assemble', skips.areas)


def header_timestamp(*, path: Path) -> str:
    """
    Read the replication time from an OSM file's header.

    Parameters
    ----------
    path : Path
        The ``.osm.pbf`` file.

    Returns
    -------
    str
        The ``osmosis_replication_timestamp``, or an empty string.
    """
    return osmium.FileProcessor(path).header.get(
        'osmosis_replication_timestamp',
        '',
    )
