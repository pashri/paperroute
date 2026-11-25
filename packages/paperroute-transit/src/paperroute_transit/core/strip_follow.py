"""The rows below a strip's From, laid out along the next trains."""

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from typing import Final

from paperroute_transit.core.live import LiveDeparture
from paperroute_transit.core.memo import SameMemo
from paperroute_transit.core.model import StopPlace
from paperroute_transit.core.patterns import PatternStop
from paperroute_transit.core.reach import Reach, ReachRow, RowKind
from paperroute_transit.core.stop_view import not_departed
from paperroute_transit.core.strip_detour_rows import (
    Shaped,
    detour_layout,
    paired_place,
)
from paperroute_transit.core.strip_model import CarryNote, TripDay, trip_day
from paperroute_transit.core.strip_times import (
    TripInputs,
    boardable,
    live_strip,
    stops_after,
)

FOLLOWED: Final[int] = 4  # next trains the rows below the From come from
MEMO_SIZE: Final[int] = 4  # ticks held
type Call = tuple[str, PatternStop]
type Served = list[tuple[str, PatternStop, datetime]]
type Timed = tuple[datetime, int]
type Row = tuple[str, PatternStop, list[Timed]]
STEADY: Final[SameMemo[tuple['Steady | None']]] = SameMemo(size=MEMO_SIZE)
REACHES: Final[SameMemo['Reach']] = SameMemo(size=MEMO_SIZE)


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
        one is passed over. Departures from a kerb across are skipped.
    """
    found: list[tuple[LiveDeparture, Served]] = []
    for item in boardable_items(trips=trips, origin=origin, target=target):
        if item.departure.facts.across:
            continue
        calls = list(served_after(item=item, trips=trips, origin=origin))
        if not calls and not found:
            return []
        if calls and calls[0][0] not in above:
            found.append((item, calls))
        if len(found) == FOLLOWED:
            break
    return found


def common_lengths(*, first: tuple[str, ...], second: tuple[str, ...]
                   ) -> list[list[int]]:
    """
    Measure the longest common run from each pair of positions on.

    Parameters
    ----------
    first : tuple[str, ...]
        Stations in order.
    second : tuple[str, ...]
        Stations in order.

    Returns
    -------
    list[list[int]]
        For `i` and `j`, the length of a longest run both share in order
        from `first[i:]` and `second[j:]`; one more row and column than
        the sequences, of zeros.
    """
    longest = [[0] * (len(second) + 1) for _ in range(len(first) + 1)]
    for i in reversed(range(len(first))):
        for j in reversed(range(len(second))):
            longest[i][j] = (
                longest[i + 1][j + 1] + 1
                if first[i] == second[j]
                else max(longest[i + 1][j], longest[i][j + 1])
            )
    return longest


@lru_cache(maxsize=256)
def common_pairs(*, first: tuple[str, ...], second: tuple[str, ...]
                 ) -> tuple[tuple[int, int], ...]:
    """
    Match up two station sequences, keeping each one's order.

    Parameters
    ----------
    first : tuple[str, ...]
        Stations in order.
    second : tuple[str, ...]
        Stations in order.

    Returns
    -------
    tuple[tuple[int, int], ...]
        Index pairs of a longest run of stations both share in order.
        Equal arguments give the cached result.
    """
    longest = common_lengths(first=first, second=second)
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < len(first) and j < len(second):
        if first[i] == second[j]:
            pairs.append((i, j))
        step = longest[i + 1][j] >= longest[i][j + 1]
        i, j = (
            (i + 1, j + 1) if first[i] == second[j]
            else (i + 1, j) if step
            else (i, j + 1)
        )
    return tuple(pairs)


def merge_into(*, rows: Sequence[Row], served: Served, train: int
               ) -> list[Row]:
    """
    Add one train's stops to rows built from other trains.

    Parameters
    ----------
    rows : Sequence[Row]
        Stations, calls and times so far, in order.
    served : Served
        The train's stops.
    train : int
        The train's index.

    Returns
    -------
    list[Row]
        The rows with the train's time on each shared station, and its
        other stops put in where they fall; between two shared stations,
        the rows' own stops come first.
    """
    pairs = common_pairs(
        first=tuple(row[0] for row in rows),
        second=tuple(station for station, _, _ in served),
    )
    merged: list[Row] = []
    at, back = 0, 0
    for i, j in [*pairs, (len(rows), len(served))]:
        merged.extend(rows[at:i])
        merged.extend(
            (station, stop, [(moment, train)])
            for station, stop, moment in served[back:j]
        )
        if i < len(rows):
            station, stop, timed = rows[i]
            merged.append((station, stop, [*timed, (served[j][2], train)]))
        at, back = i + 1, j + 1
    return merged


def on_branch(*, served: Served, first: Served) -> Served:
    """
    Cut a train's stops where it leaves the next train's way.

    Parameters
    ----------
    served : Served
        A train's stops.
    first : Served
        The next train's stops.

    Returns
    -------
    Served
        The stops up to the last one the next train also makes, or all
        of them when that is the next train's last stop.
    """
    shared = {station for station, _, _ in first}
    last = max(
        (k for k, (station, _, _) in enumerate(served) if station in shared),
        default=-1,
    )
    if last >= 0 and served[last][0] == first[-1][0]:
        return served
    return served[:last + 1]


def merged_steady(*, reach: Reach, trips: TripInputs, target: str | None
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
        The trains, rows in calling order and each row's times. Each
        train's stops are cut where it leaves the next train's way (see
        `on_branch`), then merged, the most stops first, so stops a
        train skips fall between their neighbours. None with no From or
        no train to build on.
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
    parts = sorted(
        (
            (on_branch(served=served, first=runs[0][1]), train)
            for train, (_, served) in enumerate(runs)
        ),
        key=lambda part: (-len(part[0]), part[1]),
    )
    rows: list[Row] = []
    for served, train in parts:
        rows = merge_into(rows=rows, served=served, train=train)
    return Steady(
        items=tuple(item for item, _ in runs),
        rows=[(station, stop) for station, stop, _ in rows],
        times=[timed for _, _, timed in rows],
    )


def memo_key(
    *, reach: Reach, trips: TripInputs, target: str | None,
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    """
    Make the memo key of a reach's rows.

    Parameters
    ----------
    reach : Reach
        The reach.
    trips : TripInputs
        The strip's departures and live data.
    target : str | None
        The Destination's station ID, or None.

    Returns
    -------
    tuple[tuple[object, ...], tuple[object, ...]]
        The objects compared by identity, then the values by equality.
    """
    # A city switch replaces the reach and departures, so zone,
    # station_of and line_kinds need not be in the key.
    return (
        (reach, trips.departures, trips.patterns),
        (trips.now, target, trips.key),
    )


def steady_of(*, reach: Reach, trips: TripInputs, target: str | None
              ) -> Steady | None:
    """
    Merge the next trains' stops below the From, once per tick.

    Parameters
    ----------
    reach : Reach
        The reach; its rows down to the From are used.
    trips : TripInputs
        The strip's departures and live data; positions are not read.
    target : str | None
        The Destination's station ID, or None.

    Returns
    -------
    Steady | None
        The result of `merged_steady`, remembered for the same reach,
        departures and patterns at the same frame time.
    """
    inputs, same = memo_key(reach=reach, trips=trips, target=target)
    held = STEADY.get(inputs=inputs, same=same)
    if held is not None:
        return held[0]
    steady = merged_steady(reach=reach, trips=trips, target=target)
    STEADY.put(inputs=inputs, same=same, value=(steady,))
    return steady


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


def path_rows(*, reach: Reach, origin: int, calls: Sequence[Call],
              layout: Sequence[Shaped]) -> tuple[ReachRow, ...]:
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
    layout : Sequence[Shaped]
        The calls of each row below the From and its detour part (see
        `detour_layout`).

    Returns
    -------
    tuple[ReachRow, ...]
        The rows down to the From, unchanged, then one line row per
        laid-out row, each with its exits from the strip below it; a
        spur row standing for two calls is named for both.
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
    for indexes, part in layout:
        station, stop = calls[indexes[0]]
        twin = [
            place_for(station=other, stop=call, places=places)
            for other, call in (calls[k] for k in indexes[1:])
        ]
        rows.append(ReachRow(
            place=paired_place(
                out=place_for(station=station, stop=stop, places=places),
                back=twin[0] if twin else None,
            ),
            kind=RowKind.MAIN,
            depth=0,
            above=False,
            parent=back,
            detour=(
                replace(part, back=twin[0].stop_id)
                if part is not None and twin
                else part
            ),
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
    inputs, same = memo_key(reach=reach, trips=trips, target=target)
    held = REACHES.get(inputs=inputs, same=same)
    if held is not None:
        return held
    return REACHES.put(
        inputs=inputs,
        same=same,
        value=laid_reach(reach=reach, trips=trips, target=target),
    )


def laid_reach(*, reach: Reach, trips: TripInputs, target: str | None
               ) -> Reach:
    """
    Lay a strip's rows below its From along the next trains, unremembered.

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
        The reach `followed_reach` gives.
    """
    origin = origin_of(reach=reach)
    steady = steady_of(reach=reach, trips=trips, target=target)
    if origin is None or steady is None:
        return reach
    rows = path_rows(
        reach=reach,
        origin=origin,
        calls=steady.rows,
        layout=detour_layout(calls=steady.rows),
    )
    first = steady.items[0].departure
    laid = Reach(
        rows=rows,
        stations=sum(
            row.kind in (RowKind.MAIN, RowKind.BRANCH) for row in rows
        ),
        station_of=reach.station_of,
        trip=first.trip_id,
        day=first.service_day,
        facing=reach.facing,
        hooks=reach.hooks,
    )
    inputs, same = memo_key(reach=laid, trips=trips, target=target)
    STEADY.put(inputs=inputs, same=same, value=(steady,))
    return laid


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
        The line row of each merged stop, two stops of a spur level
        sharing one, or None when the rows were laid out from other
        stops.
    """
    start = (origin_of(reach=reach) or 0) + 1
    rows = [
        index
        for index in range(start, len(reach.rows))
        if reach.rows[index].kind is RowKind.MAIN
    ]
    layout = detour_layout(calls=steady.rows)
    same = [reach.rows[index].place.stop_id for index in rows] == [
        steady.rows[indexes[0]][0] for indexes, _ in layout
    ]
    if not same:
        return None
    found = {
        call: row
        for row, (indexes, _) in zip(rows, layout)
        for call in indexes
    }
    return [found[call] for call in range(len(steady.rows))]


