"""The stations a strip reaches from its From."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from paperroute.core.model import RouteKind, StopPlace
from paperroute.core.reach_layout import Entry, lay_out
from paperroute.core.reach_track import FROM_KEY, place_of

APPROACH_ROWS: Final[int] = 2
LINE_KINDS: Final[frozenset[RouteKind]] = frozenset(
    {RouteKind.TRAIN, RouteKind.METRO, RouteKind.LIGHT_RAIL},
)


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


class RowKind(StrEnum):
    """What a strip row shows."""

    APPROACH = 'approach'
    ORIGIN = 'origin'
    MAIN = 'main'
    BRANCH = 'branch'
    FOLD = 'fold'


@dataclass(frozen=True)
class ReachRow:
    """
    One station on a strip.

    Attributes
    ----------
    place : StopPlace
        The station.
    kind : RowKind
        Approach, the From, the main line, a branch or a closed fold.
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
    """

    place: StopPlace
    kind: RowKind
    depth: int
    above: bool
    parent: int | None
    trips: int = 0
    folded: int = 0


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


def approach_rows(*, tails: Sequence[RunTail], placed: set[str]
                  ) -> list[Entry]:
    """
    List up to two stations the busiest run calls at before the From.

    Parameters
    ----------
    tails : Sequence[RunTail]
        Tails through the From; at least one.
    placed : set[str]
        Stations already on the strip, and the From's ID.

    Returns
    -------
    list[Entry]
        The last two stations before the From not on the strip, top to
        bottom, each one's track from the row below.
    """
    heaviest = max(tails, key=lambda each: each.trips)
    picks: list[str] = []
    for key in reversed(heaviest.before):
        if key not in placed and key not in picks:
            picks.append(key)
    picks = picks[:APPROACH_ROWS]
    return [
        Entry(key=key, depth=0, above=True, parent=back)
        for key, back in zip(picks, (FROM_KEY, *picks))
    ][::-1]


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
        Rows top to bottom. With one side, it goes below, with up to two
        approach rows above.
    """
    trails = sorted(
        ((each.after, each.trips) for each in data.tails if each.after),
        key=lambda trail: -trail[1],
    )
    sides = lay_out(trails=trails, places=places)
    above: list[tuple[RowKind, Entry]] = [
        (RowKind.MAIN if entry.depth == 0 else RowKind.BRANCH, entry)
        for entry in sides.above
    ]
    if sides.below and not sides.above:
        placed = {data.origin.stop_id, *(each.key for each in sides.below)}
        above = [
            (RowKind.APPROACH, entry)
            for entry in approach_rows(tails=data.tails, placed=placed)
        ]
    origin = Entry(key=FROM_KEY, depth=0, above=False, parent=None)
    return [*above, (RowKind.ORIGIN, origin), *(
        (RowKind.MAIN if entry.depth == 0 else RowKind.BRANCH, entry)
        for entry in sides.below
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
            )
            for kind, entry in rows
        ),
        stations=sum(
            kind in (RowKind.MAIN, RowKind.BRANCH) for kind, _ in rows
        ),
        station_of=data.station_of,
    )
