"""The ways trains leave a train Strip's main line, and what they name."""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from paperroute_transit.core.train_network import (
    TrainNetwork,
    WeekdayRun,
    expanded,
    from_origin,
)

EXIT_TRAINS: Final[int] = 6  # trains a weekday
END_SHARE: Final[float] = 0.3


@dataclass(frozen=True)
class TrainExit:
    """
    A way trains leave the main line.

    Attributes
    ----------
    junction : str
        The last station the main train and the exit's trains both call
        at or pass.
    first : str
        The first station off the line, possibly one passed.
    names : tuple[str, ...]
        The places the exit names, as station IDs, in walk order; a
        train that returns to the From is the loop's name instead.
    trains : int
        Weekday trains that leave this way.
    """

    junction: str
    first: str
    names: tuple[str, ...]
    trains: int


@dataclass(frozen=True)
class Tail:
    """
    The stations a run reaches after leaving the line.

    Attributes
    ----------
    route : str
        Route short name; the operator in Cambridge.
    stations : tuple[str, ...]
        Stations after the junction, passed ones included. A train that
        returns to the From ends with the loop's name.
    trains : int
        Regular trains a weekday.
    loop : bool
        Whether the run returns to the From.
    """

    route: str
    stations: tuple[str, ...]
    trains: int
    loop: bool

    @property
    def via(self) -> frozenset[str]:
        """
        Give the real stations the tail runs through.

        Returns
        -------
        frozenset[str]
            The stations, without a loop's name.
        """
        return frozenset(self.stations[:-1] if self.loop else self.stations)


@dataclass(frozen=True)
class Group:
    """
    The tails that leave the line at one junction and share stations.

    Attributes
    ----------
    junction : str
        The station the tails leave the line at.
    tails : tuple[Tail, ...]
        The tails.
    via : frozenset[str]
        Every station the tails run through.
    """

    junction: str
    tails: tuple[Tail, ...]
    via: frozenset[str]

    @property
    def trains(self) -> int:
        """
        Sum the group's weekday trains.

        Returns
        -------
        int
            The trains.
        """
        return sum(tail.trains for tail in self.tails)


@dataclass(frozen=True)
class Request:
    """
    What an exit search looks for.

    Attributes
    ----------
    network : TrainNetwork
        The hops and weekday runs.
    origin : str
        The From station ID.
    line : Sequence[str]
        The main line's stations in order, passed included, From first.
    first_hops : frozenset[str] | None
        The first stations of the main train's side; None for all.
    loop_name : str
        The name of a train that returns to the From; empty for none.
    """

    network: TrainNetwork
    origin: str
    line: Sequence[str]
    first_hops: frozenset[str] | None
    loop_name: str


@dataclass
class Node:
    """
    A station in the tree of tails, and the trains through it.

    Attributes
    ----------
    trains : int
        Weekday trains through the station along this path.
    children : dict[str, Node]
        The next stations by ID.
    """

    trains: int = 0
    children: dict[str, 'Node'] = field(default_factory=dict)


def train_exits(
    *,
    network: TrainNetwork,
    origin: str,
    line: Sequence[str],
    first_hops: frozenset[str] | None,
    loop_name: str,
) -> tuple[TrainExit, ...]:
    """
    Find the ways trains from the From leave the main line.

    Parameters
    ----------
    network : TrainNetwork
        The hops and weekday runs.
    origin : str
        The From station ID.
    line : Sequence[str]
        The main line's stations in order, passed included, with the
        From first.
    first_hops : frozenset[str] | None
        The first stations of the main train's side; None keeps every
        run.
    loop_name : str
        The name of a train that returns to the From; empty drops them.

    Returns
    -------
    tuple[TrainExit, ...]
        Exits with at least ``EXIT_TRAINS`` weekday trains, busiest
        first, ties by junction then first station. Work grows with the
        runs through the From and their tail lengths.
    """
    request = Request(
        network=network,
        origin=origin,
        line=line,
        first_hops=first_hops,
        loop_name=loop_name,
    )
    groups = grouped(tails=cut_tails(request=request))
    found = [
        exit_of(group=group, runs=network.runs)
        for group in groups
        if group.trains >= EXIT_TRAINS
    ]
    return tuple(sorted(
        found, key=lambda each: (-each.trains, each.junction, each.first),
    ))


