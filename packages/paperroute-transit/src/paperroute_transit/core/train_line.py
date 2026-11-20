"""The main line of a train Strip with a Destination."""

from collections.abc import Iterable, Sequence
from dataclasses import replace

from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.reach import Reach, ReachInput, ReachRow, RowKind
from paperroute_transit.core.strip_follow import place_for
from paperroute_transit.core.strip_times import (
    TripInputs,
    started_pattern,
    stops_after,
)
from paperroute_transit.core.train_exits import TrainExit, train_exits
from paperroute_transit.core.train_network import hop_of
from paperroute_transit.core.train_sides import first_station, north_hops


def main_departure(
    *,
    items: Sequence[LiveDeparture],
    trips: TripInputs,
    origin: str,
    picked: int | None,
) -> LiveDeparture | None:
    """
    Choose the departure whose train is the main line.

    Parameters
    ----------
    items : Sequence[LiveDeparture]
        The listed departures, soonest first.
    trips : TripInputs
        The strip's loaded patterns and station map.
    origin : str
        The From station ID.
    picked : int | None
        Index of the picked item, if any.

    Returns
    -------
    LiveDeparture | None
        The picked item as it is, even when cancelled. Otherwise the
        first item that is not cancelled and whose pattern is loaded and
        calls at the From; the same scan runs when the index is out of
        range. None when there is no such item.
    """
    if picked is not None and 0 <= picked < len(items):
        return items[picked]
    return next(
        (
            item
            for item in items
            if not item.cancelled
            and started_pattern(item=item, trips=trips, origin=origin)
        ),
        None,
    )


def earlier(
    *, pattern: TripPattern, call: int, origin: str,
) -> tuple[PatternStop, ...]:
    """
    List the calls a train made before the From.

    Parameters
    ----------
    pattern : TripPattern
        The followed trip.
    call : int
        Index of its call at the From.
    origin : str
        The From station ID.

    Returns
    -------
    tuple[PatternStop, ...]
        The calls with a pick-up or set-down before `call`, nearest
        first, back to the one after the previous call at the From.
    """
    found: list[PatternStop] = []
    for stop in reversed(pattern.stops[:call]):
        if stop.station == origin:
            break
        if not stop.passes:
            found.append(stop)
    return tuple(found)


def ahead_rows(
    *,
    data: ReachInput,
    trips: TripInputs,
    main: LiveDeparture,
    seen: set[str],
    cut: bool = False,
) -> list[ReachRow]:
    """
    Lay out the calls and passed stations after the From.

    Parameters
    ----------
    data : ReachInput
        The From and its network.
    trips : TripInputs
        The strip's loaded patterns and station map.
    main : LiveDeparture
        The followed departure.
    seen : set[str]
        Station IDs already listed; each station laid out is added.
    cut : bool
        Stop where the train calls at the From again.

    Returns
    -------
    list[ReachRow]
        Rows away from the From, parented from the From at index 0 on.
    """
    rows: list[ReachRow] = []
    last = data.origin.stop_id
    for station, stop, _ in stops_after(
        item=main, trips=trips, origin=last,
    ):
        if cut and station == data.origin.stop_id:
            break
        previous, last = last, station
        if station in seen:
            continue
        hop = (
            hop_of(network=data.network, a=previous, b=station)
            if data.network
            else None
        )
        crossed = () if hop is None else hop.passed
        for passed in crossed:
            if passed not in seen:
                seen.add(passed)
                rows.append(row_of(
                    place=data.places.get(passed) or StopPlace(
                        stop_id=passed, name=passed, point=None,
                    ),
                    kind=RowKind.PASSED,
                    parent=len(rows),
                ))
        seen.add(station)
        rows.append(row_of(
            place=place_for(station=station, stop=stop, places=data.places),
            kind=RowKind.MAIN,
            parent=len(rows),
            inside=hop is None or hop.inside,
        ))
    return rows


def row_of(
    *, place: StopPlace, kind: RowKind, parent: int, inside: bool = True,
) -> ReachRow:
    """
    Make a row below the From on the main line.

    Parameters
    ----------
    place : StopPlace
        The station.
    kind : RowKind
        The row kind.
    parent : int
        Index of the row above it.
    inside : bool
        Whether the hop arriving at it is inside the track.

    Returns
    -------
    ReachRow
        The row.
    """
    return ReachRow(
        place=place,
        kind=kind,
        depth=0,
        above=False,
        parent=parent,
        inside=inside,
    )


