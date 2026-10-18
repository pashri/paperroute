"""The stations a strip reaches from its From."""

import math
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import groupby, pairwise
from typing import Final

from paperroute.core.model import RouteKind, StopPlace

FROM_KEY: Final[str] = ''  # stands for the From in a way's track graph
SETTLE_PASSES: Final[int] = 50
SIDES: Final[int] = 2
APPROACH_ROWS: Final[int] = 2

type Trail = tuple[tuple[str, ...], int]
type Owners = dict[str, str]
type Came = tuple[str, str, int]
type Hung = dict[str, list[Branch]]


@dataclass(frozen=True)
class RunTail:
    """
    One way through the From: the stations before it and after it.

    Attributes
    ----------
    before : tuple[str, ...]
        Station IDs called at before the From, nearest last.
    after : tuple[str, ...]
        Station IDs called at after the From, nearest first.
    trips : int
        Trip-days that run this way.
    """

    before: tuple[str, ...]
    after: tuple[str, ...]
    trips: int


@dataclass(frozen=True)
class ReachInput:
    """
    Everything needed to lay out a strip from its From.

    Attributes
    ----------
    origin : StopPlace
        The From station.
    tails : tuple[RunTail, ...]
        The ways through the From.
    places : Mapping[str, StopPlace]
        Every station named in the tails, by ID.
    station_of : Mapping[str, str]
        The station ID of each stop under a station in the tails, and of
        each such station itself.
    """

    origin: StopPlace
    tails: tuple[RunTail, ...]
    places: Mapping[str, StopPlace]
    station_of: Mapping[str, str]


@dataclass(frozen=True)
class StripKey:
    """
    Which runs feed a strip.

    Attributes
    ----------
    station : str
        From stop or station ID.
    kind : RouteKind | None
        Kind of route, or None for every run.
    routes : tuple[str, ...]
        Route short names chosen.
    """

    station: str
    kind: RouteKind | None
    routes: tuple[str, ...]


class RowKind(StrEnum):
    """What a strip row shows."""

    APPROACH = 'approach'
    ORIGIN = 'origin'
    MAIN = 'main'
    BRANCH = 'branch'


@dataclass(frozen=True)
class ReachRow:
    """
    One station on a strip.

    Attributes
    ----------
    place : StopPlace
        The station.
    kind : RowKind
        Approach, the From, the main line or a branch.
    depth : int
        Indent: 0 for the main line, one more per branch level.
    above : bool
        True when the row is above the From.
    parent : int | None
        Index of the row the track comes from: the previous station on
        the row's branch, or its junction for a branch's first row. An
        approach row's is the row below it; None for the From.
    """

    place: StopPlace
    kind: RowKind
    depth: int
    above: bool
    parent: int | None


@dataclass(frozen=True)
class Reach:
    """
    The rows of a strip, top to bottom.

    Attributes
    ----------
    rows : tuple[ReachRow, ...]
        Rows, top to bottom, with exactly one From row.
    stations : int
        Rows that are neither the From nor an approach.
    station_of : Mapping[str, str]
        The station ID of each stop under a station on the strip.
    """

    rows: tuple[ReachRow, ...]
    stations: int
    station_of: Mapping[str, str]


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
class WayGraph:
    """
    The track graph of one way out of the From.

    Attributes
    ----------
    succ : Mapping[str, Counter[str]]
        Trips on each edge, by station then next station.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From under FROM_KEY.
    closures : dict[str, frozenset[str]]
        Stations found so far with everything after them.
    """

    succ: Mapping[str, Counter[str]]
    places: Mapping[str, StopPlace]
    closures: dict[str, frozenset[str]] = field(default_factory=dict)


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
    kind : RowKind
        What the row shows.
    depth : int
        Indent.
    above : bool
        True above the From.
    parent : str | None
        Station the track comes from, or None for the From.
    """

    key: str
    kind: RowKind
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


def arrivals(*, trails: Sequence[Trail]) -> dict[str, list[Came]]:
    """
    List where each station's trains come from.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.

    Returns
    -------
    dict[str, list[Came]]
        By station, the previous station, the first hop and the trips of
        every tail past it but the first hops, heaviest first.
    """
    came: dict[str, list[Came]] = defaultdict(list)
    for stations, trips in trails:
        for back, key in pairwise(stations):
            came[key].append((back, stations[0], trips))
    return came


def inherit(*, came: Sequence[Came], owner: Owners) -> str:
    """
    Pick the way most of a station's trains come from.

    Parameters
    ----------
    came : Sequence[Came]
        Previous station, first hop and trips, heaviest first.
    owner : Owners
        Way of each station settled so far.

    Returns
    -------
    str
        The way: the trip-weighted way of the stations it is reached
        from, ties to the heaviest tail's; with none settled, the first
        hop of the heaviest tail.
    """
    votes: Counter[str] = Counter()
    for back, _, trips in came:
        if back in owner:
            votes[owner[back]] += trips
    return votes.most_common(1)[0][0] if votes else came[0][1]


def inherited(*, trails: Sequence[Trail], came: Mapping[str, list[Came]]
              ) -> Owners:
    """
    Put each station on the way of the stations its trains come from.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.
    came : Mapping[str, list[Came]]
        Where each station's trains come from.

    Returns
    -------
    Owners
        Way (first hop) by station. A first hop owns itself; the rest are
        settled nearest first.
    """
    owner = {stations[0]: stations[0] for stations, _ in trails}
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
        Way by station.

    Returns
    -------
    set[str]
        Stations no tail of their way reaches through its own stations.
    """
    reached: set[str] = set()
    for stations, _ in trails:
        for key in stations:
            if owner[key] != stations[0]:
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
                votes[key][stations[0]] += trips
            elif owner[key] != stations[0]:
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


