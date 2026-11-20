"""The main line of a train Strip with a Destination."""

from collections.abc import Sequence

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
from paperroute_transit.core.train_network import hop_of


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

    Returns
    -------
    list[ReachRow]
        Rows below the From, parented from the From at index 0 on.
    """
    rows: list[ReachRow] = []
    last = data.origin.stop_id
    for station, stop, _ in stops_after(
        item=main, trips=trips, origin=last,
    ):
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
        The Destination station ID.

    Returns
    -------
    Reach
        Earlier calls, the From, then the calls and passed stations to
        the trip's end. A station is listed once, at its first place.
    """
    origin = ReachRow(
        place=data.origin, kind=RowKind.ORIGIN, depth=0, above=False,
        parent=None,
    )
    started = (
        None if main is None else started_pattern(
            item=main, trips=trips, origin=data.origin.stop_id,
        )
    )
    if main is None or started is None:
        return Reach(rows=(origin,), stations=0, station_of=data.station_of)
    seen = {data.origin.stop_id}
    ahead = ahead_rows(data=data, trips=trips, main=main, seen=seen)
    pattern, call, _ = started
    drop = seen | {destination or ''}
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
    return stacked(
        data=data, main=main, before=before, origin=origin, ahead=ahead,
    )


def stacked(
    *,
    data: ReachInput,
    main: LiveDeparture,
    before: list[ReachRow],
    origin: ReachRow,
    ahead: list[ReachRow],
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
        Earlier rows, nearest first, with placeholder parents.
    origin : ReachRow
        The From row.
    ahead : list[ReachRow]
        Rows below the From, parented from index 0.

    Returns
    -------
    Reach
        The rows top to bottom.
    """
    top = len(before)
    above = [
        ReachRow(
            place=row.place, kind=row.kind, depth=0, above=True,
            parent=index + 1,
        )
        for index, row in enumerate(reversed(before))
    ]
    below = [
        ReachRow(
            place=row.place, kind=row.kind, depth=0, above=False,
            parent=top + (row.parent or 0), inside=row.inside,
        )
        for row in ahead
    ]
    return Reach(
        rows=(*above, origin, *below),
        stations=sum(row.kind is RowKind.MAIN for row in below),
        station_of=data.station_of,
        trip=main.departure.trip_id,
        day=main.departure.service_day,
    )
