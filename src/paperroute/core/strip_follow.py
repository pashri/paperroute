"""The rows below a strip's From, laid out along the next trains."""

from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from paperroute.core.live import LiveDeparture
from paperroute.core.model import StopPlace
from paperroute.core.patterns import PatternStop
from paperroute.core.reach import Reach, ReachRow, RowKind
from paperroute.core.stop_view import clock_text, not_departed
from paperroute.core.strip_model import CarryNote
from paperroute.core.strip_times import (
    TripInputs,
    boardable,
    live_strip,
    stops_after,
)

FOLLOWED: Final[int] = 4  # next trains the rows below the From come from
type Call = tuple[str, PatternStop]
type Served = list[tuple[str, PatternStop, datetime]]
type Timed = tuple[datetime, int]


@dataclass(frozen=True)
class Steady:
    """
    The rows below a From, merged from the next trains' stops.

    Attributes
    ----------
    items : tuple[LiveDeparture, ...]
        The trains, earliest first.
    rows : list[Call]
        Stations and calls in calling order.
    times : list[list[Timed]]
        Each row's arrival times and the index of the train of each.
    """

    items: tuple[LiveDeparture, ...]
    rows: list[Call]
    times: list[list[Timed]]


def served_after(*, item: LiveDeparture, trips: TripInputs, origin: str
                 ) -> Iterator[tuple[str, PatternStop, datetime]]:
    """
    List the stations a departure stops at after the From.

    Parameters
    ----------
    item : LiveDeparture
        Departure with live times.
    trips : TripInputs
        The strip's loaded patterns and station map.
    origin : str
        The From station ID.

    Yields
    ------
    tuple[str, PatternStop, datetime]
        The calls of `stops_after`, without those run through with no
        pick-up or set-down, and with neighbouring calls at one station,
        the From's included, kept once, at the first.
    """
    before = origin
    for station, stop, moment in stops_after(
        item=item, trips=trips, origin=origin,
    ):
        if not stop.passes and station != before:
            yield station, stop, moment
        if not stop.passes:
            before = station


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


def followed_runs(
    *,
    trips: TripInputs,
    origin: str,
    target: str | None,
    above: Collection[str],
) -> list[tuple[LiveDeparture, Served]]:
    """
    Choose the trains the rows below the From are built from.

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
    list[tuple[LiveDeparture, Served]]
        Up to `FOLLOWED` departures that can be taken whose next stop is
        not above the From, earliest first, each with the stops it makes
        after the From (see `served_after`). Empty when the first one
        that can be taken has no loaded pattern yet; a later one without
        one is passed over.
    """
    found: list[tuple[LiveDeparture, Served]] = []
    for item in boardable_items(trips=trips, origin=origin, target=target):
        calls = list(served_after(item=item, trips=trips, origin=origin))
        if not calls and not found:
            return []
        if calls and calls[0][0] not in above:
            found.append((item, calls))
        if len(found) == FOLLOWED:
            break
    return found


def merge_into(*, rows: list[Call], times: list[list[Timed]],
               served: Served, train: int) -> None:
    """
    Add one train's stops to rows built from earlier trains.

    Parameters
    ----------
    rows : list[Call]
        Stations and calls in order; extended in place.
    times : list[list[Timed]]
        Each row's times and the trains they are of; extended in place.
    served : Served
        The train's stops.
    train : int
        The train's index.

    Notes
    -----
    Each stop is matched to the next row at its station. Stops between
    two matched ones are put in before the second. Stops after the last
    match run on past the rows' end when that match is the last row;
    otherwise they are on a branch the rows don't follow, and are left
    out.
    """
    at = -1
    pending: Served = []
    for station, stop, moment in served:
        match = next(
            (j for j in range(at + 1, len(rows)) if rows[j][0] == station),
            None,
        )
        if match is None:
            pending.append((station, stop, moment))
            continue
        rows[match:match] = [(each, call) for each, call, _ in pending]
        times[match:match] = [[(when, train)] for _, _, when in pending]
        at = match + len(pending)
        times[at].append((moment, train))
        pending = []
    if at == len(rows) - 1:
        rows.extend((each, call) for each, call, _ in pending)
        times.extend([(when, train)] for _, _, when in pending)


