"""Write a tiny OSM file around Constitution Hill for build tests."""

from collections.abc import Mapping
from pathlib import Path
from typing import Final

import osmium
from osmium.osm.mutable import Node, Relation, Way

WayRow = tuple[int, tuple[int, ...], Mapping[str, str]]
NodeRow = tuple[int, tuple[float, float], Mapping[str, str]]

TINY_BBOX: Final[tuple[float, float, float, float]] = (
    150.99,
    -33.81,
    151.01,
    -33.79,
)
TIMESTAMP: Final[str] = '2026-10-14T20:21:12Z'
NODES: Final[Mapping[int, tuple[float, float]]] = {
    1: (150.995, -33.800),
    2: (151.005, -33.800),
    3: (150.995, -33.802),
    4: (151.005, -33.802),
    5: (150.995, -33.804),
    6: (151.005, -33.804),
    7: (150.995, -33.806),
    8: (151.005, -33.806),
    9: (150.996, -33.796),
    10: (150.998, -33.796),
    11: (150.998, -33.794),
    12: (150.996, -33.794),
    13: (151.008, -33.809),
    14: (151.008, -33.791),
    15: (151.100, -33.800),
    16: (151.110, -33.800),
}
WAYS: Final[tuple[WayRow, ...]] = (
    (
        101,
        (1, 2),
        {'highway': 'residential', 'name': 'Hollis Street'},
    ),
    (102, (3, 4), {'highway': 'primary', 'name': 'Windsor Road'}),
    (103, (5, 6), {'railway': 'rail'}),
    (104, (7, 8), {'railway': 'rail', 'service': 'yard'}),
    (105, (9, 10, 11, 12, 9), {'leisure': 'park', 'name': 'Reserve'}),
    (106, (13, 14), {'natural': 'coastline'}),
    (107, (15, 16), {'highway': 'residential', 'name': 'Far Street'}),
    (108, (1, 999), {'highway': 'primary', 'name': 'Broken Road'}),
    (109, (2,), {'highway': 'primary', 'name': 'Stub Road'}),
    (110, (3, 4, 6, 5, 3), {'leisure': 'playground'}),
)
PLACES: Final[tuple[NodeRow, ...]] = (
    (201, (151.000, -33.797), {'place': 'suburb', 'name': 'Constitution Hill'}),
    (202, (151.002, -33.803), {'railway': 'station', 'name': 'Parramatta'}),
    (203, (151.000, -33.798), {'place': 'suburb'}),
)


def write_tiny_osm(*, path: Path) -> None:
    """
    Write a small OSM file with roads, rail, a park, coast and places.

    It also holds a street outside ``TINY_BBOX``, a yard rail line, a
    nameless suburb, a playground, a bus route and two ways that cannot
    become lines.

    Parameters
    ----------
    path : Path
        Destination ``.osm.pbf`` file; replaced if present.
    """
    header = osmium.io.Header()
    header.set('osmosis_replication_timestamp', TIMESTAMP)
    with osmium.SimpleWriter(path, header=header, overwrite=True) as writer:
        for node_id, location in NODES.items():
            writer.add_node(Node(id=node_id, location=location))
        for node_id, location, tags in PLACES:
            writer.add_node(Node(id=node_id, location=location, tags=tags))
        for way_id, nodes, tags in WAYS:
            writer.add_way(Way(id=way_id, nodes=list(nodes), tags=tags))
        writer.add_relation(
            Relation(id=301, members=[('w', 101, '')], tags={'route': 'bus'}),
        )
