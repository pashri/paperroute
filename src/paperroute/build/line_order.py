"""Put a line's stations in one list, a block per branch."""

import math
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

type Pair = tuple[str, str]
type Graph = dict[str, dict[str, int]]
type Layout = dict[str, list[int]]


@dataclass
class Walk:
    """A depth-first walk: stations in visit order and who entered whom."""

    order: list[str] = field(default_factory=list)
    children: dict[str, list[str]] = field(default_factory=dict)
    depth: dict[str, int] = field(default_factory=dict)
    trips_in: dict[str, int] = field(default_factory=dict)
    tops: list[str] = field(default_factory=list)


def pair_of(*, first: str, second: str) -> Pair:
    """
    Key an undirected hop.

    Parameters
    ----------
    first : str
        One station ID.
    second : str
        The other station ID.

    Returns
    -------
    Pair
        The two IDs, smaller first.
    """
    return (first, second) if first <= second else (second, first)


def hop_trips(
    *, patterns: Sequence[tuple[Sequence[str], int]],
) -> Counter[Pair]:
    """
    Count the trips making each hop between neighbouring stations.

    Parameters
    ----------
    patterns : Sequence[tuple[Sequence[str], int]]
        Station sequence and trips of each pattern.

    Returns
    -------
    Counter[Pair]
        Trips by hop, either direction.
    """
    trips: Counter[Pair] = Counter()
    for stations, count in patterns:
        for first, second in pairwise(stations):
            if first != second:
                trips[pair_of(first=first, second=second)] += count
    return trips


def layout_of(*, stations: Sequence[str]) -> Layout:
    """
    Index where each station sits in a pattern.

    Parameters
    ----------
    stations : Sequence[str]
        Station sequence.

    Returns
    -------
    Layout
        Positions by station ID; a loop can visit a station twice.
    """
    places: Layout = {}
    for index, station in enumerate(stations):
        places.setdefault(station, []).append(index)
    return places


def is_shortcut(*, pair: Pair, layouts: Sequence[Layout]) -> bool:
    """
    Tell whether some pattern runs through stations between a hop's ends.

    Parameters
    ----------
    pair : Pair
        Hop.
    layouts : Sequence[Layout]
        Station positions of every pattern.

    Returns
    -------
    bool
        True when a pattern holding both ends has a station between them
        even where they sit closest. Measuring at the closest pair keeps
        a loop's real hops, where a station also sits far from its
        neighbours.
    """
    first, second = pair
    return any(
        min(
            abs(left - right)
            for left in places[first]
            for right in places[second]
        ) > 1
        for places in layouts
        if first in places and second in places
    )


def hop_graph(*, patterns: Sequence[tuple[Sequence[str], int]]) -> Graph:
    """
    Join the stations by the hops trains make, express shortcuts dropped.

    Parameters
    ----------
    patterns : Sequence[tuple[Sequence[str], int]]
        Station sequence and trips of each pattern.

    Returns
    -------
    Graph
        Trips of each kept hop, by station and neighbour; every station
        of the patterns is a key.
    """
    layouts = [layout_of(stations=stations) for stations, _ in patterns]
    graph: Graph = {
        station: {} for stations, _ in patterns for station in stations
    }
    for (first, second), count in hop_trips(patterns=patterns).items():
        if not is_shortcut(pair=(first, second), layouts=layouts):
            graph[first][second] = count
            graph[second][first] = count
    return graph


def top_of(
    *, graph: Graph, latitude: Mapping[str, float], seen: Mapping[str, int],
) -> str:
    """
    Pick where the next walk starts.

    Parameters
    ----------
    graph : Graph
        Hop graph.
    latitude : Mapping[str, float]
        Latitude by station ID; missing stations count as furthest south.
    seen : Mapping[str, int]
        Stations already walked, left out.

    Returns
    -------
    str
        The northernmost terminus (a station with one neighbour), or the
        northernmost station when there is none; ties go to the smallest
        ID.
    """
    left = [station for station in graph if station not in seen]
    ends = [station for station in left if len(graph[station]) == 1]
    return min(ends or left, key=lambda station: (
        -latitude.get(station, -math.inf), station,
    ))