def cut_tails(*, request: Request) -> list[tuple[str, Tail]]:
    """
    Cut each run's stations after the From where it leaves the line.

    Parameters
    ----------
    request : Request
        The network, the line and the filters.

    Returns
    -------
    list[tuple[str, Tail]]
        The junction and tail of each run that leaves the line.
    """
    found: list[tuple[str, Tail]] = []
    for run in request.network.runs:
        cut = cut_run(run=run, request=request)
        if cut is not None:
            found.append(cut)
    return found


def cut_run(
    *, run: WeekdayRun, request: Request,
) -> tuple[str, Tail] | None:
    """
    Cut one run where it leaves the line for the last time.

    Parameters
    ----------
    run : WeekdayRun
        The run.
    request : Request
        The network, the line and the filters.

    Returns
    -------
    tuple[str, Tail] | None
        The junction and tail, or None when the run does not call at
        the From, is not on the main train's side, is a loop with no
        name or in the reverse direction, or ends on the line.
    """
    rest, loops = from_origin(stations=run.stations, origin=request.origin)
    if not rest or (loops and not request.loop_name):
        return None
    path = [
        station
        for station, _ in expanded(
            network=request.network, stations=(request.origin, *rest),
        )
    ]
    if not wanted(path=path, loops=loops, request=request):
        return None
    on_line = set(request.line)
    last = max(
        (index for index, station in enumerate(path) if station in on_line),
        default=-1,
    )
    if last < 0 or last == len(path) - 1:
        return None
    after = (*path[last + 1:], *([request.loop_name] if loops else []))
    return path[last], Tail(
        route=run.route, stations=after, trains=run.trains, loop=loops,
    )


def wanted(
    *, path: Sequence[str], loops: bool, request: Request,
) -> bool:
    """
    Say whether a tail is on the main train's side and way round.

    Parameters
    ----------
    path : Sequence[str]
        The run's stations from the From, passed ones included.
    loops : bool
        Whether the run returns to the From.
    request : Request
        The line and the filters.

    Returns
    -------
    bool
        False when the first station is not in the first hops, or a loop
        does not start with the line's own second station.
    """
    hops = request.first_hops
    if hops is not None and path[1] not in hops:
        return False
    if not loops:
        return True
    return len(request.line) > 1 and path[1] == request.line[1]


def grouped(*, tails: Iterable[tuple[str, Tail]]) -> list[Group]:
    """
    Group tails by junction, merging groups whose stations meet.

    Parameters
    ----------
    tails : Iterable[tuple[str, Tail]]
        Each tail with its junction.

    Returns
    -------
    list[Group]
        The groups, with each tail in exactly one.
    """
    groups: list[Group] = []
    for junction, tail in tails:
        meets: list[Group] = []
        apart: list[Group] = []
        for group in groups:
            meeting = group.junction == junction and group.via & tail.via
            (meets if meeting else apart).append(group)
        groups = [*apart, Group(
            junction=junction,
            tails=(*(each for group in meets for each in group.tails), tail),
            via=frozenset().union(tail.via, *(group.via for group in meets)),
        )]
    return groups


def exit_of(*, group: Group, runs: Sequence[WeekdayRun]) -> TrainExit:
    """
    Turn a group into an exit with its names.

    Parameters
    ----------
    group : Group
        The tails leaving at one junction.
    runs : Sequence[WeekdayRun]
        The runs through the From, for what runs on past an end.

    Returns
    -------
    TrainExit
        The exit. When no end qualifies the end with the most trains
        names it.
    """
    named = named_ends(tails=group.tails, runs=runs)
    if not named:
        ends = ends_of(tails=group.tails)
        named = frozenset({min(ends, key=lambda end: (-ends[end], end))})
    root = tree_of(tails=group.tails)
    return TrainExit(
        junction=group.junction,
        first=branches(node=root)[0][0],
        names=walk_order(tails=group.tails, named=named),
        trains=group.trains,
    )


