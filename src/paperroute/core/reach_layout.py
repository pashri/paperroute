"""Lay a strip's stations out in lines and indented spurs."""

import math
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import groupby, pairwise
from typing import Final

from paperroute.core.model import StopPlace
from paperroute.core.reach_hops import drawn_through, hop_ways

FROM_KEY: Final[str] = ''  # stands for the From in a way's track graph
SETTLE_PASSES: Final[int] = 50
SIDES: Final[int] = 2

type Trail = tuple[tuple[str, ...], int]
type Owners = dict[str, str]
type Came = tuple[str, str, int]
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
class LaidWay:
    """
    One way out of the From, laid out.

    Attributes
    ----------
    hop : str
        The way's first hop.
    hung : Hung
        Branches by junction; the From's first is the main chain.
    """

    hop: str
    hung: Hung


@dataclass(frozen=True)
class Sides:
    """
    The rows either side of the From.

    Attributes
    ----------
    above : tuple[Entry, ...]
        Rows above the From, top to bottom.
    below : tuple[Entry, ...]
        Rows below the From, top to bottom.
    """

    above: tuple[Entry, ...]
    below: tuple[Entry, ...]


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


@dataclass(frozen=True)
class Entry:
    """
    A row before track parents become row indexes.

    Attributes
    ----------
    key : str
        Station ID, or FROM_KEY for the From.
    depth : int
        Indent.
    above : bool
        True above the From.
    parent : str | None
        Station the track comes from, or None for the From.
    """

    key: str
    depth: int
    above: bool
    parent: str | None


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


def nearest_first(*, trails: Sequence[Trail]) -> list[str]:
    """
    Order stations by their fewest stops from the From, then by ID.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, per tail.

    Returns
    -------
    list[str]
        Every station once.
    """
    near: dict[str, int] = {}
    for stations, _ in trails:
        for stops, key in enumerate(stations):
            near[key] = min(near.get(key, stops), stops)
    return sorted(near, key=lambda key: (near[key], key))


def arrivals(*, trails: Sequence[Trail], hops: Owners
             ) -> dict[str, list[Came]]:
    """
    List where each station's trains come from.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.
    hops : Owners
        Way by first hop.

    Returns
    -------
    dict[str, list[Came]]
        By station, the previous station, the tail's way and the trips of
        every tail past it but the first hops, heaviest first.
    """
    came: dict[str, list[Came]] = defaultdict(list)
    for stations, trips in trails:
        for back, key in pairwise(stations):
            came[key].append((back, hops[stations[0]], trips))
    return came


def inherit(*, came: Sequence[Came], owner: Owners) -> str:
    """
    Pick the way most of a station's trains come from.

    Parameters
    ----------
    came : Sequence[Came]
        Previous station, way and trips, heaviest first.
    owner : Owners
        Way of each station settled so far.

    Returns
    -------
    str
        The way: the trip-weighted way of the stations it is reached
        from, ties to the heaviest tail's; with none settled, the way of
        the heaviest tail.
    """
    votes: Counter[str] = Counter()
    for back, _, trips in came:
        if back in owner:
            votes[owner[back]] += trips
    return votes.most_common(1)[0][0] if votes else came[0][1]


def inherited(*, trails: Sequence[Trail], came: Mapping[str, list[Came]],
              hops: Owners) -> Owners:
    """
    Put each station on the way of the stations its trains come from.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.
    came : Mapping[str, list[Came]]
        Where each station's trains come from.
    hops : Owners
        Way by first hop.

    Returns
    -------
    Owners
        Way (a first hop) by station. A first hop takes its own way; the
        rest are settled nearest first.
    """
    owner = dict(hops)
    for key in nearest_first(trails=trails):
        if key not in owner:
            owner[key] = inherit(came=came[key], owner=owner)
    return owner


def strays(*, trails: Sequence[Trail], owner: Owners) -> set[str]:
    """
    Find stations their way can't reach without leaving its stations.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips.
    owner : Owners
        Way by station; a tail's way is its first hop's.

    Returns
    -------
    set[str]
        Stations no tail of their way reaches through its own stations.
    """
    reached: set[str] = set()
    for stations, _ in trails:
        for key in stations:
            if owner[key] != owner[stations[0]]:
                break
            reached.add(key)
    return set(owner) - reached


def ballots(*, trails: Sequence[Trail], owner: Owners, stray: set[str]
            ) -> dict[str, Counter[str]]:
    """
    Count the trips reaching each stray through one way's stations.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.
    owner : Owners
        Way by station.
    stray : set[str]
        Stations to move.

    Returns
    -------
    dict[str, Counter[str]]
        Trips by stray and way. Strays count as any way's stations.
    """
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for stations, trips in trails:
        for key in stations:
            if key in stray:
                votes[key][owner[stations[0]]] += trips
            elif owner[key] != owner[stations[0]]:
                break
    return votes


def settle_pass(*, trails: Sequence[Trail], owner: Owners,
                came: Mapping[str, list[Came]]) -> bool:
    """
    Move each stray to the way most of its trips reach it through.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.
    owner : Owners
        Way by station; updated in place.
    came : Mapping[str, list[Came]]
        Where each station's trains come from.

    Returns
    -------
    bool
        True when a station moved.
    """
    stray = strays(trails=trails, owner=owner)
    votes = ballots(trails=trails, owner=owner, stray=stray)
    moves = {
        key: (
            votes[key].most_common(1)[0][0] if votes[key]
            else owner[came[key][0][0]]
        )
        for key in sorted(stray)
    }
    moved = any(owner[key] != way for key, way in moves.items())
    owner.update(moves)
    return moved