def busiest_first(*, graph: Graph, station: str) -> Iterator[str]:
    """
    Give a station's neighbours, busiest hop first.

    Parameters
    ----------
    graph : Graph
        Hop graph.
    station : str
        Station ID.

    Returns
    -------
    Iterator[str]
        Neighbours by falling trips, ties by ID.
    """
    hops = graph[station]
    return iter(sorted(hops, key=lambda other: (-hops[other], other)))


def enter(*, walk: Walk, station: str, parent: str | None, trips: int) -> None:
    """
    Record a station as the walk reaches it.

    Parameters
    ----------
    walk : Walk
        Walk so far, changed in place.
    station : str
        Station reached.
    parent : str | None
        Station it was entered from; None for a top.
    trips : int
        Trips of the hop entered by.
    """
    walk.order.append(station)
    walk.children[station] = []
    walk.trips_in[station] = trips
    if parent is None:
        walk.depth[station] = 0
        walk.tops.append(station)
    else:
        walk.depth[station] = walk.depth[parent] + 1
        walk.children[parent].append(station)


def grow(*, walk: Walk, graph: Graph, top: str) -> None:
    """
    Walk depth first from a top, busiest hop first, each station once.

    Parameters
    ----------
    walk : Walk
        Walk so far, changed in place.
    graph : Graph
        Hop graph.
    top : str
        Station to start from.
    """
    enter(walk=walk, station=top, parent=None, trips=0)
    path = [(top, busiest_first(graph=graph, station=top))]
    while path:
        station, ahead = path[-1]
        step = next(
            (other for other in ahead if other not in walk.depth), None,
        )
        if step is None:
            path.pop()
            continue
        enter(walk=walk, station=step, parent=station,
              trips=graph[station][step])
        path.append((step, busiest_first(graph=graph, station=step)))


def heads_of(*, walk: Walk) -> dict[str, str]:
    """
    Find the station each block runs to.

    Parameters
    ----------
    walk : Walk
        Finished walk.

    Returns
    -------
    dict[str, str]
        Head by block-opening station: each top heads itself, and where
        the walk entered two or more ways from one station, each way's
        first station gets the furthest station walked from it (by hops,
        then by trips into it, then smallest ID).
    """
    sizes: dict[str, int] = {}
    for station in reversed(walk.order):
        sizes[station] = 1 + sum(sizes[c] for c in walk.children[station])
    where = {station: index for index, station in enumerate(walk.order)}
    heads = {top: top for top in walk.tops}
    for ways in walk.children.values():
        for way in ways if len(ways) > 1 else ():
            part = walk.order[where[way]:where[way] + sizes[way]]
            heads[way] = min(part, key=lambda station: (
                -walk.depth[station], -walk.trips_in[station], station,
            ))
    return heads


def line_order(
    *,
    patterns: Sequence[tuple[Sequence[str], int]],
    latitude: Mapping[str, float],
) -> tuple[tuple[str, str], ...]:
    """
    List a line's stations once each, a block per branch.

    The list starts at the northernmost terminus and walks the line,
    taking the busier way first at each split. Stations the hops leave
    apart from the rest follow in their own walk.

    Parameters
    ----------
    patterns : Sequence[tuple[Sequence[str], int]]
        Station sequence and trips of each of the line's patterns.
    latitude : Mapping[str, float]
        Latitude by station ID.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Station ID and head, in list order. The head is the station a
        block runs to, on the block's first row, else ''.
    """
    graph = hop_graph(patterns=patterns)
    walk = Walk()
    while len(walk.order) < len(graph):
        top = top_of(graph=graph, latitude=latitude, seen=walk.depth)
        grow(walk=walk, graph=graph, top=top)
    heads = heads_of(walk=walk)
    return tuple((station, heads.get(station, '')) for station in walk.order)
