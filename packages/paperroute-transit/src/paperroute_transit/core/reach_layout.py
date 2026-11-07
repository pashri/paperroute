"""Lay a strip's stations out in lines and indented spurs."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.reach_hops import (
    closest,
    drawn_through,
    hop_stats,
    hop_ways,
)
from paperroute_transit.core.reach_track import (
    FROM_KEY,
    Branch,
    Hung,
    lay_way,
    place_of,
)
from paperroute_transit.core.reach_ways import Trail, way_runs, ways_of

SIDES: Final[int] = 2


@dataclass(frozen=True)
class LineExit:
    """
    A way trips leave a strip's line.

    Attributes
    ----------
    junction : str
        Station ID on the strip the trips leave from, the From's
        included.
    off : str
        First station off the line.
    lines : tuple[str, ...]
        Route numbers making the hop, in chooser order.
    trips : int
        Trip-days through the From making the hop.
    """

    junction: str
    off: str
    lines: tuple[str, ...]
    trips: int


type Exits = Mapping[str, Sequence[LineExit]]


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
    trips : int
        Trips on the track from the parent; 0 for the From and approach
        rows.
    exit : bool
        True for a way off the line.
    lines : tuple[str, ...]
        An exit's route numbers; empty for any other row.
    """

    key: str
    depth: int
    above: bool
    parent: str | None
    trips: int = 0
    exit: bool = False
    lines: tuple[str, ...] = ()


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


def exit_entries(*, junction: str, depth: int, above: bool, exits: Exits
                 ) -> list[Entry]:
    """
    List a junction's exit rows, nearest the junction first.

    Parameters
    ----------
    junction : str
        Station ID, or FROM_KEY for the From.
    depth : int
        The junction's indent, which its exits share.
    above : bool
        True above the From.
    exits : Exits
        Exits by junction.

    Returns
    -------
    list[Entry]
        One row per exit, the most trips first, ties by off station.
    """
    found = sorted(
        exits.get(junction, ()), key=lambda each: (-each.trips, each.off),
    )
    return [
        Entry(
            key=each.off,
            depth=depth,
            above=above,
            parent=junction,
            trips=each.trips,
            exit=True,
            lines=each.lines,
        )
        for each in found
    ]


def travel(*, hung: Hung, branch: Branch, above: bool, exits: Exits
           ) -> Iterator[Entry]:
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
    exits : Exits
        Exits by junction; each station's follow its branches.

    Yields
    ------
    Entry
        Rows in travel order.
    """
    for back, key, trips in zip(
        (branch.junction, *branch.stations), branch.stations, branch.trips,
    ):
        yield Entry(
            key=key,
            depth=branch.depth,
            above=above,
            parent=back,
            trips=trips,
        )
        for child in hung.get(key, ()):
            yield from travel(
                hung=hung, branch=child, above=above, exits=exits,
            )
        yield from exit_entries(
            junction=key, depth=branch.depth, above=above, exits=exits,
        )


def side_rows(*, hung: Hung, above: bool, exits: Exits) -> list[Entry]:
    """
    List a side's rows in travel order.

    Parameters
    ----------
    hung : Hung
        Branches by junction; the From's first is the main chain.
    above : bool
        True above the From.
    exits : Exits
        Exits by junction.

    Returns
    -------
    list[Entry]
        The From's other branches, heaviest first, then, below the From
        only, the From's exits, then the main chain.
    """
    main, *rest = hung[FROM_KEY]
    pieces = sorted(rest, key=lambda branch: -branch.weight)
    ahead = [
        entry
        for branch in pieces
        for entry in travel(hung=hung, branch=branch, above=above, exits=exits)
    ]
    own = [] if above else exit_entries(
        junction=FROM_KEY, depth=0, above=False, exits=exits,
    )
    return [
        *ahead,
        *own,
        *travel(hung=hung, branch=main, above=above, exits=exits),
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


def lay_out(*, trails: Sequence[Trail], places: Mapping[str, StopPlace],
            exits: Exits) -> Sides:
    """
    Lay the stations out either side of the From.

    The two busiest ways out of the From are its two sides. A further way
    is a depth-1 spur at the From on the side whose first hop heads
    closest to its own, or on the top side without positions. The side
    with more spur rows, then more stations, goes on top, listed against
    travel order. One way goes below. Each station's exits follow its
    branches; the From's go below it.

    Parameters
    ----------
    trails : Sequence[Trail]
        Stations after the From and trips, heaviest first; none empty.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From under FROM_KEY.
    exits : Exits
        Exits by junction, the From's under FROM_KEY.

    Returns
    -------
    Sides
        Rows above and below the From.
    """
    ways = lay_ways(trails=trails, places=places)
    if len(ways) < SIDES:
        below = side_rows(
            hung=ways[0].hung, above=False, exits=exits,
        ) if ways else exit_entries(
            junction=FROM_KEY, depth=0, above=False, exits=exits,
        )
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
        above=tuple(reversed(side_rows(
            hung=top.hung, above=True, exits=exits,
        ))),
        below=tuple(side_rows(hung=bottom.hung, above=False, exits=exits)),
    )