def ends_of(*, tails: Iterable[Tail]) -> Counter[str]:
    """
    Count the trains ending at each station.

    Parameters
    ----------
    tails : Iterable[Tail]
        The tails.

    Returns
    -------
    Counter[str]
        Weekday trains by last station, a loop's name for a loop.
    """
    found: Counter[str] = Counter()
    for tail in tails:
        found[tail.stations[-1]] += tail.trains
    return found


def named_ends(
    *, tails: Sequence[Tail], runs: Sequence[WeekdayRun],
) -> frozenset[str]:
    """
    Pick the ends of an exit that earn a name.

    Parameters
    ----------
    tails : Sequence[Tail]
        The tails of one exit.
    runs : Sequence[WeekdayRun]
        Every run through the From.

    Returns
    -------
    frozenset[str]
        The ends where at least ``EXIT_TRAINS`` trains end, and either
        some route ending there has fewer than that running on past it,
        or at least ``END_SHARE`` of the trains reaching it end there.
    """
    ends = ends_of(tails=tails)
    through: Counter[str] = Counter()
    routes: dict[str, set[str]] = {}
    for tail in tails:
        routes.setdefault(tail.stations[-1], set()).add(tail.route)
        for station in tail.stations[:-1]:
            through[station] += tail.trains
    onward = onward_trains(runs=runs)
    return frozenset(
        end
        for end, count in ends.items()
        if count >= EXIT_TRAINS and (
            any(onward[route, end] < EXIT_TRAINS for route in routes[end])
            or count >= END_SHARE * (count + through[end])
        )
    )


def onward_trains(*, runs: Iterable[WeekdayRun]) -> Counter[tuple[str, str]]:
    """
    Count the trains of each route that call at a station and run on.

    Parameters
    ----------
    runs : Iterable[WeekdayRun]
        The runs.

    Returns
    -------
    Counter[tuple[str, str]]
        Weekday trains by route and station, for stations that are not
        the run's last.
    """
    found: Counter[tuple[str, str]] = Counter()
    for run in runs:
        for station in set(run.stations[:-1]):
            found[run.route, station] += run.trains
    return found


def tree_of(*, tails: Iterable[Tail]) -> Node:
    """
    Merge tails into a tree from the junction.

    Parameters
    ----------
    tails : Iterable[Tail]
        The tails.

    Returns
    -------
    Node
        The junction; each tail is a path of children from it.
    """
    root = Node()
    for tail in tails:
        node = root
        for station in tail.stations:
            node = node.children.setdefault(station, Node())
            node.trains += tail.trains
    return root


def branches(*, node: Node) -> list[tuple[str, Node]]:
    """
    List a node's children, busiest first.

    Parameters
    ----------
    node : Node
        The node.

    Returns
    -------
    list[tuple[str, Node]]
        Each child with its station, ties by station ID.
    """
    return sorted(
        node.children.items(), key=lambda item: (-item[1].trains, item[0]),
    )


def walk_order(
    *, tails: Iterable[Tail], named: frozenset[str],
) -> tuple[str, ...]:
    """
    Name the ends in a depth-first walk, busiest branch first.

    Parameters
    ----------
    tails : Iterable[Tail]
        The tails of one exit.
    named : frozenset[str]
        The stations that earn a name.

    Returns
    -------
    tuple[str, ...]
        Each named station once, where the walk first reaches it.
    """
    found: list[str] = []
    stack = branches(node=tree_of(tails=tails))[::-1]
    while stack:
        station, node = stack.pop()
        if station in named and station not in found:
            found.append(station)
        stack.extend(branches(node=node)[::-1])
    return tuple(found)
