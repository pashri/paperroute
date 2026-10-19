"""Group the first hops out of a strip's From into ways."""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Final

from paperroute.core.model import StopPlace

END: Final[str] = ''  # stands for a tail stopping at its first hop

type HopTrail = tuple[tuple[str, ...], int]


def guests(*, trails: Sequence[HopTrail]) -> dict[str, set[str]]:
    """
    List the stations after each first hop on the tails it leads.

    Parameters
    ----------
    trails : Sequence[HopTrail]
        Stations after the From and trips.

    Returns
    -------
    dict[str, set[str]]
        Stations after the first hop by first hop, and END when a tail
        stops there.
    """
    found: dict[str, set[str]] = defaultdict(set)
    for stations, _ in trails:
        found[stations[0]].update(stations[1:] or (END,))
    return found


def passes(*, trails: Sequence[HopTrail], after: Mapping[str, set[str]]
           ) -> set[tuple[str, str]]:
    """
    Find first hops another first hop's tails pass and then ride on with.

    Parameters
    ----------
    trails : Sequence[HopTrail]
        Stations after the From and trips.
    after : Mapping[str, set[str]]
        Stations after each first hop on the tails it leads.

    Returns
    -------
    set[tuple[str, str]]
        Host and guest first hops: a host tail calls at the guest and
        then at a station one of the guest's tails calls at after it, or
        anywhere when one of the guest's tails stops there.
    """
    found: set[tuple[str, str]] = set()
    for stations, _ in trails:
        for index, key in enumerate(stations[1:], start=1):
            ahead = after.get(key, set())
            if END in ahead or not ahead.isdisjoint(stations[index + 1:]):
                found.add((stations[0], key))
    return found


def same_heading(*, places: Mapping[str, StopPlace], origin: str,
                 host: str, guest: str) -> bool:
    """
    Say whether two first hops leave the From within a right angle.

    Parameters
    ----------
    places : Mapping[str, StopPlace]
        Every station by ID, and the From.
    origin : str
        The From's key in places.
    host : str
        One first hop.
    guest : str
        The other first hop.

    Returns
    -------
    bool
        True when both lie less than 90 degrees apart as seen from the
        From; False when any position is missing.
    """
    points = [
        found.point if (found := places.get(key)) else None
        for key in (origin, host, guest)
    ]
    start, one, two = points
    if start is None or one is None or two is None:
        return False
    return (
        (one[0] - start[0]) * (two[0] - start[0])
        + (one[1] - start[1]) * (two[1] - start[1])
    ) > 0


def hosts(*, trails: Sequence[HopTrail], places: Mapping[str, StopPlace],
          origin: str, weight: Counter[str]) -> dict[str, str]:
    """
    Pick, for each first hop, the busiest heavier hop it rides with.

    Parameters
    ----------
    trails : Sequence[HopTrail]
        Stations after the From and trips.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From.
    origin : str
        The From's key in places.
    weight : Counter[str]
        Trips by first hop.

    Returns
    -------
    dict[str, str]
        Host by guest first hop, for guests that have one.
    """
    rank = {hop: (weight[hop], hop) for hop in weight}
    found: dict[str, str] = {}
    for host, guest in passes(trails=trails, after=guests(trails=trails)):
        if rank[host] > rank[found.get(guest, guest)] and same_heading(
            places=places, origin=origin, host=host, guest=guest,
        ):
            found[guest] = host
    return found


def hop_ways(*, trails: Sequence[HopTrail],
             places: Mapping[str, StopPlace], origin: str) -> dict[str, str]:
    """
    Put each first hop on the way of the hop whose line it rides.

    A first hop that another hop's tails call at, heading the same way
    and riding on to a station its own tails reach (or where its own
    tails stop), belongs to that hop's way: an express that skips
    stations shares the all-stops line's way.

    Parameters
    ----------
    trails : Sequence[HopTrail]
        Stations after the From and trips; none empty.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From.
    origin : str
        The From's key in places.

    Returns
    -------
    dict[str, str]
        Way (a first hop) by first hop.
    """
    weight: Counter[str] = Counter()
    for stations, trips in trails:
        weight[stations[0]] += trips
    host = hosts(trails=trails, places=places, origin=origin, weight=weight)
    rank = {hop: (weight[hop], hop) for hop in weight}
    way: dict[str, str] = {}
    for hop in sorted(weight, key=rank.__getitem__, reverse=True):
        way[hop] = way[host[hop]] if hop in host else hop
    return way
