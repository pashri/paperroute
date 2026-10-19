"""Lay one way out of a strip's From as a main chain and spurs."""

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Final

from paperroute.core.model import StopPlace

FROM_KEY: Final[str] = ''  # stands for the From in a way's track graph

type Hung = dict[str, list[Branch]]

@dataclass(frozen=True)
class Branch:
    """
    A run of stations laid out in a line from a junction.

    Attributes
    ----------
    junction : str
        Station the branch leaves, or FROM_KEY.
    stations : tuple[str, ...]
        Stations in travel order.
    depth : int
        Indent of every station on the branch.
    weight : int
        Trips from the junction to the first station.
    """

    junction: str
    stations: tuple[str, ...]
    depth: int
    weight: int

@dataclass(frozen=True)
class Run:
    """
    Stations of one tail in a row on one way.

    Attributes
    ----------
    stations : tuple[str, ...]
        Stations in travel order.
    trips : int
        The tail's trips.
    joined : bool
        True when the run is drawn leaving the From.
    """

    stations: tuple[str, ...]
    trips: int
    joined: bool

@dataclass(frozen=True)
class WayGraph:
    """
    The track graph of one way out of the From.

    Attributes
    ----------
    succ : Mapping[str, Counter[str]]
        Trips on each edge, by station then next station.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From under FROM_KEY.
    """

    succ: Mapping[str, Counter[str]]
    places: Mapping[str, StopPlace]

@dataclass
class Layout:
    """
    One way's layout while it is built.

    Attributes
    ----------
    graph : WayGraph
        The way's track graph.
    placed : set[str]
        Stations laid out, and FROM_KEY.
    depth : dict[str, int]
        Indent of each placed station; FROM_KEY is 0.
    hung : Hung
        Branches by junction, in the order laid out.
    """

    graph: WayGraph
    placed: set[str]
    depth: dict[str, int]
    hung: Hung

def place_of(*, places: Mapping[str, StopPlace], key: str) -> StopPlace:
    """
    Find a station's place, or a bare one named by its ID.

    Parameters
    ----------
    places : Mapping[str, StopPlace]
        Places by ID.
    key : str
        Station ID.

    Returns
    -------
    StopPlace
        The place.
    """
    found = places.get(key)
    return (
        StopPlace(stop_id=key, name=key, point=None)
        if found is None
        else found
    )

def track(*, run: Run) -> tuple[str, ...]:
    """
    List a run's stations, led by FROM_KEY when it leaves the From.

    Parameters
    ----------
    run : Run
        The run.

    Returns
    -------
    tuple[str, ...]
        Stations along the run's track.
    """
    return (FROM_KEY, *run.stations) if run.joined else run.stations


def edge_trips(*, runs: Sequence[Run]) -> dict[str, Counter[str]]:
    """
    Weight every edge of the runs by their trips.

    Parameters
    ----------
    runs : Sequence[Run]
        Runs of one way.

    Returns
    -------
    dict[str, Counter[str]]
        Trips by station and next station; the From is FROM_KEY.
    """
    succ: dict[str, Counter[str]] = defaultdict(Counter)
    for run in runs:
        for here, there in pairwise(track(run=run)):
            succ[here][there] += run.trips
    return succ


def closure(*, succ: Mapping[str, Counter[str]], key: str,
            placed: set[str]) -> set[str]:
    """
    Find a station and everything after it not yet laid out.

    Parameters
    ----------
    succ : Mapping[str, Counter[str]]
        Trips by station and next station.
    key : str
        Station ID.
    placed : set[str]
        Stations already laid out, which the search does not enter.

    Returns
    -------
    set[str]
        The station and every station reachable from it without passing
        a laid-out one.
    """
    seen, todo = {key}, [key]
    while todo:
        fresh = succ.get(todo.pop(), Counter()).keys() - seen - placed
        seen |= fresh
        todo.extend(fresh)
    return seen


def joined(*, runs: Sequence[Run]) -> list[Run]:
    """
    Join to the From each run the rest of its way never reaches.

    A run that starts away from the From joins its way at the stations
    before its first, when a run reaches that first station from the From
    through the way's stations. Only otherwise is it drawn leaving the
    From.

    Parameters
    ----------
    runs : Sequence[Run]
        Runs of one way.

    Returns
    -------
    list[Run]
        The runs, with the unreachable ones joined to the From.
    """
    reached = closure(
        succ=edge_trips(runs=runs), key=FROM_KEY, placed=set(),
    )
    return [
        run if run.stations[0] in reached else replace(run, joined=True)
        for run in runs
    ]


def drawn(*, runs: Sequence[Run], through: set[str]) -> list[Run]:
    """
    Draw runs from the line that passes their first hop, where it can.

    Parameters
    ----------
    runs : Sequence[Run]
        Runs of one way.
    through : set[str]
        First hops to draw on another hop's line.

    Returns
    -------
    list[Run]
        The runs. One starting at a hop in through no longer leaves the
        From when the way's other runs reach that hop from the From.
    """
    loose = [
        replace(run, joined=False) if run.stations[0] in through else run
        for run in runs
    ]
    reached = closure(
        succ=edge_trips(runs=loose), key=FROM_KEY, placed=set(),
    )
    return [
        free if free.stations[0] in reached else run
        for run, free in zip(runs, loose)
    ]