def picked_item(*, trips: TripInputs, steady: Steady,
                selected: TripDay | None) -> LiveDeparture:
    """
    Find the train whose times the rows below the From show.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    steady : Steady
        The merged stops.
    selected : TripDay | None
        The picked train, or None.

    Returns
    -------
    LiveDeparture
        The picked train; the next of the trains when none is picked or
        it is not loaded.
    """
    return next(
        (
            item
            for item in trips.departures
            if trip_day(departure=item.departure) == selected
        ),
        steady.items[0],
    )


def gathered(*, calls: Iterable[tuple[int, datetime]]
             ) -> dict[int, tuple[datetime, ...]]:
    """
    Collect each row's times.

    Parameters
    ----------
    calls : Iterable[tuple[int, datetime]]
        Row and time of each call, in calling order.

    Returns
    -------
    dict[int, tuple[datetime, ...]]
        Each row's times in calling order.
    """
    found: dict[int, tuple[datetime, ...]] = {}
    for index, moment in calls:
        found[index] = (*found.get(index, ()), moment)
    return found


def item_calls(*, trips: TripInputs, reach: Reach, steady: Steady,
               item: LiveDeparture) -> dict[int, tuple[datetime, ...]]:
    """
    Time a train at the rows below the From.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, laid out by `followed_reach`; its rows match `steady`.
    steady : Steady
        The merged stops.
    item : LiveDeparture
        The train.

    Returns
    -------
    dict[int, tuple[datetime, ...]]
        Arrivals by line row where the train stops, out then back on a
        spur row; one of the trains the rows come from by its own calls,
        any other by station.
    """
    rows = line_rows(reach=reach, steady=steady) or []
    train = next(
        (
            index
            for index, other in enumerate(steady.items)
            if other.departure == item.departure
        ),
        None,
    )
    if train is not None:
        return gathered(calls=(
            (index, moment)
            for index, timed in zip(rows, steady.times)
            for moment, which in timed
            if which == train
        ))
    origin = reach.rows[origin_of(reach=reach) or 0].place.stop_id
    served = dict(reversed([
        (station, moment)
        for station, _, moment in served_after(
            item=item, trips=trips, origin=origin,
        )
    ]))
    return gathered(calls=(
        (index, served[station])
        for index, (station, _) in zip(rows, steady.rows)
        if station in served
    ))


