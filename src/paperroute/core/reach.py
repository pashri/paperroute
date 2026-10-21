"""The stations a strip reaches from its From."""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import takewhile
from typing import Final

from paperroute.core.model import STATION_FIRST, RouteKind, StopPlace
from paperroute.core.reach_layout import Entry, LineExit, lay_out
from paperroute.core.reach_track import FROM_KEY, place_of

LINE_KINDS: Final[frozenset[RouteKind]] = STATION_FIRST


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
    exits : tuple[LineExit, ...]
        Ways trips leave the strip's line; their off stations are in the
        places.
    """

    origin: StopPlace
    tails: tuple[RunTail, ...]
    places: Mapping[str, StopPlace]
    station_of: Mapping[str, str]
    exits: tuple[LineExit, ...] = ()


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


def every_route(*, key: StripKey) -> bool:
    """
    Say whether a strip takes every route of its kind through the From.

    Parameters
    ----------
    key : StripKey
        Strip kind and routes.

    Returns
    -------
    bool
        True with no kind, for Train, Metro and Light rail, and for
        another kind with no routes chosen.
    """
    return key.kind is None or key.kind in LINE_KINDS or not key.routes


def line_key_of(*, key: StripKey) -> str | None:
    """
    Name the line a strip follows.

    Parameters
    ----------
    key : StripKey
        Strip kind and routes.

    Returns
    -------
    str | None
        The line key, such as 'train:T1', for a station-first kind with
        one route; None otherwise.
    """
    if key.kind not in STATION_FIRST or len(key.routes) != 1:
        return None
    return f'{key.kind}:{key.routes[0]}'


def merge_tails(*, tails: Iterable[RunTail]) -> tuple[RunTail, ...]:
    """
    Merge tails with the same stations after the From.

    Their trips are summed and the heaviest tail's stations before the
    From are kept; on a tie, the first one's.

    Parameters
    ----------
    tails : Iterable[RunTail]
        Tails in order.

    Returns
    -------
    tuple[RunTail, ...]
        Tails, the most trips first, then by their stations.
    """
    found: dict[tuple[str, ...], tuple[int, int, tuple[str, ...]]] = {}
    for tail in tails:
        total, heaviest, before = found.get(tail.after, (0, -1, ()))
        if tail.trips > heaviest:
            heaviest, before = tail.trips, tail.before
        found[tail.after] = (total + tail.trips, heaviest, before)
    merged = (
        RunTail(before=before, after=after, trips=total)
        for after, (total, _, before) in found.items()
    )
    return tuple(sorted(merged, key=lambda tail: (-tail.trips, tail.after)))


def cut_to_line(
    *, tails: Iterable[RunTail], line: Collection[str],
) -> tuple[RunTail, ...]:
    """
    Cut each tail before its first station off a line, then merge them.

    Both sides are counted outward from the From. Stations the runs skip
    do not matter: only stations they call at are checked.

    Parameters
    ----------
    tails : Iterable[RunTail]
        Tails through the From.
    line : Collection[str]
        Station IDs on the line, with the stations merged into them.

    Returns
    -------
    tuple[RunTail, ...]
        Cut tails, merged as `merge_tails` does.
    """
    return merge_tails(tails=(
        RunTail(
            before=tuple(reversed(tuple(
                takewhile(line.__contains__, reversed(tail.before)),
            ))),
            after=tuple(takewhile(line.__contains__, tail.after)),
            trips=tail.trips,
        )
        for tail in tails
    ))


class RowKind(StrEnum):
    """What a strip row shows."""

    APPROACH = 'approach'
    ORIGIN = 'origin'
    MAIN = 'main'
    BRANCH = 'branch'
    FOLD = 'fold'
    EXIT = 'exit'


@dataclass(frozen=True)
class ReachRow:
    """
    One station on a strip.

    Attributes
    ----------
    place : StopPlace
        The station.
    kind : RowKind
        Approach, the From, the main line, a branch, a closed fold or a
        way off the line.
    depth : int
        Indent: 0 for the main line, one more per branch level.
    above : bool
        True when the row is above the From.
    parent : int | None
        Index of the row the track comes from: the previous station on
        the row's branch, or its junction for a branch's first row. An
        approach row's is the row below it; None for the From.
    trips : int
        Trip-days on the track from the parent; 0 for the From and
        approach rows.
    folded : int
        Stations a fold row stands for; 0 for any other row.
    lines : tuple[str, ...]
        Route numbers of an exit row, whose place is the first station
        off the line and whose depth is its junction's; empty for any
        other row.
    """

    place: StopPlace
    kind: RowKind
    depth: int
    above: bool
    parent: int | None
    trips: int = 0
    folded: int = 0
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reach:
    """
    The rows of a strip, top to bottom.

    Attributes
    ----------
    rows : tuple[ReachRow, ...]
        Rows, top to bottom, with exactly one From row.
    stations : int
        Rows that are neither the From, an approach nor an exit.
    station_of : Mapping[str, str]
        The station ID of each stop under a station on the strip.
    trip : str | None
        The trip whose calls are the rows below the From, in calling
        order; None when they are the strip's fixed line.
    """

    rows: tuple[ReachRow, ...]
    stations: int
    station_of: Mapping[str, str]
    trip: str | None = None


def approach_rows(*, tails: Sequence[RunTail], placed: set[str]
                  ) -> list[Entry]:
    """
    List every station the busiest run calls at before the From.

    Parameters
    ----------
    tails : Sequence[RunTail]
        Tails through the From; at least one.
    placed : set[str]
        Stations already on the strip, and the From's ID.

    Returns
    -------
    list[Entry]
        The stations before the From not on the strip, back to the
        run's first stop, top to bottom, each one's track from the row
        below.
    """
    heaviest = max(tails, key=lambda each: each.trips)
    picks: list[str] = []
    for key in reversed(heaviest.before):
        if key not in placed and key not in picks:
            picks.append(key)
    return [
        Entry(key=key, depth=0, above=True, parent=back)
        for key, back in zip(picks, (FROM_KEY, *picks))
    ][::-1]


def exits_by_junction(*, data: ReachInput) -> dict[str, list[LineExit]]:
    """
    Group a strip's exits by junction.

    Parameters
    ----------
    data : ReachInput
        The From and its exits.

    Returns
    -------
    dict[str, list[LineExit]]
        Exits by junction station ID, the From's under FROM_KEY.
    """
    found: dict[str, list[LineExit]] = {}
    origin = data.origin.stop_id
    for each in data.exits:
        at = FROM_KEY if each.junction == origin else each.junction
        found.setdefault(at, []).append(each)
    return found


def kind_of(*, entry: Entry) -> RowKind:
    """
    Say what a laid-out station row shows.

    Parameters
    ----------
    entry : Entry
        The row.

    Returns
    -------
    RowKind
        EXIT for a way off the line, MAIN at depth 0, else BRANCH.
    """
    if entry.exit:
        return RowKind.EXIT
    return RowKind.MAIN if entry.depth == 0 else RowKind.BRANCH


def ordered(*, data: ReachInput, places: Mapping[str, StopPlace]
            ) -> list[tuple[RowKind, Entry]]:
    """
    List the rows top to bottom with what each shows.

    Parameters
    ----------
    data : ReachInput
        The From and its tails.
    places : Mapping[str, StopPlace]
        Every station by ID, and the From under FROM_KEY.

    Returns
    -------
    list[tuple[RowKind, Entry]]
        Rows top to bottom. With one side, it goes below, with the
        busiest run's earlier stations above as approach rows.
    """
    trails = sorted(
        ((each.after, each.trips) for each in data.tails if each.after),
        key=lambda trail: -trail[1],
    )
    sides = lay_out(
        trails=trails, places=places, exits=exits_by_junction(data=data),
    )
    above = [(kind_of(entry=entry), entry) for entry in sides.above]
    if sides.below and not sides.above and data.tails:
        placed = {data.origin.stop_id, *(each.key for each in sides.below)}
        above = [
            (RowKind.APPROACH, entry)
            for entry in approach_rows(tails=data.tails, placed=placed)
        ]
    origin = Entry(key=FROM_KEY, depth=0, above=False, parent=None)
    return [*above, (RowKind.ORIGIN, origin), *(
        (kind_of(entry=entry), entry) for entry in sides.below
    )]


def located(*, data: ReachInput) -> StopPlace:
    """
    Give the From a position from its station when it has none.

    Parameters
    ----------
    data : ReachInput
        The From and its places.

    Returns
    -------
    StopPlace
        The From, positioned like its station in the places when its own
        position is missing.
    """
    station = data.places.get(data.origin.stop_id)
    if data.origin.point is not None or station is None:
        return data.origin
    return replace(data.origin, point=station.point)


def build_reach(*, data: ReachInput) -> Reach:
    """
    Lay out the stations a strip reaches from its From.

    Parameters
    ----------
    data : ReachInput
        The From, its tails and their places.

    Returns
    -------
    Reach
        Rows top to bottom; just the From when nothing runs on from it.
    """
    places = {**data.places, FROM_KEY: located(data=data)}
    rows = ordered(data=data, places=places)
    index = {entry.key: row for row, (_, entry) in enumerate(rows)}
    return Reach(
        rows=tuple(
            ReachRow(
                place=place_of(places=places, key=entry.key),
                kind=kind,
                depth=entry.depth,
                above=entry.above,
                parent=None if entry.parent is None else index[entry.parent],
                trips=entry.trips,
                lines=entry.lines,
            )
            for kind, entry in rows
        ),
        stations=sum(
            kind in (RowKind.MAIN, RowKind.BRANCH) for kind, _ in rows
        ),
        station_of=data.station_of,
    )
