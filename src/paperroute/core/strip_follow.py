"""The rows below a strip's From, laid out along the train it follows."""

from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import replace

from paperroute.core.live import LiveDeparture
from paperroute.core.model import StopPlace
from paperroute.core.patterns import PatternStop
from paperroute.core.reach import Reach, ReachRow, RowKind
from paperroute.core.stop_view import clock_text, not_departed
from paperroute.core.strip_times import (
    TripInputs,
    arrivals_after,
    boardable,
    live_strip,
    stops_after,
)

type Call = tuple[str, PatternStop]


def origin_of(*, reach: Reach) -> int | None:
    """
    Find the From row.

    Parameters
    ----------
    reach : Reach
        The reach.

    Returns
    -------
    int | None
        Its index, or None for a reach without one.
    """
    return next(
        (
            index
            for index, row in enumerate(reach.rows)
            if row.kind is RowKind.ORIGIN
        ),
        None,
    )


def above_stations(*, reach: Reach, origin: int) -> frozenset[str]:
    """
    List the stations laid out above the From, approach rows aside.

    Parameters
    ----------
    reach : Reach
        The reach.
    origin : int
        The From row.

    Returns
    -------
    frozenset[str]
        Station IDs of the line and branch rows above the From, which
        trips leaving the other way call at.
    """
    return frozenset(
        row.place.stop_id
        for row in reach.rows[:origin]
        if row.kind in (RowKind.MAIN, RowKind.BRANCH)
    )


def boardable_items(*, trips: TripInputs, origin: str, target: str | None
                    ) -> Iterator[LiveDeparture]:
    """
    List the strip's departures not yet gone that can be taken.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    origin : str
        The From station ID.
    target : str | None
        The Destination's station ID, or None for any departure.

    Yields
    ------
    LiveDeparture
        The departures by their live time, earliest first.
    """
    upcoming = not_departed(
        departures=live_strip(trips=trips), now=trips.now,
    )
    for item in sorted(upcoming, key=lambda each: each.departs):
        if boardable(item=item, trips=trips, origin=origin, target=target):
            yield item


def followed_item(
    *,
    trips: TripInputs,
    origin: str,
    target: str | None,
    above: Collection[str],
) -> LiveDeparture | None:
    """
    Choose the train the rows below the From follow.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    origin : str
        The From station ID.
    target : str | None
        The Destination's station ID, or None for any departure.
    above : Collection[str]
        Stations laid out above the From.

    Returns
    -------
    LiveDeparture | None
        The first departure that can be taken whose next call is not
        above the From. None when there is none, or when the first one
        that can be taken has no loaded pattern yet.
    """
    for item in boardable_items(trips=trips, origin=origin, target=target):
        first = next(
            iter(arrivals_after(item=item, trips=trips, origin=origin)),
            None,
        )
        if first is None:
            return None
        if first[0] not in above:
            return item
    return None


def hooks_of(*, reach: Reach, origin: int) -> dict[str, list[ReachRow]]:
    """
    Group the exit rows below the From by their junction.

    Parameters
    ----------
    reach : Reach
        The reach.
    origin : int
        The From row.

    Returns
    -------
    dict[str, list[ReachRow]]
        Exit rows by junction station ID, the From's under its own.
    """
    found: dict[str, list[ReachRow]] = {}
    for row in reach.rows[origin + 1:]:
        if row.kind is RowKind.EXIT and row.parent is not None:
            junction = reach.rows[row.parent].place.stop_id
            found.setdefault(junction, []).append(row)
    return found


def place_for(*, station: str, stop: PatternStop,
              places: Mapping[str, StopPlace]) -> StopPlace:
    """
    Give a followed call's station.

    Parameters
    ----------
    station : str
        The call's strip station ID.
    stop : PatternStop
        The call.
    places : Mapping[str, StopPlace]
        The stations already on the strip, by ID.

    Returns
    -------
    StopPlace
        The strip's own station, else one named and placed from the call.
    """
    return places.get(station) or StopPlace(
        stop_id=station, name=stop.name or station, point=stop.point,
    )


