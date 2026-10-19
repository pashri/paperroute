"""Group the first hops out of a strip's From into ways."""

import math
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


def hop_weight(*, trails: Sequence[HopTrail]) -> Counter[str]:
    """
    Count the trips leaving the From on each first hop.

    Parameters
    ----------
    trails : Sequence[HopTrail]
        Stations after the From and trips; none empty.

    Returns
    -------
    Counter[str]
        Trips by first hop.
    """
    weight: Counter[str] = Counter()
    for stations, trips in trails:
        weight[stations[0]] += trips
    return weight


def passes(*, trails: Sequence[HopTrail], after: Mapping[str, set[str]]
           ) -> Counter[tuple[str, str]]:
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
    Counter[tuple[str, str]]
        Trips by passing and passed first hop: a tail of the passing hop
        calls at the passed one and then at a station one of the passed
        hop's tails calls at after it, or anywhere when one of the passed
        hop's tails stops there.
    """
    found: Counter[tuple[str, str]] = Counter()
    for stations, trips in trails:
        pairs = {
            (stations[0], key)
            for index, key in enumerate(stations[1:], start=1)
            if END in (ahead := after.get(key, set()))
            or not ahead.isdisjoint(stations[index + 1:])
        }
        found.update(dict.fromkeys(pairs, trips))
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
    found = heading_match(places=places, origin=origin, one=host, two=guest)
    return found is not None and found > 0


def heading_match(*, places: Mapping[str, StopPlace], origin: str,
                  one: str, two: str) -> float | None:
    """
    Measure how closely two stations lie the same way from the From.

    Parameters
    ----------
    places : Mapping[str, StopPlace]
        Every station by ID, and the From.
    origin : str
        The From's key in places.
    one : str
        One station.
    two : str
        The other station.

    Returns
    -------
    float | None
        The cosine of the angle between them as seen from the From: 1
        the same way, -1 opposite. None when a position is missing or a
        station sits on the From.
    """
    points = [
        found.point if (found := places.get(key)) else None
        for key in (origin, one, two)
    ]
    start, first, second = points
    if start is None or first is None or second is None:
        return None
    ahead = (first[0] - start[0], first[1] - start[1])
    other = (second[0] - start[0], second[1] - start[1])
    size = math.hypot(*ahead) * math.hypot(*other)
    return (
        (ahead[0] * other[0] + ahead[1] * other[1]) / size if size else None
    )


def closest(*, places: Mapping[str, StopPlace], origin: str, hop: str,
            choices: Sequence[str]) -> int:
    """
    Pick the first hop heading closest to another's way from the From.

    Parameters
    ----------
    places : Mapping[str, StopPlace]
        Every station by ID, and the From.
    origin : str
        The From's key in places.
    hop : str
        The first hop to match.
    choices : Sequence[str]
        First hops to choose from; at least one.

    Returns
    -------
    int
        Index of the choice at the smallest angle to the hop; a choice
        with no angle loses to one with an angle, and ties go to the
        earliest.
    """
    matches = [
        heading_match(places=places, origin=origin, one=hop, two=choice)
        for choice in choices
    ]
    return max(range(len(choices)), key=lambda index: (
        matches[index] is not None, matches[index] or 0.0, -index,
    ))


def partners(*, trails: Sequence[HopTrail], places: Mapping[str, StopPlace],
             origin: str, weight: Counter[str]) -> dict[str, list[str]]:
    """
    List, for each first hop, the heavier hops it rides with.

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
    dict[str, list[str]]
        Heavier first hops by first hop, heaviest first. Two hops ride
        together when either one's tails pass the other and both head the
        same way, whichever is the express.
    """
    rank = {hop: (weight[hop], hop) for hop in weight}
    found: dict[str, list[str]] = defaultdict(list)
    for pair in passes(trails=trails, after=guests(trails=trails)):
        light, heavy = sorted(pair, key=rank.__getitem__)
        if light != heavy and same_heading(
            places=places, origin=origin, host=heavy, guest=light,
        ):
            found[light].append(heavy)
    return {
        hop: sorted(hosts, key=rank.__getitem__, reverse=True)
        for hop, hosts in found.items()
    }


def hop_ways(*, trails: Sequence[HopTrail],
             places: Mapping[str, StopPlace], origin: str) -> dict[str, str]:
    """
    Put each first hop on the way of the hop whose line it rides.

    Of two first hops heading the same way, where one hop's tails call
    at the other and ride on to a station its tails reach (or where its
    tails stop), the lighter joins the heavier's way, when that way's
    own first hop heads the same way too: an express shares the
    all-stops line's way, whichever is busier.

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
    weight = hop_weight(trails=trails)
    rides = partners(
        trails=trails, places=places, origin=origin, weight=weight,
    )
    order = sorted(weight, key=lambda hop: (weight[hop], hop), reverse=True)
    way: dict[str, str] = {}
    for hop in order:
        way[hop] = next(
            (
                way[host]
                for host in rides.get(hop, ())
                if same_heading(
                    places=places, origin=origin, host=way[host], guest=hop,
                )
            ),
            hop,
        )
    return way


def drawn_through(*, trails: Sequence[HopTrail], way: Mapping[str, str]
                  ) -> set[str]:
    """
    Find first hops to draw on the line of another hop of their way.

    Parameters
    ----------
    trails : Sequence[HopTrail]
        Stations after the From and trips; none empty.
    way : Mapping[str, str]
        Way by first hop.

    Returns
    -------
    set[str]
        First hops that another hop of the same way passes on most of its
        trips, as the all-stops line passes an express's first stop.
    """
    weight = hop_weight(trails=trails)
    return {
        passed
        for (passer, passed), trips in passes(
            trails=trails, after=guests(trails=trails),
        ).items()
        if passer != passed
        and way[passer] == way[passed]
        and 2 * trips > weight[passer]
    }
