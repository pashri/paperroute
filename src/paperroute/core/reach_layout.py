"""Lay a strip's stations out in lines and indented spurs."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from paperroute.core.model import StopPlace
from paperroute.core.reach_hops import (
    closest,
    drawn_through,
    hop_stats,
    hop_ways,
)
from paperroute.core.reach_track import (
    FROM_KEY,
    Branch,
    Hung,
    lay_way,
    place_of,
)
from paperroute.core.reach_ways import Trail, way_runs, ways_of

SIDES: Final[int] = 2


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
    stats = hop_stats(trails=trails)
    hops = hop_ways(stats=stats, places=places, origin=FROM_KEY)
    ways = way_runs(trails=trails, owner=ways_of(trails=trails, hops=hops))
    through = drawn_through(stats=stats, way=hops)
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


def hang_further(*, sides: Sequence[LaidWay], others: Sequence[LaidWay],
                 places: Mapping[str, StopPlace]) -> list[LaidWay]:
    """
    Hang each further way at the From of the side heading its way.

    Parameters
    ----------
    sides : Sequence[LaidWay]
        The two sides, the one to use without positions first.
    others : Sequence[LaidWay]
        Further ways, laid out at depth 1.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From under FROM_KEY.

    Returns
    -------
    list[LaidWay]
        The sides, each with the further ways whose first hop heads
        closest to its own.
    """
    picks = [
        closest(
            places=places,
            origin=FROM_KEY,
            hop=other.hop,
            choices=[side.hop for side in sides],
        )
        for other in others
    ]
    return [
        LaidWay(hop=side.hop, hung=with_spurs(side=side.hung, others=[
            other for other, pick in zip(others, picks) if pick == index
        ]))
        for index, side in enumerate(sides)
    ]


def lay_out(*, trails: Sequence[Trail], places: Mapping[str, StopPlace]
            ) -> Sides:
    """
    Lay the stations out either side of the From.

    The two busiest ways out of the From are its two sides. A further way
    is a depth-1 spur at the From on the side whose first hop heads
    closest to its own, or on the top side without positions. The side
    with more spur rows, then more stations, goes on top, listed against
    travel order. One way goes below.

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
    sides = hang_further(
        sides=sorted(ways[:SIDES], key=lambda way: side_rank(
            way=way, places=places,
        )),
        others=ways[SIDES:],
        places=places,
    )
    top, bottom = sorted(sides, key=lambda way: side_rank(
        way=way, places=places,
    ))
    return Sides(
        above=tuple(reversed(side_rows(hung=top.hung, above=True))),
        below=tuple(side_rows(hung=bottom.hung, above=False)),
    )