def ways_of(*, trails: Sequence[Trail], hops: Owners) -> Owners:
    """
    Split stations into ways by following the trains in (rule D).

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.
    hops : Owners
        Way by first hop.

    Returns
    -------
    Owners
        Way (a first hop) by station, settled so that each way reaches
        its stations through its own stations where any tail does. First
        hops riding the same line share one way.
    """
    came = arrivals(trails=trails, hops=hops)
    owner = inherited(trails=trails, came=came, hops=hops)
    for _ in range(SETTLE_PASSES):
        if not settle_pass(trails=trails, owner=owner, came=came):
            break
    return owner


def way_runs(*, trails: Sequence[Trail], owner: Owners
             ) -> dict[str, list[Run]]:
    """
    Split each tail into runs of stations on one way.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.
    owner : Owners
        Way by station.

    Returns
    -------
    dict[str, list[Run]]
        Runs by way, heaviest first. Only a tail's first run is joined to
        the From.
    """
    ways: dict[str, list[Run]] = defaultdict(list)
    for stations, trips in trails:
        for index, (way, run) in enumerate(
            groupby(stations, key=owner.__getitem__),
        ):
            ways[way].append(
                Run(stations=tuple(run), trips=trips, joined=index == 0),
            )
    return ways


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


def lay_ways(*, trails: Sequence[Trail], places: Mapping[str, StopPlace]
             ) -> list[LaidWay]:
    """
    Split the tails into ways and lay each out, heaviest first.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From under FROM_KEY.

    Returns
    -------
    list[LaidWay]
        Ways by trips, then by first hop. The first two have main chains
        at depth 0; the rest at depth 1.
    """
    hops = hop_ways(trails=trails, places=places, origin=FROM_KEY)
    ways = way_runs(trails=trails, owner=ways_of(trails=trails, hops=hops))
    through = drawn_through(trails=trails, way=hops)
    order = sorted(ways, key=lambda way: (
        -sum(run.trips for run in ways[way]), way,
    ))
    return [
        LaidWay(hop=way, hung=lay_way(
            runs=ways[way],
            places=places,
            depth=0 if rank < SIDES else 1,
            through=through,
        ))
        for rank, way in enumerate(order)
    ]


def travel(*, hung: Hung, branch: Branch, above: bool) -> Iterator[Entry]:
    """
    List a branch's rows in travel order, each followed by its branches.

    Parameters
    ----------
    hung : Hung
        Branches by junction.
    branch : Branch
        The branch.
    above : bool
        True above the From.

    Yields
    ------
    Entry
        Rows in travel order.
    """
    for back, key in zip((branch.junction, *branch.stations),
                         branch.stations):
        yield Entry(
            key=key,
            depth=branch.depth,
            above=above,
            parent=back,
        )
        for child in hung.get(key, ()):
            yield from travel(hung=hung, branch=child, above=above)


def side_rows(*, hung: Hung, above: bool) -> list[Entry]:
    """
    List a side's rows in travel order.

    Parameters
    ----------
    hung : Hung
        Branches by junction; the From's first is the main chain.
    above : bool
        True above the From.

    Returns
    -------
    list[Entry]
        The From's other branches, heaviest first, then the main chain.
    """
    main, *rest = hung[FROM_KEY]
    pieces = sorted(rest, key=lambda branch: -branch.weight)
    return [
        entry
        for branch in (*pieces, main)
        for entry in travel(hung=hung, branch=branch, above=above)
    ]


def side_rank(*, way: LaidWay, places: Mapping[str, StopPlace]
              ) -> tuple[int, int, str]:
    """
    Rank a side for the top: more spur rows, more stations, then name.

    Parameters
    ----------
    way : LaidWay
        The side.
    places : Mapping[str, StopPlace]
        Every station by ID.

    Returns
    -------
    tuple[int, int, str]
        A key that sorts the top side first; the name is the first hop's.
    """
    branches = [branch for found in way.hung.values() for branch in found]
    spurs = sum(len(b.stations) for b in branches if b.depth > 0)
    stations = sum(len(branch.stations) for branch in branches)
    return -spurs, -stations, place_of(places=places, key=way.hop).name


def with_spurs(*, side: Hung, others: Sequence[LaidWay]) -> Hung:
    """
    Hang further ways at the From of a side.

    Parameters
    ----------
    side : Hung
        The side's branches by junction.
    others : Sequence[LaidWay]
        Further ways, laid out at depth 1.

    Returns
    -------
    Hung
        The side with every further way's branches added.
    """
    merged = dict(side)
    for other in others:
        merged |= other.hung
    merged[FROM_KEY] = [
        *side[FROM_KEY],
        *(branch for other in others for branch in other.hung[FROM_KEY]),
    ]
    return merged


def lay_out(*, trails: Sequence[Trail], places: Mapping[str, StopPlace]
            ) -> Sides:
    """
    Lay the stations out either side of the From.

    The two busiest ways out of the From are its two sides; the side with
    more spur rows, then more stations, goes on top, listed against travel
    order. A further way is a depth-1 spur at the From on the top side.
    One way goes below.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first; none empty.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From under FROM_KEY.

    Returns
    -------
    Sides
        Rows above and below the From.
    """
    ways = lay_ways(trails=trails, places=places)
    if len(ways) < SIDES:
        below = side_rows(hung=ways[0].hung, above=False) if ways else []
        return Sides(above=(), below=tuple(below))
    top, bottom = sorted(ways[:SIDES], key=lambda way: side_rank(
        way=way, places=places,
    ))
    hung = with_spurs(side=top.hung, others=ways[SIDES:])
    return Sides(
        above=tuple(reversed(side_rows(hung=hung, above=True))),
        below=tuple(side_rows(hung=bottom.hung, above=False)),
    )