def ways_of(*, trails: Sequence[Trail]) -> Owners:
    """
    Split stations into ways by following the trains in (rule D).

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first.

    Returns
    -------
    Owners
        Way (first hop) by station, settled so that each way reaches its
        stations through its own stations where any tail does.
    """
    came = arrivals(trails=trails)
    owner = inherited(trails=trails, came=came)
    for _ in range(SETTLE_PASSES):
        if not settle_pass(trails=trails, owner=owner, came=came):
            break
    return owner


def way_trails(*, trails: Sequence[Trail], owner: Owners
               ) -> dict[str, list[Trail]]:
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
    dict[str, list[Trail]]
        Runs by way, heaviest first, each read as leaving the From. A run
        that starts away from the From joins its way where the way already
        has its first station, or else at the From.
    """
    ways: dict[str, list[Trail]] = defaultdict(list)
    for stations, trips in trails:
        for way, run in groupby(stations, key=owner.__getitem__):
            ways[way].append((tuple(run), trips))
    return ways


def edge_trips(*, trails: Sequence[Trail]) -> dict[str, Counter[str]]:
    """
    Weight every edge of the tails by their trips.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips.

    Returns
    -------
    dict[str, Counter[str]]
        Trips by station and next station; the From is FROM_KEY.
    """
    succ: dict[str, Counter[str]] = defaultdict(Counter)
    for stations, trips in trails:
        for here, there in pairwise((FROM_KEY, *stations)):
            succ[here][there] += trips
    return succ


def closure(*, graph: WayGraph, key: str) -> frozenset[str]:
    """
    Find a station and everything after it.

    Parameters
    ----------
    graph : WayGraph
        Track graph; its cache is filled.
    key : str
        Station ID.

    Returns
    -------
    frozenset[str]
        The station and every station reachable from it.
    """
    if key not in graph.closures:
        seen, todo = {key}, [key]
        while todo:
            fresh = graph.succ.get(todo.pop(), Counter()).keys() - seen
            seen |= fresh
            todo.extend(fresh)
        graph.closures[key] = frozenset(seen)
    return graph.closures[key]


def furthest(*, graph: WayGraph, here: str, key: str) -> float:
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

    Returns
    -------
    float
        Metres to the furthest station after the branch's first; 0 when
        positions are missing.
    """
    start = place_of(places=graph.places, key=here).point
    if start is None:
        return 0.0
    points = (
        place_of(places=graph.places, key=far).point
        for far in closure(graph=graph, key=key)
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
        -furthest(graph=graph, here=here, key=there),
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


def junctions(*, trails: Sequence[Trail], placed: set[str]) -> list[str]:
    """
    Find where each run of stations not laid out leaves the laid ones.

    Parameters
    ----------
    trails : Sequence[Trail]
        Runs and trips, heaviest first.
    placed : set[str]
        Stations already laid out, and FROM_KEY.

    Returns
    -------
    list[str]
        Laid-out stations followed by one not laid out, in the order the
        heaviest tails reach them.
    """
    found: dict[str, None] = {}
    for stations, _ in trails:
        for here, there in pairwise((FROM_KEY, *stations)):
            if here in placed and there not in placed:
                found[here] = None
    return list(found)


def lay_way(*, trails: Sequence[Trail], graph: WayGraph, depth: int
            ) -> Hung:
    """
    Lay a way out: a main chain, then every other run hung, indented.

    Parameters
    ----------
    trails : Sequence[Trail]
        The way's runs and trips, heaviest first.
    graph : WayGraph
        The way's track graph.
    depth : int
        Indent of the way's main chain.

    Returns
    -------
    Hung
        Branches by junction; the From's first is the main chain. Other
        branches at the From have depth 1.
    """
    layout = Layout(
        graph=graph, placed={FROM_KEY}, depth={FROM_KEY: 0},
        hung=defaultdict(list),
    )
    grow(layout=layout, junction=FROM_KEY, depth=depth)
    while hanging := junctions(trails=trails, placed=layout.placed):
        for junction in hanging:
            below = layout.depth[junction] + 1
            while grow(layout=layout, junction=junction, depth=below):
                continue
    return layout.hung


def lay_ways(*, trails: Sequence[Trail], places: Mapping[str, StopPlace]
             ) -> list[Hung]:
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
    list[Hung]
        Ways by trips, then by first hop. The first two have main chains
        at depth 0; the rest at depth 1.
    """
    ways = way_trails(trails=trails, owner=ways_of(trails=trails))
    order = sorted(ways, key=lambda way: (
        -sum(trips for _, trips in ways[way]), way,
    ))
    return [
        lay_way(
            trails=ways[way],
            graph=WayGraph(succ=edge_trips(trails=ways[way]), places=places),
            depth=0 if rank < SIDES else 1,
        )
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
            kind=RowKind.MAIN if branch.depth == 0 else RowKind.BRANCH,
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


def side_rank(*, hung: Hung, places: Mapping[str, StopPlace]
              ) -> tuple[int, int, str]:
    """
    Rank a side for the top: more spur rows, more stations, then name.

    Parameters
    ----------
    hung : Hung
        The side's branches by junction.
    places : Mapping[str, StopPlace]
        Every station by ID.

    Returns
    -------
    tuple[int, int, str]
        A key that sorts the top side first.
    """
    branches = [branch for found in hung.values() for branch in found]
    spurs = sum(len(b.stations) for b in branches if b.depth > 0)
    stations = sum(len(branch.stations) for branch in branches)
    first = hung[FROM_KEY][0].stations[0]
    return -spurs, -stations, place_of(places=places, key=first).name


def with_spurs(*, side: Hung, others: Sequence[Hung]) -> Hung:
    """
    Hang further ways at the From of a side.

    Parameters
    ----------
    side : Hung
        The side's branches by junction.
    others : Sequence[Hung]
        Further ways, laid out at depth 1.

    Returns
    -------
    Hung
        The side with every further way's branches added.
    """
    merged = dict(side)
    for other in others:
        merged |= other
    merged[FROM_KEY] = [
        *side[FROM_KEY],
        *(branch for other in others for branch in other[FROM_KEY]),
    ]
    return merged


def approach_rows(*, tails: Sequence[RunTail], placed: set[str]
                  ) -> list[Entry]:
    """
    List up to two stations the busiest run calls at before the From.

    Parameters
    ----------
    tails : Sequence[RunTail]
        Tails through the From.
    placed : set[str]
        Stations already on the strip, and the From's ID.

    Returns
    -------
    list[Entry]
        Approach rows top to bottom, each one's track from the row below.
    """
    heaviest = max(tails, key=lambda each: each.trips)
    picks: list[str] = []
    for key in reversed(heaviest.before):
        if key not in placed and key not in picks:
            picks.append(key)
    picks = picks[:APPROACH_ROWS]
    return [
        Entry(key=key, kind=RowKind.APPROACH, depth=0, above=True,
              parent=back)
        for key, back in zip(picks, (FROM_KEY, *picks))
    ][::-1]


def arrange(*, data: ReachInput, ways: Sequence[Hung],
            places: Mapping[str, StopPlace]) -> list[Entry]:
    """
    Put the sides either side of the From.

    Parameters
    ----------
    data : ReachInput
        The From and its tails.
    ways : Sequence[Hung]
        Ways laid out, heaviest first.
    places : Mapping[str, StopPlace]
        Every station by ID.

    Returns
    -------
    list[Entry]
        Rows top to bottom.
    """
    origin = Entry(key=FROM_KEY, kind=RowKind.ORIGIN, depth=0, above=False,
                   parent=None)
    if len(ways) < SIDES:
        below = side_rows(hung=ways[0], above=False) if ways else []
        placed = {data.origin.stop_id, *(entry.key for entry in below)}
        above = (
            approach_rows(tails=data.tails, placed=placed)
            if ways
            else []
        )
        return [*above, origin, *below]
    top, bottom = sorted(ways[:SIDES], key=lambda hung: side_rank(
        hung=hung, places=places,
    ))
    top = with_spurs(side=top, others=ways[SIDES:])
    return [
        *reversed(side_rows(hung=top, above=True)),
        origin,
        *side_rows(hung=bottom, above=False),
    ]


def build_reach(*, data: ReachInput) -> Reach:
    """
    Lay out the stations a strip reaches from its From.

    The two busiest ways out of the From are its two sides; the side with
    more spur rows, then more stations, goes on top, listed against travel
    order. A further way is a depth-1 spur at the From on the top side.
    With one side, it goes below and up to two approach stations above.

    Parameters
    ----------
    data : ReachInput
        The From, its tails and their places.

    Returns
    -------
    Reach
        Rows top to bottom; just the From when nothing runs on from it.
    """
    trails = sorted(
        ((each.after, each.trips) for each in data.tails if each.after),
        key=lambda trail: -trail[1],
    )
    places = {**data.places, FROM_KEY: data.origin}
    ways = lay_ways(trails=trails, places=places) if trails else []
    entries = arrange(data=data, ways=ways, places=places)
    index = {entry.key: row for row, entry in enumerate(entries)}
    rows = tuple(
        ReachRow(
            place=place_of(places=places, key=entry.key),
            kind=entry.kind,
            depth=entry.depth,
            above=entry.above,
            parent=None if entry.parent is None else index[entry.parent],
        )
        for entry in entries
    )
    return Reach(
        rows=rows,
        stations=sum(
            row.kind in (RowKind.MAIN, RowKind.BRANCH) for row in rows
        ),
        station_of=data.station_of,
    )
