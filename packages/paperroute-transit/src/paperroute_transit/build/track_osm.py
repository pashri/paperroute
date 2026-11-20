"""Read running rail lines and stations from OSM extracts."""

from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import osmium

from paperroute_transit.build.track_db import RailTrack, TrackStation
from paperroute_transit.core.geo import Projection, to_metres

NOT_RUNNING: Final[frozenset[str]] = frozenset({'siding', 'yard', 'spur'})
NOT_RAIL: Final[frozenset[str]] = frozenset(
    {'subway', 'light_rail', 'monorail', 'miniature', 'funicular'},
)
STATION_KINDS: Final[frozenset[str]] = frozenset({'station', 'halt'})
INDEX: Final[str] = 'flex_mem'

Spot = tuple[float, float]


def running_line(*, tags: Mapping[str, str]) -> bool:
    """
    Check whether a way's tags make a running rail line.

    Parameters
    ----------
    tags : Mapping[str, str]
        The way's tags.

    Returns
    -------
    bool
        True for ``railway=rail`` that is not a siding, yard or spur.
    """
    return (
        tags.get('railway') == 'rail'
        and tags.get('service') not in NOT_RUNNING
    )


def rail_station(*, tags: Mapping[str, str]) -> bool:
    """
    Check whether a node's tags make a named heavy-rail station.

    Parameters
    ----------
    tags : Mapping[str, str]
        The node's tags.

    Returns
    -------
    bool
        True for a named station or halt that is not a subway, light
        rail, monorail, miniature or funicular stop. A ``subway`` or
        ``light_rail`` tag of ``yes`` counts only with ``train=yes``.
    """
    if tags.get('railway') not in STATION_KINDS or not tags.get('name'):
        return False
    if tags.get('station') in NOT_RAIL:
        return False
    shared = 'yes' in (tags.get('subway'), tags.get('light_rail'))
    return not shared or tags.get('train') == 'yes'


@dataclass
class TrackParts:
    """
    What the files have given so far, each OSM object once.

    Attributes
    ----------
    spots : dict[int, tuple[float, float]]
        Longitude and latitude of each line node.
    ways : dict[int, tuple[int, ...]]
        Node ids of each running way.
    stations : dict[int, TrackStation]
        Stations by OSM node id.
    """

    spots: dict[int, Spot] = field(default_factory=dict)
    ways: dict[int, tuple[int, ...]] = field(default_factory=dict)
    stations: dict[int, TrackStation] = field(default_factory=dict)


class TrackHandler(osmium.SimpleHandler):
    """
    Collects running ways and station nodes into shared parts.

    Parameters
    ----------
    parts : TrackParts
        Where to put what is found. Objects already in it are skipped.
    projection : Projection
        Projects stations to metres.
    """

    def __init__(
        self,
        *,
        parts: TrackParts,
        projection: Projection,
    ) -> None:
        super().__init__()
        self.parts = parts
        self.projection = projection

    def node(self, n: osmium.osm.Node) -> None:
        """
        Keep a node that is a rail station.

        Parameters
        ----------
        n : osmium.osm.Node
            The node.
        """
        if n.id in self.parts.stations or not n.location.valid():
            return
        tags = dict(n.tags)
        if rail_station(tags=tags):
            x, y = to_metres(
                lon=n.location.lon,
                lat=n.location.lat,
                projection=self.projection,
            )
            self.parts.stations[n.id] = TrackStation(
                osm_id=n.id,
                name=tags['name'],
                lat=n.location.lat,
                lon=n.location.lon,
                x=x,
                y=y,
            )

    def way(self, w: osmium.osm.Way) -> None:
        """
        Keep a way that is a running line.

        Parameters
        ----------
        w : osmium.osm.Way
            The way, with node locations.
        """
        if w.id in self.parts.ways or not running_line(tags=dict(w.tags)):
            return
        refs = tuple(n.ref for n in w.nodes if n.location.valid())
        for node in w.nodes:
            if node.location.valid():
                self.parts.spots[node.ref] = (
                    node.location.lon,
                    node.location.lat,
                )
        self.parts.ways[w.id] = refs


def largest_part(*, ways: Sequence[tuple[int, ...]]) -> set[int]:
    """
    Find the node ids of the biggest connected set of lines.

    Parameters
    ----------
    ways : Sequence[tuple[int, ...]]
        Node ids along each way.

    Returns
    -------
    set[int]
        The node ids of the part with the most nodes.
    """
    parent: dict[int, int] = {}

    def find(node: int) -> int:
        """
        Find a node's set, compressing the path.

        Parameters
        ----------
        node : int
            The node id.

        Returns
        -------
        int
            The id standing for the node's set.
        """
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for refs in ways:
        for a, b in zip(refs, refs[1:]):
            parent[find(a)] = find(b)
    parts: dict[int, set[int]] = {}
    for node in list(parent):
        parts.setdefault(find(node), set()).add(node)
    return max(parts.values(), key=len, default=set())


def build_track(
    *,
    parts: TrackParts,
    projection: Projection,
) -> RailTrack:
    """
    Turn the collected parts into a track of the largest connected part.

    Parameters
    ----------
    parts : TrackParts
        The ways, nodes and stations read.
    projection : Projection
        Projects nodes to metres.

    Returns
    -------
    RailTrack
        Dense node ids, in order of first use along the ways.
    """
    keep = largest_part(ways=list(parts.ways.values()))
    ids: dict[int, int] = {}
    xs: array[float] = array('d')
    ys: array[float] = array('d')
    edges: array[int] = array('l')
    for refs in parts.ways.values():
        for ref in refs:
            if ref in keep and ref not in ids:
                ids[ref] = len(ids)
                lon, lat = parts.spots[ref]
                x, y = to_metres(lon=lon, lat=lat, projection=projection)
                xs.append(x)
                ys.append(y)
        for a, b in zip(refs, refs[1:]):
            if a in keep and a != b:
                edges.extend((ids[a], ids[b]))
    return RailTrack(
        xs=xs,
        ys=ys,
        edges=edges,
        stations=tuple(parts.stations.values()),
    )


def read_track(
    *,
    paths: Sequence[Path],
    projection: Projection,
) -> RailTrack:
    """
    Read the running rail track and stations from OSM extracts.

    Parameters
    ----------
    paths : Sequence[Path]
        The ``.osm.pbf`` files. An object in more than one file counts
        once. Stations are read from nodes only.
    projection : Projection
        The city's projection; coordinates are metres in it.

    Returns
    -------
    RailTrack
        The largest connected part of the running lines, and every
        named station.
    """
    parts = TrackParts()
    for path in paths:
        TrackHandler(parts=parts, projection=projection).apply_file(
            str(path),
            locations=True,
            idx=INDEX,
        )
    return build_track(parts=parts, projection=projection)
