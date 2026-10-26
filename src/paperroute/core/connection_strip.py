"""The Strip of a connection pin: a section per leg, then the pairs."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from paperroute.core.connection_model import Pair, walk_metres
from paperroute.core.connection_view import PairRow, no_pair_text, pair_rows
from paperroute.core.live import LiveDeparture
from paperroute.core.map_view import MapView
from paperroute.core.model import (
    Mode,
    Pin,
    StopPlace,
    service_seconds,
    service_time,
    shifted,
)
from paperroute.core.patterns import PatternStop, TripPattern
from paperroute.core.stop_view import PinTag, StopView, clock_text
from paperroute.core.strip_model import LOADING
from paperroute.core.strip_times import from_call

STRIP_STOPS: Final[int] = 11
MIN_SHARE: Final[int] = 3

type LegCall = tuple[PatternStop, datetime]


class StopMark(StrEnum):
    """Where a row sits in its leg."""

    FIRST = 'first'
    MIDDLE = 'middle'
    LAST = 'last'
    FOLD = 'fold'


@dataclass(frozen=True)
class LegStop:
    """
    One row of a leg.

    Attributes
    ----------
    name : str
        Stop name, or '{hidden} stops' on a fold row.
    time : str
        Call time; empty on a fold row.
    mark : StopMark
        Where the row sits in its leg.
    """

    name: str
    time: str
    mark: StopMark


@dataclass(frozen=True)
class LegSection:
    """
    One leg of the selected pair.

    Attributes
    ----------
    route : str
        Route of the leg.
    mode : Mode
        Mode of the leg.
    title : str
        '{from} → {to}' of the leg.
    note : str | None
        The walk and allowance on leg 2, else None.
    stops : tuple[LegStop, ...]
        The leg's rows, trimmed to its share.
    empty : str | None
        Text drawn in place of the rows, or None.
    """

    route: str
    mode: Mode
    title: str
    note: str | None
    stops: tuple[LegStop, ...]
    empty: str | None


@dataclass(frozen=True)
class ConnectionStripView:
    """
    Everything the connection Strip draws.

    Attributes
    ----------
    sections : tuple[LegSection, ...]
        The selected pair's legs; empty with no pair.
    pairs : tuple[PairRow, ...]
        Every pair, in leg-1 departure order.
    selected : int
        Index of the selected pair.
    empty : str | None
        Why there is no pair, or None.
    """

    origin: str
    destination: str | None
    clock: str
    tag: PinTag
    banner: str | None
    warning: bool
    sections: tuple[LegSection, ...]
    more: int
    planned: bool
    pairs: tuple[PairRow, ...]
    selected: int
    empty: str | None


@dataclass(frozen=True)
class ConnectionStripMapView:
    """
    The connection map beside the selected pair's sections.

    Attributes
    ----------
    map : MapView
        The connection map.
    pane : ConnectionStripView
        The sections, drawn without the pair list.
    """

    map: MapView
    pane: ConnectionStripView


def end_call(
    *, item: LiveDeparture, pattern: TripPattern, start: int, end: str,
) -> int | None:
    """
    Find the call at which a departure reaches a station.

    Parameters
    ----------
    item : LiveDeparture
        Departure.
    pattern : TripPattern
        Its trip.
    start : int
        Index of the boarding call.
    end : str
        Station ID got off at.

    Returns
    -------
    int | None
        The first later call at `end` at the departure's arrival time,
        else the first later call at `end`, else None.
    """
    departure = item.departure
    at_end = [
        index
        for index in range(start + 1, len(pattern.stops))
        if pattern.stops[index].station == end
    ]
    arrives = service_seconds(
        day=departure.service_day, moment=departure.arrives,
    )
    timed = [i for i in at_end if pattern.stops[i].arrives == arrives]
    return next(iter(timed or at_end), None)


def leg_calls(
    *, item: LiveDeparture, pattern: TripPattern, start: str, end: str,
) -> tuple[LegCall, ...]:
    """
    List a leg's calls from boarding to getting off, with live times.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    pattern : TripPattern
        Its trip.
    start : str
        Station ID boarded at.
    end : str
        Station ID got off at.

    Returns
    -------
    tuple[LegCall, ...]
        Each call and its time shifted by the delay: the departure, or
        the arrival on the last call. Passing calls are left out. Empty
        when either end is not found.
    """
    departure = item.departure
    first = from_call(
        departure=departure, pattern=pattern, origin=start, stations={},
    )
    last = None if first is None else end_call(
        item=item, pattern=pattern, start=first, end=end,
    )
    if first is None or last is None:
        return ()
    delay = int(item.departs.timestamp() - departure.departs.timestamp())
    return tuple(
        (
            stop,
            shifted(
                moment=service_time(
                    day=departure.service_day,
                    seconds=stop.arrives if index == last else stop.departs,
                ),
                seconds=delay,
            ),
        )
        for index, stop in enumerate(pattern.stops)
        if first <= index <= last and not stop.passes
    )


def shares(*, counts: tuple[int, ...]) -> tuple[int, ...]:
    """
    Share the strip's rows out between legs.

    Parameters
    ----------
    counts : tuple[int, ...]
        Calls on each leg.

    Returns
    -------
    tuple[int, ...]
        Rows each leg gets: in proportion to its calls, at least
        `MIN_SHARE`, never more than it has, and any rows a leg cannot
        use handed to the others, up to `STRIP_STOPS` in all.
    """
    total = sum(counts) or 1
    given = [
        min(count, max(MIN_SHARE, round(STRIP_STOPS * count / total)))
        for count in counts
    ]
    while sum(given) > STRIP_STOPS:
        given[given.index(max(given))] -= 1
    short = [i for i, count in enumerate(counts) if given[i] < count]
    while short and sum(given) < STRIP_STOPS:
        given[short[0]] += 1
        short = [i for i, count in enumerate(counts) if given[i] < count]
    return tuple(given)


def trimmed(*, calls: Sequence[LegStop], share: int) -> tuple[LegStop, ...]:
    """
    Cut a leg's rows to its share, folding the middle.

    Parameters
    ----------
    calls : Sequence[LegStop]
        The leg's rows.
    share : int
        Rows it may use.

    Returns
    -------
    tuple[LegStop, ...]
        The rows when they fit; else `share // 2` from the start, a fold
        row '{hidden} stops', and `(share - 1) // 2` from the end.
    """
    if len(calls) <= share:
        return tuple(calls)
    head = share // 2
    tail = (share - 1) // 2
    hidden = len(calls) - head - tail
    fold = LegStop(name=f'{hidden} stops', time='', mark=StopMark.FOLD)
    return (*calls[:head], fold, *calls[len(calls) - tail:])


def leg_stops(*, calls: Sequence[LegCall]) -> tuple[LegStop, ...]:
    """
    Turn a leg's calls into rows.

    Parameters
    ----------
    calls : Sequence[LegCall]
        The calls with their times.

    Returns
    -------
    tuple[LegStop, ...]
        One row each; the first is `FIRST`, the last `LAST`.
    """
    last = len(calls) - 1
    return tuple(
        LegStop(
            name=stop.name or stop.station,
            time=clock_text(moment=moment),
            mark=(
                StopMark.FIRST
                if index == 0
                else StopMark.LAST if index == last else StopMark.MIDDLE
            ),
        )
        for index, (stop, moment) in enumerate(calls)
    )


@dataclass(frozen=True)
class Leg:
    """
    One leg of a pair, before its rows are trimmed.

    Attributes
    ----------
    item : LiveDeparture
        The leg's departure.
    start : StopPlace
        Where it is boarded.
    end : StopPlace
        Where it is left.
    note : str | None
        The heading's note.
    """

    item: LiveDeparture
    start: StopPlace
    end: StopPlace
    note: str | None = None


def pair_legs_of(*, pair: Pair, pin: Pin) -> tuple[Leg, ...]:
    """
    Name the legs a pair rides.

    Parameters
    ----------
    pair : Pair
        The pair.
    pin : Pin
        Connection pin.

    Returns
    -------
    tuple[Leg, ...]
        Leg 1 to the change and leg 2 on, or one leg when staying on.
    """
    change = pin.change
    end = pin.destination or pin.origin
    if pair.second is None or change is None:
        return (Leg(item=pair.first, start=pin.origin, end=end),)
    metres = walk_metres(alight=change.alight, board=change.board)
    return (
        Leg(item=pair.first, start=pin.origin, end=change.alight),
        Leg(
            item=pair.second,
            start=change.board,
            end=end,
            note=f'walk {metres} m, {change.allow} min',
        ),
    )


def leg_rows(
    *, leg: Leg, pattern: TripPattern | None,
) -> tuple[LegStop, ...]:
    """
    List every row of a leg.

    Parameters
    ----------
    leg : Leg
        The leg.
    pattern : TripPattern | None
        Its trip, or None while loading.

    Returns
    -------
    tuple[LegStop, ...]
        One row per call; empty while loading.
    """
    calls = () if pattern is None else leg_calls(
        item=leg.item,
        pattern=pattern,
        start=leg.start.stop_id,
        end=leg.end.stop_id,
    )
    return leg_stops(calls=calls)


def leg_section(
    *, leg: Leg, rows: tuple[LegStop, ...], loaded: bool,
) -> LegSection:
    """
    Build one leg's section.

    Parameters
    ----------
    leg : Leg
        The leg.
    rows : tuple[LegStop, ...]
        Its rows, already trimmed.
    loaded : bool
        False while its pattern has not loaded.

    Returns
    -------
    LegSection
        The section; one not loaded, or with no rows, reads `LOADING`.
    """
    departure = leg.item.departure
    return LegSection(
        route=departure.route,
        mode=departure.mode,
        title=f'{leg.start.name} → {leg.end.name}',
        note=leg.note,
        stops=rows,
        empty=LOADING if not loaded or not rows else None,
    )


def pair_sections(
    *,
    pair: Pair,
    pin: Pin,
    patterns: Mapping[str, TripPattern | None],
) -> tuple[LegSection, ...]:
    """
    Build a section per leg of a pair, sharing the strip's rows.

    Parameters
    ----------
    pair : Pair
        The selected pair.
    pin : Pin
        Connection pin.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns by trip ID.

    Returns
    -------
    tuple[LegSection, ...]
        The sections, leg 1 first.
    """
    legs = pair_legs_of(pair=pair, pin=pin)
    found = [patterns.get(leg.item.departure.trip_id) for leg in legs]
    rows = [
        leg_rows(leg=leg, pattern=trip)
        for leg, trip in zip(legs, found, strict=True)
    ]
    given = shares(counts=tuple(len(each) for each in rows))
    return tuple(
        leg_section(
            leg=leg,
            rows=trimmed(calls=each, share=share),
            loaded=trip is not None,
        )
        for leg, trip, each, share in zip(
            legs, found, rows, given, strict=True,
        )
    )


def build_connection_strip(
    *,
    stop_view: StopView,
    pin: Pin,
    pairs: Sequence[Pair],
    selected: int,
    patterns: Mapping[str, TripPattern | None],
) -> ConnectionStripView:
    """
    Build the connection Strip.

    Parameters
    ----------
    stop_view : StopView
        Leg 1's Stop view, for the titles, tag and banner.
    pin : Pin
        Connection pin.
    pairs : Sequence[Pair]
        Workable pairs in leg-1 departure order.
    selected : int
        Requested pair; clamped to the last.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns by trip ID.

    Returns
    -------
    ConnectionStripView
        View-model.
    """
    index = max(0, min(selected, len(pairs) - 1))
    sections = () if not pairs else pair_sections(
        pair=pairs[index], pin=pin, patterns=patterns,
    )
    return ConnectionStripView(
        origin=stop_view.origin,
        destination=stop_view.destination,
        clock=stop_view.clock,
        tag=stop_view.tag,
        banner=stop_view.banner,
        warning=stop_view.warning,
        sections=sections,
        more=stop_view.more,
        planned=stop_view.planned,
        pairs=pair_rows(pairs=pairs),
        selected=index,
        empty=no_pair_text(stop_view=stop_view, pairs=pairs),
    )