def steady_of(*, reach: Reach, trips: TripInputs, target: str | None
              ) -> Steady | None:
    """
    Merge the next trains' stops below the From into one run of rows.

    Parameters
    ----------
    reach : Reach
        The reach; its rows down to the From are used.
    trips : TripInputs
        The strip's departures and live data.
    target : str | None
        The Destination's station ID, or None.

    Returns
    -------
    Steady | None
        The trains, rows in calling order and each row's times; the
        first train's stops in order, with the stops later trains make
        between them added. None with no From or no train to build on.
    """
    origin = origin_of(reach=reach)
    if origin is None:
        return None
    runs = followed_runs(
        trips=trips,
        origin=reach.rows[origin].place.stop_id,
        target=target,
        above=above_stations(reach=reach, origin=origin),
    )
    if not runs:
        return None
    rows = [(station, stop) for station, stop, _ in runs[0][1]]
    times = [[(moment, 0)] for _, _, moment in runs[0][1]]
    for train, (_, served) in enumerate(runs[1:], start=1):
        merge_into(rows=rows, times=times, served=served, train=train)
    return Steady(
        items=tuple(item for item, _ in runs), rows=rows, times=times,
    )


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
    Lay a strip's rows below its From along the next trains.

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
        The rows above the From as they are, and below it the stations
        the next trains that reach the Destination (or the next trains
        at all, with none) stop at, through the trips they run on as
        (see `steady_of`). The fixed rows while the first train's
        pattern isn't loaded or there is none.
    """
    origin = origin_of(reach=reach)
    steady = steady_of(reach=reach, trips=trips, target=target)
    if origin is None or steady is None:
        return reach
    rows = path_rows(reach=reach, origin=origin, calls=steady.rows)
    first = steady.items[0].departure
    return Reach(
        rows=rows,
        stations=sum(
            row.kind in (RowKind.MAIN, RowKind.BRANCH) for row in rows
        ),
        station_of=reach.station_of,
        trip=first.trip_id,
        day=first.service_day,
    )


def line_rows(*, reach: Reach, steady: Steady) -> list[int] | None:
    """
    Pair the rows below the From with the merged stops.

    Parameters
    ----------
    reach : Reach
        The reach, laid out by `followed_reach`.
    steady : Steady
        The merged stops.

    Returns
    -------
    list[int] | None
        The line row of each merged stop, or None when the rows were
        laid out from other stops.
    """
    start = (origin_of(reach=reach) or 0) + 1
    rows = [
        index
        for index in range(start, len(reach.rows))
        if reach.rows[index].kind is RowKind.MAIN
    ]
    same = [reach.rows[index].place.stop_id for index in rows] == [
        station for station, _ in steady.rows
    ]
    return rows if same else None


def followed_text(*, trips: TripInputs, reach: Reach, target: str | None
                  ) -> dict[int, str]:
    """
    Give the rows below the From the next train's times.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, laid out by `followed_reach`.
    target : str | None
        The Destination's station ID, or None.

    Returns
    -------
    dict[int, str]
        Clock time by line row below the From where the next of the
        trains stops; none on the rows it skips or doesn't reach. Empty
        when the rows no longer match the trains.
    """
    steady = steady_of(reach=reach, trips=trips, target=target)
    rows = None if steady is None else line_rows(reach=reach, steady=steady)
    if steady is None or rows is None:
        return {}
    return {
        index: clock_text(moment=moment)
        for index, timed in zip(rows, steady.times)
        for moment, train in timed
        if train == 0
    }


def change_at(*, trips: TripInputs, item: LiveDeparture) -> str | None:
    """
    Find the station where a train runs on as another route.

    Parameters
    ----------
    trips : TripInputs
        The strip's loaded patterns and station map.
    item : LiveDeparture
        The train.

    Returns
    -------
    str | None
        The last station of its loaded trip when it carries on; else
        None.
    """
    pattern = trips.patterns.get(item.departure.trip_id)
    if item.departure.facts.then is None or not pattern or not pattern.stops:
        return None
    last = pattern.stops[-1].station
    return trips.station_of.get(last, last)


def followed_note(*, trips: TripInputs, reach: Reach, target: str | None
                  ) -> tuple[int, CarryNote] | None:
    """
    Place the carry-on note on the rows below the From.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, laid out by `followed_reach`.
    target : str | None
        The Destination's station ID, or None.

    Returns
    -------
    tuple[int, CarryNote] | None
        The row where the next of the trains runs on as another route,
        with that change; None when it doesn't, or not on these rows.
    """
    steady = steady_of(reach=reach, trips=trips, target=target)
    rows = None if steady is None else line_rows(reach=reach, steady=steady)
    if steady is None or rows is None:
        return None
    item = steady.items[0]
    then = item.departure.facts.then
    change = change_at(trips=trips, item=item)
    found = next(
        (
            index
            for index, (station, _), timed in zip(
                rows, steady.rows, steady.times,
            )
            if station == change and any(train == 0 for _, train in timed)
        ),
        None,
    )
    if then is None or found is None:
        return None
    return found, CarryNote(
        route=item.departure.route, then=f'{then.route} {then.headsign}',
    )
