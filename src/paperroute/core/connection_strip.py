"""The Strip of a connection pin: a section per leg, then the pairs."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from paperroute.core.chains import Chain
from paperroute.core.connection_model import change_walk
from paperroute.core.connection_view import PairRow, chain_rows, no_pair_text
from paperroute.core.live import LiveDeparture
from paperroute.core.map_view import MapView, is_fresh, position_age
from paperroute.core.model import (
    Change,
    Mode,
    Pin,
    StopPlace,
    service_seconds,
    service_time,
    shifted,
)
from paperroute.core.patterns import PatternStop, TripPattern, hop_near
from paperroute.core.positions import PositionsSnapshot, VehiclePosition
from paperroute.core.rides import rides_of
from paperroute.core.stop_view import PinTag, StopView, clock_text, stops_text
from paperroute.core.strip_model import LOADING, NO_LEG_STOPS
from paperroute.core.strip_times import from_call, joined_patterns

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
        The leg's first and last rows, with a fold row between.
    empty : str | None
        Text drawn in place of the rows, or None.
    vehicle : str | None
        Where its vehicle is before the boarding stop, or None.
    """

    route: str
    mode: Mode
    title: str
    note: str | None
    stops: tuple[LegStop, ...]
    empty: str | None
    vehicle: str | None = None


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
    *,
    item: LiveDeparture,
    last: LiveDeparture,
    pattern: TripPattern,
    start: str,
    end: str,
) -> tuple[LegCall, ...]:
    """
    List a leg's calls from boarding to getting off, with live times.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times, boarded.
    last : LiveDeparture
        The same trip on the leg got off; `item` unless staying on. Its
        arrival finds the call got off at, and times it.
    pattern : TripPattern
        Its trip.
    start : str
        Station ID boarded at.
    end : str
        Station ID got off at.

    Returns
    -------
    tuple[LegCall, ...]
        Each call and its time shifted by the departure's delay, and the
        last call by the delay of the live arrival that chose the pair.
        Passing calls are left out. Empty when either end is not found.
    """
    departure = item.departure
    first = from_call(
        departure=departure, pattern=pattern, origin=start, stations={},
    )
    final = None if first is None else end_call(
        item=last, pattern=pattern, start=first, end=end,
    )
    if first is None or final is None:
        return ()
    return tuple(
        (
            stop,
            shifted(
                moment=service_time(
                    day=departure.service_day,
                    seconds=stop.arrives if index == final else stop.departs,
                ),
                seconds=(
                    late_arriving(item=last) if index == final
                    else delay_of(item=item)
                ),
            ),
        )
        for index, stop in enumerate(pattern.stops)
        if first <= index <= final and not stop.passes
    )


def delay_of(*, item: LiveDeparture) -> int:
    """
    Give a departure's delay.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.

    Returns
    -------
    int
        Seconds it leaves after its timetabled time.
    """
    return int(item.departs.timestamp() - item.departure.departs.timestamp())


def late_arriving(*, item: LiveDeparture) -> int:
    """
    Give how late a departure arrives.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.

    Returns
    -------
    int
        Seconds it arrives after its timetabled time.
    """
    return int(item.arrives.timestamp() - item.departure.arrives.timestamp())


def fold_leg(*, calls: Sequence[LegCall]) -> tuple[LegStop, ...]:
    """
    Show a leg's first and last calls, folding the stops between.

    Parameters
    ----------
    calls : Sequence[LegCall]
        The leg's calls with their times.

    Returns
    -------
    tuple[LegStop, ...]
        The first call, a fold row '{n} stops' ('1 stop') when any are
        between, and the last call; empty with no calls.
    """
    rows = leg_stops(calls=calls)
    if len(rows) <= 2:
        return rows
    hidden = len(rows) - 2
    fold = LegStop(
        name=stops_text(stops=hidden), time='', mark=StopMark.FOLD,
    )
    return rows[0], fold, rows[-1]


