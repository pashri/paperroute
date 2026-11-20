"""Rail paths over the track graph, and the stations along them."""

import heapq
import math
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from paperroute_transit.build.track_db import RailTrack
from paperroute_transit.core.geo import Point, project_onto_segment

NEAR: Final[float] = 60.0  # m
SNAP: Final[float] = 150.0  # m
CELL: Final[float] = 500.0  # m
WINDING: Final[float] = 1.6
SLACK: Final[float] = 2000.0  # m

Cell = tuple[int, int]


@dataclass(frozen=True)
class TrackGraph:
    """
    The track as a compressed sparse row graph with a grid of nodes.

    Attributes
    ----------
    starts : array[int]
        Node i's edges are ``targets[starts[i]:starts[i + 1]]``.
    targets : array[int]
        The node at the far end of each edge.
    lengths : array[float]
        The length of each edge in metres.
    xs : array[float]
        Metres east of the origin, one per node.
    ys : array[float]
        Metres north of the origin, one per node.
    grid : dict[tuple[int, int], array[int]]
        Node ids by ``CELL`` square.
    """

    starts: array[int]
    targets: array[int]
    lengths: array[float]
    xs: array[float]
    ys: array[float]
    grid: dict[Cell, array[int]]


def cell_of(*, point: Point) -> Cell:
    """
    Give the grid cell holding a point.

    Parameters
    ----------
    point : Point
        Metres east and north.

    Returns
    -------
    tuple[int, int]
        The cell's column and row.
    """
    return (math.floor(point[0] / CELL), math.floor(point[1] / CELL))


def graph_of(*, track: RailTrack) -> TrackGraph:
    """
    Build the graph of a track.

    Parameters
    ----------
    track : RailTrack
        The track.

    Returns
    -------
    TrackGraph
        Every edge in both directions, and the node grid.
    """
    count = len(track.xs)
    degree = [0] * (count + 1)
    for node in track.edges:
        degree[node + 1] += 1
    for node in range(count):
        degree[node + 1] += degree[node]
    fill = degree[:-1]
    targets = array('q', bytes(8 * len(track.edges)))
    lengths = array('d', bytes(8 * len(track.edges)))
    for a, b in zip(track.edges[0::2], track.edges[1::2], strict=True):
        span = math.hypot(
            track.xs[a] - track.xs[b],
            track.ys[a] - track.ys[b],
        )
        for src, dst in ((a, b), (b, a)):
            targets[fill[src]] = dst
            lengths[fill[src]] = span
            fill[src] += 1
    return TrackGraph(
        starts=array('q', degree),
        targets=targets,
        lengths=lengths,
        xs=track.xs,
        ys=track.ys,
        grid=node_grid(xs=track.xs, ys=track.ys),
    )


def node_grid(
    *,
    xs: array[float],
    ys: array[float],
) -> dict[Cell, array[int]]:
    """
    Bucket nodes into grid cells.

    Parameters
    ----------
    xs : array[float]
        Metres east, one per node.
    ys : array[float]
        Metres north, one per node.

    Returns
    -------
    dict[tuple[int, int], array[int]]
        Node ids by cell.
    """
    grid: dict[Cell, array[int]] = {}
    for node, point in enumerate(zip(xs, ys, strict=True)):
        grid.setdefault(cell_of(point=point), array('q')).append(node)
    return grid


def snapped(*, graph: TrackGraph, point: Point) -> tuple[int, ...]:
    """
    Find every node within ``SNAP`` of a point.

    Parameters
    ----------
    graph : TrackGraph
        The track graph.
    point : Point
        Metres east and north.

    Returns
    -------
    tuple[int, ...]
        Node ids; empty when the point is outside the track.
    """
    col, row = cell_of(point=point)
    found = [
        node
        for dc in (-1, 0, 1)
        for dr in (-1, 0, 1)
        for node in graph.grid.get((col + dc, row + dr), ())
        if math.dist(point, (graph.xs[node], graph.ys[node])) <= SNAP
    ]
    return tuple(sorted(found))