def picked_calls(
    *,
    trips: TripInputs,
    reach: Reach,
    target: str | None,
    selected: TripDay | None,
) -> tuple[LiveDeparture, dict[int, tuple[datetime, ...]]] | None:
    """
    Find the picked train and its times at the rows below the From.

    Parameters
    ----------
    trips : TripInputs
        The strip's departures and live data.
    reach : Reach
        The reach, laid out by `followed_reach`.
    target : str | None
        The Destination's station ID, or None.
    selected : TripDay | None
        The picked train, or None for the next.

    Returns
    -------
    tuple[LiveDeparture, dict[int, tuple[datetime, ...]]] | None
        The train (see `picked_item`) and its arrivals by line row; None
        when the rows no longer match the trains.
    """
    steady = steady_of(reach=reach, trips=trips, target=target)
    if steady is None or line_rows(reach=reach, steady=steady) is None:
        return None
    item = picked_item(trips=trips, steady=steady, selected=selected)
    return item, item_calls(
        trips=trips, reach=reach, steady=steady, item=item,
    )


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


def followed_note(*, trips: TripInputs, reach: Reach, target: str | None,
                  selected: TripDay | None = None
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
    selected : TripDay | None
        The picked train, or None for the next of the trains.

    Returns
    -------
    tuple[int, CarryNote] | None
        The row where the picked train runs on as another route, with
        that change; None when it doesn't, or not on these rows.
    """
    picked = picked_calls(
        trips=trips, reach=reach, target=target, selected=selected,
    )
    if picked is None:
        return None
    item, calls = picked
    then = item.departure.facts.then
    change = change_at(trips=trips, item=item)
    found = next(
        (
            index
            for index in sorted(calls)
            if reach.rows[index].place.stop_id == change
        ),
        None,
    )
    if then is None or found is None:
        return None
    return found, CarryNote(
        route=item.departure.route, then=f'{then.route} {then.headsign}',
    )