def train_reach(
    *,
    data: ReachInput,
    trips: TripInputs,
    main: LiveDeparture | None,
    destination: str | None,
    above: LiveDeparture | None = None,
    loop_name: str = '',
) -> Reach:
    """
    Lay out a Strip along the followed train.

    Parameters
    ----------
    data : ReachInput
        The From and the kind's network.
    trips : TripInputs
        The strip's loaded patterns and station map.
    main : LiveDeparture | None
        The followed departure; None for the From alone.
    destination : str | None
        The Destination station ID. With none, each train is cut where
        it calls at the From again; with one, trains are never cut.
    above : LiveDeparture | None
        The train read upward above the From. When given, no earlier
        calls are listed.
    loop_name : str
        The name of a train that returns to the From; empty drops them.

    Returns
    -------
    Reach
        Earlier calls, the From, then the calls and passed stations to
        the trip's end. A station is listed once, at its first place:
        the train below claims its stations before the one above. Each
        train's exit rows follow their junction rows. The work grows
        with the runs through the From and their tail stations.
    """
    origin = ReachRow(
        place=data.origin, kind=RowKind.ORIGIN, depth=0, above=False,
        parent=None,
    )
    lead = next(
        (
            item
            for item in (main, above)
            if item is not None and started_pattern(
                item=item, trips=trips, origin=data.origin.stop_id,
            )
        ),
        None,
    )
    if lead is None:
        return Reach(
            rows=(origin,),
            stations=0,
            station_of=data.station_of,
            source=data,
        )
    seen = {data.origin.stop_id}
    cut = destination is None
    ahead = side_rows(
        data=data, trips=trips, item=main, seen=seen, cut=cut,
    )
    before = (
        earlier_rows(
            data=data, trips=trips, main=lead, drop=seen | {destination or ''},
        )
        if above is None
        else side_rows(
            data=data, trips=trips, item=above, seen=seen, cut=cut,
        )
    )
    sides = (
        None
        if data.network is None
        else north_hops(
            network=data.network,
            origin=data.origin.stop_id,
            places={data.origin.stop_id: data.origin, **data.places},
        )
    )
    return stacked(
        data=data,
        main=lead,
        before=(
            before
            if above is None
            else with_exits(
                data=data, trips=trips, item=above, rows=before,
                sides=sides, side=0, loop_name=loop_name,
            )
        ),
        origin=origin,
        upward=above,
        ahead=with_exits(
            data=data, trips=trips, item=main, rows=ahead,
            sides=sides, side=None if above is None else 1,
            loop_name=loop_name,
        ),
    )


def side_rows(
    *,
    data: ReachInput,
    trips: TripInputs,
    item: LiveDeparture | None,
    seen: set[str],
    cut: bool,
) -> list[ReachRow]:
    """
    Lay out one train's rows after the From.

    Parameters
    ----------
    data : ReachInput
        The From and its network.
    trips : TripInputs
        The strip's loaded patterns and station map.
    item : LiveDeparture | None
        The departure; None gives no rows.
    seen : set[str]
        Station IDs already listed; each station laid out is added.
    cut : bool
        Stop where the train calls at the From again.

    Returns
    -------
    list[ReachRow]
        The rows, parented from the From at index 0 on; none when the
        pattern isn't loaded.
    """
    if item is None:
        return []
    return ahead_rows(data=data, trips=trips, main=item, seen=seen, cut=cut)


def earlier_rows(
    *,
    data: ReachInput,
    trips: TripInputs,
    main: LiveDeparture,
    drop: set[str],
) -> list[ReachRow]:
    """
    Lay out the calls the followed train made before the From.

    Parameters
    ----------
    data : ReachInput
        The From and its station map.
    trips : TripInputs
        The strip's loaded patterns and station map.
    main : LiveDeparture
        The followed departure.
    drop : set[str]
        Station IDs not to list; each station laid out is added.

    Returns
    -------
    list[ReachRow]
        Earlier rows, nearest first, with the From as parent.
    """
    started = started_pattern(
        item=main, trips=trips, origin=data.origin.stop_id,
    )
    if started is None:
        return []
    pattern, call, _ = started
    before: list[ReachRow] = []
    for stop in earlier(
        pattern=pattern, call=call, origin=data.origin.stop_id,
    ):
        station = trips.station_of.get(stop.station, stop.station)
        if station not in drop:
            drop.add(station)
            before.append(row_of(
                place=place_for(
                    station=station, stop=stop, places=data.places,
                ),
                kind=RowKind.EARLIER,
                parent=0,
            ))
    return before


def with_exits(
    *,
    data: ReachInput,
    trips: TripInputs,
    item: LiveDeparture | None,
    rows: list[ReachRow],
    sides: tuple[frozenset[str], frozenset[str]] | None,
    side: int | None,
    loop_name: str,
) -> list[ReachRow]:
    """
    Hang a train's exit rows after their junction rows.

    Parameters
    ----------
    data : ReachInput
        The From and the kind's network.
    trips : TripInputs
        The strip's loaded patterns and station map.
    item : LiveDeparture | None
        The train whose rows these are.
    rows : list[ReachRow]
        The train's rows after the From, parented from the From at
        index 0.
    sides : tuple[frozenset[str], frozenset[str]] | None
        The first stations above and below, from `north_hops`, or None
        with no network.
    side : int | None
        Which side the train is on, 0 above and 1 below; None takes the
        side holding its first station.
    loop_name : str
        The name of a train that returns to the From.

    Returns
    -------
    list[ReachRow]
        The rows with exit rows added, parents moved to match; the rows
        unchanged when there is no network or train.
    """
    if data.network is None or sides is None or item is None:
        return rows
    origin = data.origin.stop_id
    first = first_station(
        item=item, trips=trips, origin=origin, network=data.network,
    )
    found = train_exits(
        network=data.network,
        origin=origin,
        line=[origin, *(row.place.stop_id for row in rows)],
        first_hops=(
            frozenset() if first is None else side_of_first(
                sides=sides, side=side, first=first,
            )
        ),
        loop_name=loop_name,
    )
    return hung(rows=rows, exits=found, data=data)


