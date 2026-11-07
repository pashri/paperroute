"""Split the stations a strip's From reaches into ways."""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from itertools import groupby, pairwise
from typing import Final

from paperroute_transit.core.reach_track import Run

SETTLE_PASSES: Final[int] = 50

type Trail = tuple[tuple[str, ...], int]
type Owners = dict[str, str]
type Came = tuple[str, str, int]


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