def furthest(*, graph: WayGraph, here: str, key: str,
             placed: set[str]) -> float:
    """
    Measure how far a branch reaches from a station in a straight line.

    Parameters
    ----------
    graph : WayGraph
        Track graph.
    here : str
        Station the branch leaves.
    key : str
        First station of the branch.
    placed : set[str]
        Stations already laid out, which the branch can't reach through.

    Returns
    -------
    float
        Metres to the furthest of the branch's first station and the
        stations after it not yet laid out; 0 when positions are missing.
    """
    start = place_of(places=graph.places, key=here).point
    if start is None:
        return 0.0
    points = (
        place_of(places=graph.places, key=far).point
        for far in closure(succ=graph.succ, key=key, placed=placed)
    )
    return max(
        (math.dist(start, point) for point in points if point is not None),
        default=0.0,
    )


def next_step(*, graph: WayGraph, here: str, placed: set[str]
              ) -> str | None:
    """
    Pick the busiest next station not yet laid out.

    Parameters
    ----------
    graph : WayGraph
        Track graph.
    here : str
        Station to step from.
    placed : set[str]
        Stations already laid out.

    Returns
    -------
    str | None
        The next station with the most trips; ties go to the one whose
        stations reach furthest, then to the lower name. None when every
        next station is laid out.
    """
    options = {
        there: trips
        for there, trips in graph.succ.get(here, Counter()).items()
        if there not in placed
    }
    if not options:
        return None
    most = max(options.values())
    tied = [there for there, trips in options.items() if trips == most]
    return tied[0] if len(tied) == 1 else min(tied, key=lambda there: (
        -furthest(graph=graph, here=here, key=there, placed=placed),
        place_of(places=graph.places, key=there).name,
        there,
    ))


def walk(*, graph: WayGraph, start: str, placed: set[str]
         ) -> tuple[str, ...]:
    """
    Follow the busiest edges from a station over stations not laid out.

    Parameters
    ----------
    graph : WayGraph
        Track graph.
    start : str
        Station to leave.
    placed : set[str]
        Stations already laid out; the walk's are added.

    Returns
    -------
    tuple[str, ...]
        Stations stepped to, in order.
    """
    chain: list[str] = []
    here: str | None = next_step(graph=graph, here=start, placed=placed)
    while here is not None:
        chain.append(here)
        placed.add(here)
        here = next_step(graph=graph, here=here, placed=placed)
    return tuple(chain)


def grow(*, layout: Layout, junction: str, depth: int) -> bool:
    """
    Lay out one branch from a junction.

    Parameters
    ----------
    layout : Layout
        The way so far; the branch is added.
    junction : str
        Station the branch leaves.
    depth : int
        The branch's indent.

    Returns
    -------
    bool
        True when the junction had a station left to lay out.
    """
    stations = walk(graph=layout.graph, start=junction, placed=layout.placed)
    if not stations:
        return False
    layout.hung[junction].append(Branch(
        junction=junction,
        stations=stations,
        depth=depth,
        weight=layout.graph.succ[junction][stations[0]],
    ))
    layout.depth.update(dict.fromkeys(stations, depth))
    return True


def junctions(*, runs: Sequence[Run], placed: set[str]) -> list[str]:
    """
    Find where each run of stations not laid out leaves the laid ones.

    Parameters
    ----------
    runs : Sequence[Run]
        Runs of one way, heaviest first.
    placed : set[str]
        Stations already laid out, and FROM_KEY.

    Returns
    -------
    list[str]
        Laid-out stations followed by one not laid out, in the order the
        heaviest tails reach them.
    """
    found: dict[str, None] = {}
    for run in runs:
        for here, there in pairwise(track(run=run)):
            if here in placed and there not in placed:
                found[here] = None
    return list(found)


def lay_way(*, runs: Sequence[Run], places: Mapping[str, StopPlace],
            depth: int, through: set[str]) -> Hung:
    """
    Lay a way out: a main chain, then every other run hung, indented.

    Parameters
    ----------
    runs : Sequence[Run]
        The way's runs, heaviest first.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From under FROM_KEY.
    depth : int
        Indent of the way's main chain.
    through : set[str]
        First hops to draw on another hop's line.

    Returns
    -------
    Hung
        Branches by junction; the From's first is the main chain. Other
        branches at the From have depth 1.
    """
    runs = joined(runs=drawn(runs=runs, through=through))
    layout = Layout(
        graph=WayGraph(succ=edge_trips(runs=runs), places=places),
        placed={FROM_KEY}, depth={FROM_KEY: 0}, hung=defaultdict(list),
    )
    grow(layout=layout, junction=FROM_KEY, depth=depth)
    while hanging := junctions(runs=runs, placed=layout.placed):
        for junction in hanging:
            below = layout.depth[junction] + 1
            while grow(layout=layout, junction=junction, depth=below):
                continue
    return layout.hung