def with_hooks(*, rows: list[ReachRow], station: str,
               hooks: dict[str, list[ReachRow]], path: Collection[str]
               ) -> None:
    """
    Hang a junction's exits off the row just added for it.

    Parameters
    ----------
    rows : list[ReachRow]
        Rows so far, the junction's last; extended in place.
    station : str
        The junction's station ID.
    hooks : dict[str, list[ReachRow]]
        Exit rows by junction; a junction's are taken, so a station
        visited twice hooks only at its first visit.
    path : Collection[str]
        Stations the followed train calls at; exits to them are dropped.
    """
    junction = len(rows) - 1
    rows.extend(
        replace(row, parent=junction)
        for row in hooks.pop(station, ())
        if row.place.stop_id not in path
    )


def path_rows(*, reach: Reach, origin: int, calls: Sequence[Call]
              ) -> tuple[ReachRow, ...]:
    """
    Lay the followed calls out below the From.

    Parameters
    ----------
    reach : Reach
        The reach.
    origin : int
        The From row.
    calls : Sequence[Call]
        The followed train's strip stations and calls after the From.

    Returns
    -------
    tuple[ReachRow, ...]
        The rows down to the From, unchanged, then one line row per call
        in calling order, each with its exits from the strip below it.
    """
    places = {
        row.place.stop_id: row.place
        for row in reach.rows
        if row.kind is not RowKind.EXIT
    }
    path = {station for station, _ in calls}
    hooks = hooks_of(reach=reach, origin=origin)
    rows = list(reach.rows[:origin + 1])
    with_hooks(
        rows=rows, station=rows[-1].place.stop_id, hooks=hooks, path=path,
    )
    back = origin
    for station, stop in calls:
        rows.append(ReachRow(
            place=place_for(station=station, stop=stop, places=places),
            kind=RowKind.MAIN,
            depth=0,
            above=False,
            parent=back,
        ))
        back = len(rows) - 1
        with_hooks(rows=rows, station=station, hooks=hooks, path=path)
    return tuple(rows)


def followed_reach(*, reach: Reach, trips: TripInputs,
                   target: str | None) -> Reach:
    """
    Lay a strip's rows below its From along the train it follows.

    Parameters
    ----------
    reach : Reach
        The strip's fixed rows.
    trips : TripInputs
        The strip's departures and live data.
    target : str | None
        The Destination's station ID, or None.

    Returns
    -------
    Reach
        The rows above the From as they are, and below it the calls of
        the next train that reaches the Destination (or of the next one
        at all, with none), through the trips it runs on as. The fixed
        rows when there is no such train or its pattern isn't loaded.
    """
    origin = origin_of(reach=reach)
    if origin is None:
        return reach
    station = reach.rows[origin].place.stop_id
    item = followed_item(
        trips=trips,
        origin=station,
        target=target,
        above=above_stations(reach=reach, origin=origin),
    )
    calls = [] if item is None else [
        (place, stop)
        for place, stop, _ in stops_after(
            item=item, trips=trips, origin=station,
        )
    ]
    if item is None or not calls:
        return reach
    rows = path_rows(reach=reach, origin=origin, calls=calls)
    return Reach(
        rows=rows,
        stations=sum(
            row.kind in (RowKind.MAIN, RowKind.BRANCH) for row in rows
        ),
        station_of=reach.station_of,
        trip=item.departure.trip_id,
    )


def followed_text(*, trips: TripInputs, reach: Reach) -> dict[int, str]:
    """
    Give the rows below the From the followed train's times.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, laid out by `followed_reach`.

    Returns
    -------
    dict[int, str]
        Clock time by line row below the From, in calling order; empty
        when the rows follow no train or it is no longer listed.
    """
    origin = origin_of(reach=reach)
    item = next(
        (
            each
            for each in live_strip(trips=trips)
            if each.departure.trip_id == reach.trip
        ),
        None,
    )
    if origin is None or item is None:
        return {}
    rows = [
        index
        for index in range(origin + 1, len(reach.rows))
        if reach.rows[index].kind is RowKind.MAIN
    ]
    calls = arrivals_after(
        item=item, trips=trips, origin=reach.rows[origin].place.stop_id,
    )
    return {
        index: clock_text(moment=moment)
        for index, (_, moment) in zip(rows, calls)
    }