def rail_path(
    *,
    graph: TrackGraph,
    starts: Sequence[int],
    ends: Sequence[int],
    straight: float,
) -> tuple[int, ...] | None:
    """
    Find the shortest path from any start node to any end node.

    Parameters
    ----------
    graph : TrackGraph
        The track graph.
    starts : Sequence[int]
        Nodes the path may begin at.
    ends : Sequence[int]
        Nodes the path may finish at.
    straight : float
        The straight-line distance in metres between the two stations.

    Returns
    -------
    tuple[int, ...] | None
        The node ids, or None when no path is within ``WINDING`` times
        the straight line plus ``SLACK``.
    """
    limit = WINDING * straight + SLACK
    goals = set(ends)
    dist = {node: 0.0 for node in starts}
    prev: dict[int, int] = {}
    heap = [(0.0, node) for node in dist]
    heapq.heapify(heap)
    while heap:
        here, node = heapq.heappop(heap)
        if here > dist[node]:
            continue
        if node in goals:
            return trace(prev=prev, node=node)
        relax(
            graph=graph,
            node=node,
            here=here,
            limit=limit,
            dist=dist,
            prev=prev,
            heap=heap,
        )
    return None


def relax(
    *,
    graph: TrackGraph,
    node: int,
    here: float,
    limit: float,
    dist: dict[int, float],
    prev: dict[int, int],
    heap: list[tuple[float, int]],
) -> None:
    """
    Push the neighbours of a node that it reaches more cheaply.

    Parameters
    ----------
    graph : TrackGraph
        The track graph.
    node : int
        The node being left.
    here : float
        The distance to ``node`` in metres.
    limit : float
        Distances above this are not explored.
    dist : dict[int, float]
        Best distances so far; updated.
    prev : dict[int, int]
        Predecessors; updated.
    heap : list[tuple[float, int]]
        The queue; pushed to.
    """
    for i in range(graph.starts[node], graph.starts[node + 1]):
        there = here + graph.lengths[i]
        target = graph.targets[i]
        if there <= limit and there < dist.get(target, math.inf):
            dist[target] = there
            prev[target] = node
            heapq.heappush(heap, (there, target))


def trace(*, prev: Mapping[int, int], node: int) -> tuple[int, ...]:
    """
    Walk predecessors back to a start.

    Parameters
    ----------
    prev : Mapping[int, int]
        Predecessor of each reached node; starts are absent.
    node : int
        The node to walk back from.

    Returns
    -------
    tuple[int, ...]
        The nodes from the start to ``node``.
    """
    path = [node]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    return tuple(reversed(path))


def along(
    *,
    graph: TrackGraph,
    path: Sequence[int],
    points: Mapping[str, Point],
) -> tuple[str, ...]:
    """
    List the points within ``NEAR`` of a path, in order along it.

    Parameters
    ----------
    graph : TrackGraph
        The track graph.
    path : Sequence[int]
        Node ids.
    points : Mapping[str, Point]
        Candidate points by key.

    Returns
    -------
    tuple[str, ...]
        Keys ordered by metres along the path.
    """
    buckets: dict[Cell, list[str]] = {}
    for key, point in points.items():
        buckets.setdefault(cell_of(point=point), []).append(key)
    best: dict[str, float] = {}
    offset = 0.0
    for a, b in zip(path, path[1:]):
        start = (graph.xs[a], graph.ys[a])
        end = (graph.xs[b], graph.ys[b])
        for key in candidates(buckets=buckets, start=start, end=end):
            gap, metres = project_onto_segment(
                point=points[key],
                start=start,
                end=end,
            )
            if gap <= NEAR and offset + metres < best.get(key, math.inf):
                best[key] = offset + metres
        offset += math.dist(start, end)
    return tuple(sorted(best, key=lambda key: (best[key], key)))


def candidates(
    *,
    buckets: Mapping[Cell, Sequence[str]],
    start: Point,
    end: Point,
) -> list[str]:
    """
    List the keys in cells that a segment widened by ``NEAR`` covers.

    Parameters
    ----------
    buckets : Mapping[tuple[int, int], Sequence[str]]
        Point keys by cell.
    start : Point
        The segment start.
    end : Point
        The segment end.

    Returns
    -------
    list[str]
        The keys to test against the segment.
    """
    low = (min(start[0], end[0]) - NEAR, min(start[1], end[1]) - NEAR)
    high = (max(start[0], end[0]) + NEAR, max(start[1], end[1]) + NEAR)
    c0, r0 = cell_of(point=low)
    c1, r1 = cell_of(point=high)
    return [
        key
        for col in range(c0, c1 + 1)
        for row in range(r0, r1 + 1)
        for key in buckets.get((col, row), ())
    ]