def side_of_first(
    *,
    sides: tuple[frozenset[str], frozenset[str]],
    side: int | None,
    first: str,
) -> frozenset[str]:
    """
    Take the first stations a train's exits may start with.

    Parameters
    ----------
    sides : tuple[frozenset[str], frozenset[str]]
        First stations above and below.
    side : int | None
        The train's side; None takes the one holding `first`.
    first : str
        The train's own first station.

    Returns
    -------
    frozenset[str]
        The side's first stations, or just the train's own when it is
        not among them.
    """
    if side is None:
        side = 0 if first in sides[0] else 1
    return sides[side] if first in sides[side] else frozenset({first})


def hung(
    *, rows: list[ReachRow], exits: Iterable[TrainExit], data: ReachInput,
) -> list[ReachRow]:
    """
    Put each exit's row after the row of its junction.

    Parameters
    ----------
    rows : list[ReachRow]
        Rows after the From, parented from the From at index 0.
    exits : Iterable[TrainExit]
        The exits, busiest first.
    data : ReachInput
        The From and its places.

    Returns
    -------
    list[ReachRow]
        The rows with each exit row after its junction's, and the other
        rows' parents moved to follow. An exit at the From comes first.
    """
    at: dict[str, list[TrainExit]] = {}
    for each in exits:
        at.setdefault(each.junction, []).append(each)
    out = exit_rows(exits=at.get(data.origin.stop_id, ()), parent=0, data=data)
    moved = {0: 0}
    for old, row in enumerate(rows, start=1):
        out.append(replace(row, parent=moved[row.parent or 0]))
        moved[old] = len(out)
        out.extend(exit_rows(
            exits=at.get(row.place.stop_id, ()), parent=len(out), data=data,
        ))
    return out


def exit_rows(
    *, exits: Sequence[TrainExit], parent: int, data: ReachInput,
) -> list[ReachRow]:
    """
    Make the exit rows of one junction.

    Parameters
    ----------
    exits : Sequence[TrainExit]
        The exits at the junction.
    parent : int
        Index of the junction's row.
    data : ReachInput
        The places, for names.

    Returns
    -------
    list[ReachRow]
        One EXIT row per exit at the junction's depth, 0, carrying the
        weekday trains in `trips` and the places named in `names`.
    """
    return [
        ReachRow(
            place=data.places.get(each.first) or StopPlace(
                stop_id=each.first, name=each.first, point=None,
            ),
            kind=RowKind.EXIT,
            depth=0,
            above=False,
            parent=parent,
            trips=each.trains,
            names=tuple(
                data.places[name].name if name in data.places else name
                for name in each.names
            ),
        )
        for each in exits
    ]


def stacked(
    *,
    data: ReachInput,
    main: LiveDeparture,
    before: list[ReachRow],
    origin: ReachRow,
    ahead: list[ReachRow],
    upward: LiveDeparture | None = None,
) -> Reach:
    """
    Stack earlier rows, the From and the rows below it into a Reach.

    Parameters
    ----------
    data : ReachInput
        The From and its station map.
    main : LiveDeparture
        The followed departure.
    before : list[ReachRow]
        Rows above the From, nearest first; their parents are reset.
    origin : ReachRow
        The From row.
    ahead : list[ReachRow]
        Rows below the From, parented from index 0.
    upward : LiveDeparture | None
        The train read upward above the From, if any.

    Returns
    -------
    Reach
        The rows top to bottom, with `data` as their source.
    """
    top = len(before)
    above = [
        replace(
            row,
            above=True,
            parent=(
                index + 1
                if row.kind is RowKind.EARLIER
                else top - (row.parent or 0)
            ),
        )
        for index, row in enumerate(reversed(before))
    ]
    below = [
        replace(row, above=False, parent=top + (row.parent or 0))
        for row in ahead
    ]
    return Reach(
        rows=(*above, origin, *below),
        stations=sum(row.kind is RowKind.MAIN for row in below),
        station_of=data.station_of,
        trip=main.departure.trip_id,
        day=main.departure.service_day,
        source=data,
        above_trip=None if upward is None else upward.departure.trip_id,
        above_day=None if upward is None else upward.departure.service_day,
    )