def approach_note(
    *,
    leg: LiveDeparture,
    position: VehiclePosition | None,
    board: StopPlace,
    pattern: TripPattern | None,
) -> str | None:
    """
    Say how far a leg's vehicle is from the stop it is boarded at.

    Parameters
    ----------
    leg : LiveDeparture
        The leg.
    position : VehiclePosition | None
        Its vehicle's position, or None when not heard.
    board : StopPlace
        Where the leg is boarded.
    pattern : TripPattern | None
        Its trip, or None while loading.

    Returns
    -------
    str | None
        '{route} {n} stops before {board}' ('1 stop before') while the
        vehicle is on a hop before the boarding stop; None otherwise.
    """
    if position is None or pattern is None:
        return None
    start = from_call(
        departure=leg.departure, pattern=pattern, origin=board.stop_id,
        stations={},
    )
    hop = hop_near(pattern=pattern, point=position.point)
    if start is None or hop is None or hop[0] >= start:
        return None
    count = sum(
        not stop.passes for stop in pattern.stops[hop[0] + 1 : start + 1]
    )
    route = leg.departure.route
    return f'{route} {count} {"stop" if count == 1 else "stops"} ' + (
        f'before {board.name}'
    )


def leg_vehicle(
    *,
    leg: LiveDeparture,
    positions: Mapping[str, PositionsSnapshot],
    now: datetime,
    limit: timedelta,
) -> VehiclePosition | None:
    """
    Find a leg's vehicle while its position is fresh.

    Parameters
    ----------
    leg : LiveDeparture
        The leg.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed.
    now : datetime
        The frame time.
    limit : timedelta
        How old a position may be.

    Returns
    -------
    VehiclePosition | None
        The freshest report of its trip, by ID or alias, when fresh.
    """
    trips = {leg.departure.trip_id, *leg.departure.aliases}
    found = [
        (position_age(vehicle=item, snapshot=feed, now=now), item)
        for feed in positions.values()
        for item in feed.vehicles
        if item.trip_id in trips
    ]
    fresh = [pair for pair in found if is_fresh(age=pair[0], limit=limit)]
    return min(fresh, key=lambda pair: pair[0])[1] if fresh else None


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
    last : LiveDeparture | None
        The departure it is left on when staying on; None for `item`.
    """

    item: LiveDeparture
    start: StopPlace
    end: StopPlace
    note: str | None = None
    last: LiveDeparture | None = None


def walk_note(*, change: Change) -> str:
    """
    Say how far a change walks and how long it allows.

    Parameters
    ----------
    change : Change
        The change.

    Returns
    -------
    str
        'walk {metres} m, {allow} min', or 'change, {allow} min' when
        there is no walk (see `change_walk`).
    """
    metres = change_walk(change=change)
    if metres is None:
        return f'change, {change.allow} min'
    return f'walk {metres} m, {change.allow} min'


def chain_legs_of(*, chain: Chain, pin: Pin) -> tuple[Leg, ...]:
    """
    Name the rides a chain takes.

    Parameters
    ----------
    chain : Chain
        The chain.
    pin : Pin
        Connection pin.

    Returns
    -------
    tuple[Leg, ...]
        One leg per ride, a stay on folded into one; each after the
        first notes its walk and allowance.
    """
    return tuple(
        Leg(
            item=ride.item,
            last=ride.last,
            start=ride.start,
            end=ride.end,
            note=None if ride.change is None else walk_note(
                change=ride.change,
            ),
        )
        for ride in rides_of(chain=chain, pin=pin)
    )


def run_on_pattern(
    *,
    pattern: TripPattern,
    patterns: Mapping[str, TripPattern | None],
    leg: Leg,
) -> TripPattern:
    """
    Join a trip's calls to those of the trips it runs on as.

    Parameters
    ----------
    pattern : TripPattern
        The boarded trip.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns by trip ID.
    leg : Leg
        The leg, for its service day.

    Returns
    -------
    TripPattern
        The trip with each loaded joined trip's calls after its own; a
        joined trip's first call is where the vehicle already is, so it
        is left out.
    """
    joined = joined_patterns(
        pattern=pattern,
        patterns=patterns,
        day=leg.item.departure.service_day,
    )
    stops = pattern.stops + tuple(
        stop for more in joined for stop in more.stops[1:]
    )
    return replace(pattern, stops=stops, along=None)


def leg_rows(
    *, leg: Leg, patterns: Mapping[str, TripPattern | None],
) -> tuple[LegStop, ...]:
    """
    List every row of a leg.

    Parameters
    ----------
    leg : Leg
        The leg.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns by trip ID.

    Returns
    -------
    tuple[LegStop, ...]
        The folded rows, following the trip on through the trips it
        joins onto; empty while loading or when the trip never reaches
        the leg's end.
    """
    pattern = patterns.get(leg.item.departure.trip_id)
    calls = () if pattern is None else leg_calls(
        item=leg.item,
        last=leg.last or leg.item,
        pattern=run_on_pattern(pattern=pattern, patterns=patterns, leg=leg),
        start=leg.start.stop_id,
        end=leg.end.stop_id,
    )
    return fold_leg(calls=calls)


def leg_section(
    *,
    leg: Leg,
    rows: tuple[LegStop, ...],
    loaded: bool,
    vehicle: str | None = None,
) -> LegSection:
    """
    Build one leg's section.

    Parameters
    ----------
    leg : Leg
        The leg.
    rows : tuple[LegStop, ...]
        Its rows, already folded.
    loaded : bool
        False while its pattern has not come back.
    vehicle : str | None
        Where its vehicle is before the boarding stop, or None.

    Returns
    -------
    LegSection
        The section; one not loaded reads `LOADING`, and one loaded
        with no rows `NO_LEG_STOPS`.
    """
    departure = leg.item.departure
    return LegSection(
        route=departure.route,
        mode=departure.mode,
        title=f'{leg.start.name} → {leg.end.name}',
        note=leg.note,
        stops=rows,
        empty=(
            LOADING if not loaded
            else NO_LEG_STOPS if not rows
            else None
        ),
        vehicle=vehicle,
    )


def chain_sections(
    *,
    chain: Chain,
    pin: Pin,
    patterns: Mapping[str, TripPattern | None],
    positions: Mapping[str, PositionsSnapshot],
    now: datetime,
    stale_after: timedelta,
) -> tuple[LegSection, ...]:
    """
    Build a section per ride of a chain.

    Parameters
    ----------
    chain : Chain
        The selected chain.
    pin : Pin
        Connection pin.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns by trip ID.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed.
    now : datetime
        The frame time.
    stale_after : timedelta
        How old a position may be.

    Returns
    -------
    tuple[LegSection, ...]
        The sections, the first ride first.
    """
    sections = []
    for leg in chain_legs_of(chain=chain, pin=pin):
        trip_id = leg.item.departure.trip_id
        trip = patterns.get(trip_id)
        note = approach_note(
            leg=leg.item,
            position=leg_vehicle(
                leg=leg.item,
                positions=positions,
                now=now,
                limit=stale_after,
            ),
            board=leg.start,
            pattern=trip,
        )
        sections.append(leg_section(
            leg=leg,
            rows=leg_rows(leg=leg, patterns=patterns),
            loaded=trip_id in patterns,
            vehicle=note,
        ))
    return tuple(sections)


def build_connection_strip(
    *,
    stop_view: StopView,
    pin: Pin,
    chains: Sequence[Chain],
    selected: int,
    patterns: Mapping[str, TripPattern | None],
    positions: Mapping[str, PositionsSnapshot],
    now: datetime,
    stale_after: timedelta,
) -> ConnectionStripView:
    """
    Build the connection Strip.

    Parameters
    ----------
    stop_view : StopView
        Leg 1's Stop view, for the titles, tag and banner.
    pin : Pin
        Connection pin.
    chains : Sequence[Chain]
        Workable chains in first-leg departure order.
    selected : int
        Requested chain; clamped to the last.
    patterns : Mapping[str, TripPattern | None]
        Loaded patterns by trip ID.
    positions : Mapping[str, PositionsSnapshot]
        The latest download of each positions feed.
    now : datetime
        The frame time.
    stale_after : timedelta
        How old a position may be.

    Returns
    -------
    ConnectionStripView
        View-model.
    """
    index = max(0, min(selected, len(chains) - 1))
    sections = () if not chains else chain_sections(
        chain=chains[index],
        pin=pin,
        patterns=patterns,
        positions=positions,
        now=now,
        stale_after=stale_after,
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
        pairs=chain_rows(chains=chains, pin=pin, now=now),
        selected=index,
        empty=no_pair_text(stop_view=stop_view, chains=chains),
    )
