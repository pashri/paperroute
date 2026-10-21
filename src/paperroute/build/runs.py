"""Follow trips through their blocks into runs of stations."""

import dataclasses
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import groupby
from operator import itemgetter
from typing import Final

from paperroute.build.gtfs_files import insert_many
from paperroute.core.model import (
    HIDDEN_HEADSIGNS,
    RouteKind,
    kind_for,
    until_repeated_hop,
)

RAIL_TYPES: Final[frozenset[int]] = frozenset({2, 401, 900})
CALLS: Final[str] = """
SELECT st.trip_id, COALESCE(NULLIF(s.parent_station, ''), s.stop_id),
  st.arr, st.dep, st.pickup <> 1, st.dropoff <> 1
FROM stop_times st JOIN stops s USING (stop_id)
ORDER BY st.trip_id, st.seq
"""
TRIP_ROUTES: Final[str] = """
SELECT t.trip_id, t.route_id, r.short_name, r.type,
  COALESCE(t.agency_id, r.agency_id), t.headsign, t.block_id, t.service_id,
  t.service
FROM trips t JOIN routes r USING (route_id)
ORDER BY t.trip_id
"""
RUN_SCHEMA: Final[str] = """
CREATE TABLE runs (run_id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
  route TEXT NOT NULL, stations TEXT NOT NULL, trips INTEGER NOT NULL);
CREATE TABLE run_stops (station_id TEXT NOT NULL, run_id INTEGER NOT NULL,
  pos INTEGER NOT NULL);
CREATE TABLE run_calls (run_id INTEGER NOT NULL, stops TEXT NOT NULL,
  no_board TEXT NOT NULL, no_alight TEXT NOT NULL);
CREATE TABLE trip_runs (trip_id TEXT PRIMARY KEY, run_id INTEGER NOT NULL)
  WITHOUT ROWID;
CREATE TABLE trip_next (trip_id TEXT PRIMARY KEY, route TEXT NOT NULL,
  headsign TEXT NOT NULL, next_trip TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE route_patterns (route_id TEXT NOT NULL, headsign TEXT NOT NULL,
  stations TEXT NOT NULL, trips INTEGER NOT NULL);
"""
RUN_STOPS_INDEX: Final[str] = (
    'CREATE INDEX run_stops_station ON run_stops (station_id, run_id, pos)'
)
RUN_CALLS_INDEX: Final[str] = (
    'CREATE INDEX run_calls_run ON run_calls (run_id)'
)
type CallRow = tuple[str, str, int, int, int, int]
type TripRow = tuple[
    str, str, str, int, str | None, str, str | None, str, int,
]


@dataclass(frozen=True)
class TripCalls:
    """
    One trip's route, block and the stations it calls at.

    Hidden trips (out of service, or on a route with no kind) are
    kept so they still break block chains; check ``shown`` before offering
    a trip. ``passes`` holds the positions in ``stations`` the trip runs
    through without stopping; ``no_board`` and ``no_alight`` the
    positions where no call allows pickup, or drop-off. ``passes`` are the
    positions in both.
    """

    trip_id: str
    route_id: str
    kind: RouteKind | None
    route: str
    headsign: str
    block: str | None
    service: str
    first_dep: int
    last_arr: int
    stations: tuple[str, ...]
    rail: bool
    passes: tuple[int, ...] = ()
    revenue: bool = True
    no_board: tuple[int, ...] = ()
    no_alight: tuple[int, ...] = ()

    @property
    def shown(self) -> bool:
        """
        Whether the trip carries passengers on a route with a kind.

        Returns
        -------
        bool
            False for a trip out of service, a hidden headsign or a route
            with no kind.
        """
        return (
            self.revenue and self.kind is not None
            and self.headsign not in HIDDEN_HEADSIGNS
        )


def collapsed(*, stations: Iterable[str]) -> tuple[str, ...]:
    """
    Intern station IDs and drop consecutive repeats.

    Parameters
    ----------
    stations : Iterable[str]
        Station IDs in call order.

    Returns
    -------
    tuple[str, ...]
        Stations, each one shared str object per ID.
    """
    return tuple(
        sys.intern(station)
        for station, _ in groupby(stations)
    )


def one_way_of(
    *, calls: Iterable[CallRow],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """
    Find the stations a trip can't be boarded at, or left at.

    Parameters
    ----------
    calls : Iterable[CallRow]
        Rows of CALLS for one trip, in stop order.

    Returns
    -------
    tuple[tuple[int, ...], tuple[int, ...]]
        Positions, among the stations with consecutive repeats dropped,
        where no call has a pickup, and those where no call has a
        drop-off.
    """
    no_board: list[int] = []
    no_alight: list[int] = []
    groups = groupby(calls, key=itemgetter(1))
    for pos, group in enumerate(list(rows) for _, rows in groups):
        if not any(call[4] for call in group):
            no_board.append(pos)
        if not any(call[5] for call in group):
            no_alight.append(pos)
    return tuple(no_board), tuple(no_alight)


def passes_of(
    *, no_board: Collection[int], no_alight: Collection[int],
) -> tuple[int, ...]:
    """
    Find the stations a trip runs through without stopping.

    Parameters
    ----------
    no_board : Collection[int]
        Positions with no pickup, from one_way_of.
    no_alight : Collection[int]
        Positions with no drop-off, from one_way_of.

    Returns
    -------
    tuple[int, ...]
        Positions with neither a pickup nor a drop-off, in order.
    """
    return tuple(sorted(set(no_board) & set(no_alight)))


def stopping(
    *, stations: Sequence[str], passes: Collection[int],
) -> tuple[str, ...]:
    """
    Drop the stations a run passes through without stopping.

    Parameters
    ----------
    stations : Sequence[str]
        Stations of a run.
    passes : Collection[int]
        Positions in stations that are not stopped at.

    Returns
    -------
    tuple[str, ...]
        The stopping stations, in order.
    """
    if not passes:
        return tuple(stations)
    return tuple(
        station
        for pos, station in enumerate(stations)
        if pos not in passes
    )


def trip_from_rows(
    *, trip: TripRow, calls: Sequence[CallRow],
) -> TripCalls:
    """
    Turn one trip's row and call rows into a TripCalls.

    Repeated names are interned, so holding many trips stays small.

    Parameters
    ----------
    trip : TripRow
        Row of TRIP_ROUTES.
    calls : Sequence[CallRow]
        Rows of CALLS for the trip, in stop order.

    Returns
    -------
    TripCalls
        The trip.
    """
    (
        trip_id, route_id, route, route_type, agency, headsign, block,
        service, revenue,
    ) = trip
    no_board, no_alight = one_way_of(calls=calls)
    return TripCalls(
        trip_id=trip_id, route_id=sys.intern(route_id),
        kind=kind_for(
            route_type=route_type, agency_id=agency, short_name=route,
        ),
        route=sys.intern(route), headsign=sys.intern(headsign),
        block=block, service=sys.intern(service),
        first_dep=calls[0][3], last_arr=calls[-1][2],
        stations=collapsed(stations=(call[1] for call in calls)),
        rail=route_type in RAIL_TYPES,
        passes=passes_of(no_board=no_board, no_alight=no_alight),
        revenue=bool(revenue), no_board=no_board, no_alight=no_alight,
    )


def trip_calls(*, conn: sqlite3.Connection) -> Iterator[TripCalls]:
    """
    Read every trip with the parent stations it calls at.

    Platforms are merged into their parent station, and a station called
    at twice in a row counts once. Hidden trips are included (their
    ``shown`` is False). Calls and trips are read by two queries ordered by
    trip ID and merged in one pass; Python's str order matches SQLite's
    BINARY collation, so the merge stays in step.

    Parameters
    ----------
    conn : sqlite3.Connection
        Built timetable.

    Yields
    ------
    TripCalls
        One per trip with calls, in trip ID order.
    """
    trips = conn.execute(TRIP_ROUTES)
    trip: TripRow | None = next(trips, None)
    for trip_id, calls in groupby(conn.execute(CALLS), key=itemgetter(0)):
        while trip is not None and trip[0] < trip_id:
            trip = next(trips, None)
        if trip is not None and trip[0] == trip_id:
            yield trip_from_rows(trip=trip, calls=list(calls))


def next_on_block(
    *, trip: TripCalls, later: Sequence[TripCalls],
) -> TripCalls | None:
    """
    Find the trip a vehicle runs next, when it leaves where trip ended.

    Parameters
    ----------
    trip : TripCalls
        A trip.
    later : Sequence[TripCalls]
        Candidate trips, hidden ones included; those on another block or
        service are ignored.

    Returns
    -------
    TripCalls | None
        The block's next trip on the same service (the earliest leaving at
        or after trip arrives) when it is shown and starts at trip's last
        station; None otherwise, so a trip is never chained past one that
        is hidden or starts elsewhere.
    """
    following = [
        other
        for other in later
        if other.trip_id != trip.trip_id and trip.block is not None
        and (other.block, other.service) == (trip.block, trip.service)
        and other.first_dep >= trip.last_arr
    ]
    if not following:
        return None
    upcoming = min(following, key=lambda other: other.first_dep)
    joins = upcoming.shown and upcoming.stations[0] == trip.stations[-1]
    return upcoming if joins else None


def next_trips(
    *, trips: Sequence[TripCalls],
) -> dict[str, TripCalls]:
    """
    Find each block trip's next trip on the block.

    Parameters
    ----------
    trips : Sequence[TripCalls]
        Trips, hidden ones included.

    Returns
    -------
    dict[str, TripCalls]
        Trip ID to its next_on_block, for trips that have one.
    """
    blocks: dict[tuple[str, str], list[TripCalls]] = {}
    for trip in trips:
        if trip.block is not None:
            blocks.setdefault((trip.block, trip.service), []).append(trip)
    found = (
        (trip.trip_id, next_on_block(trip=trip, later=block))
        for block in blocks.values()
        for trip in block
    )
    return {trip_id: after for trip_id, after in found if after is not None}


def runs_on(
    *, current: TripCalls, after: TripCalls | None, joined: set[str],
) -> bool:
    """
    Decide whether a run carries on from one trip into the next.

    Rail trips run on into any route; others only into the same route
    short name. A trip already in the run is never joined again.

    Parameters
    ----------
    current : TripCalls
        Last trip in the run so far.
    after : TripCalls | None
        Its next trip on the block, if any.
    joined : set[str]
        Trip IDs already in the run.

    Returns
    -------
    bool
        True when after joins the run.
    """
    if after is None or after.trip_id in joined:
        return False
    return current.rail or after.route == current.route


def joined_passes(
    *, passes: set[int], at: int, after: TripCalls,
) -> set[int]:
    """
    Add the next trip's pass-throughs to a run's.

    Parameters
    ----------
    passes : set[int]
        Positions the run so far passes through.
    at : int
        Position of the run's last station, where after starts.
    after : TripCalls
        Trip joining the run.

    Returns
    -------
    set[int]
        Positions the joined run passes through; the station where the
        trips meet is a pass only when both pass it.
    """
    kept = passes if 0 in after.passes else passes - {at}
    return kept | {at + pos for pos in after.passes if pos}


def joined_flags(
    *, no_board: set[int], no_alight: set[int], at: int, after: TripCalls,
) -> tuple[set[int], set[int]]:
    """
    Add the next trip's one-way positions to a run's.

    Boarding where the trips meet boards the later trip, so that flag
    comes from its first call; alighting there leaves the earlier trip,
    so that flag stays as the run had it.

    Parameters
    ----------
    no_board : set[int]
        Positions of the run so far with no pickup.
    no_alight : set[int]
        Positions of the run so far with no drop-off.
    at : int
        Position of the run's last station, where after starts.
    after : TripCalls
        Trip joining the run.

    Returns
    -------
    tuple[set[int], set[int]]
        The joined run's positions with no pickup, and with no drop-off.
    """
    return (
        (no_board - {at}) | {at + pos for pos in after.no_board},
        no_alight | {at + pos for pos in after.no_alight if pos},
    )


@dataclass(frozen=True)
class RunCalls:
    """
    A run's stations and the calls its trips make there.

    Attributes
    ----------
    stations : tuple[str, ...]
        Stations of the run.
    stops : tuple[str, ...]
        Those of them the run stops at.
    no_board : tuple[int, ...]
        Positions in stops with no pickup.
    no_alight : tuple[int, ...]
        Positions in stops with no drop-off.
    """

    stations: tuple[str, ...]
    stops: tuple[str, ...]
    no_board: tuple[int, ...] = ()
    no_alight: tuple[int, ...] = ()


def cut_run(
    *, run: Sequence[str], passes: Collection[int],
    no_board: Collection[int] = (), no_alight: Collection[int] = (),
) -> RunCalls:
    """
    Cut a run before its first repeated hop.

    Parameters
    ----------
    run : Sequence[str]
        Stations of the run.
    passes : Collection[int]
        Positions in run that are not stopped at.
    no_board : Collection[int]
        Positions in run with no pickup.
    no_alight : Collection[int]
        Positions in run with no drop-off.

    Returns
    -------
    RunCalls
        The cut stations, those of them the run stops at, and the
        one-way positions among the stops.
    """
    cut = until_repeated_hop(stations=run)
    kept = [pos for pos in range(len(cut)) if pos not in passes]
    return RunCalls(
        stations=cut, stops=stopping(stations=cut, passes=passes),
        no_board=tuple(i for i, pos in enumerate(kept) if pos in no_board),
        no_alight=tuple(
            i
            for i, pos in enumerate(kept)
            if pos in no_alight
        ),
    )


def run_calls_of(
    *, trip: TripCalls, nexts: dict[str, TripCalls],
) -> RunCalls:
    """
    Follow one trip along its block, cut before the first repeated hop.

    This owns the repeated-hop cut: the run stops growing as soon as a hop
    repeats, and is cut with until_repeated_hop.

    Parameters
    ----------
    trip : TripCalls
        First trip of the run.
    nexts : dict[str, TripCalls]
        Trip ID to its next trip on the block, from next_trips.

    Returns
    -------
    RunCalls
        Stations of the run, those of them it stops at and its one-way
        positions.
    """
    run = list(trip.stations)
    passes = set(trip.passes)
    flags = set(trip.no_board), set(trip.no_alight)
    joined = {trip.trip_id}
    current = trip
    while runs_on(
        current=current, after=nexts.get(current.trip_id), joined=joined,
    ):
        current = nexts[current.trip_id]
        passes = joined_passes(passes=passes, at=len(run) - 1, after=current)
        flags = joined_flags(
            no_board=flags[0], no_alight=flags[1], at=len(run) - 1,
            after=current,
        )
        run.extend(current.stations[1:])
        if len(until_repeated_hop(stations=run)) < len(run):
            break
        joined.add(current.trip_id)
    return cut_run(
        run=run, passes=passes, no_board=flags[0], no_alight=flags[1],
    )


def run_of(
    *, trip: TripCalls, nexts: dict[str, TripCalls],
) -> tuple[str, ...]:
    """
    Follow one trip along its block, cut before the first repeated hop.

    Parameters
    ----------
    trip : TripCalls
        First trip of the run.
    nexts : dict[str, TripCalls]
        Trip ID to its next trip on the block, from next_trips.

    Returns
    -------
    tuple[str, ...]
        Stations of the run, from run_calls_of.
    """
    return run_calls_of(trip=trip, nexts=nexts).stations


def chained(
    *, trips: Sequence[TripCalls], nexts: dict[str, TripCalls],
) -> dict[str, tuple[str, ...]]:
    """
    Find the run of stations each shown trip's vehicle makes from its start.

    Parameters
    ----------
    trips : Sequence[TripCalls]
        Trips; hidden ones get no run.
    nexts : dict[str, TripCalls]
        Trip ID to its next trip on the block, from next_trips.

    Returns
    -------
    dict[str, tuple[str, ...]]
        Trip ID to its run from run_of.
    """
    return {
        trip.trip_id: run_of(trip=trip, nexts=nexts)
        for trip in trips
        if trip.shown
    }


def carry_ons(
    *, trips: Sequence[TripCalls], nexts: dict[str, TripCalls],
) -> dict[str, tuple[str, str, str]]:
    """
    Find shown block trips that carry on as a route of another short name.

    Parameters
    ----------
    trips : Sequence[TripCalls]
        Trips.
    nexts : dict[str, TripCalls]
        Trip ID to its next trip on the block, from next_trips.

    Returns
    -------
    dict[str, tuple[str, str, str]]
        Trip ID to the next trip's short name, headsign and trip ID.
    """
    return {
        trip.trip_id: (after.route, after.headsign, after.trip_id)
        for trip in trips
        if trip.shown and (after := nexts.get(trip.trip_id)) is not None
        and after.route != trip.route
    }


def trip_days(*, conn: sqlite3.Connection) -> dict[str, int]:
    """
    Count the dates each service runs on in the window.

    Parameters
    ----------
    conn : sqlite3.Connection
        Built timetable.

    Returns
    -------
    dict[str, int]
        Service ID to its number of dates.
    """
    return dict(conn.execute(
        'SELECT service_id, COUNT(*) FROM services GROUP BY service_id',
    ))


def trip_skips(*, conn: sqlite3.Connection) -> Counter[str]:
    """
    Count the dates each trip is skipped on.

    Parameters
    ----------
    conn : sqlite3.Connection
        Built timetable.

    Returns
    -------
    Counter[str]
        Trip ID to its number of trip_skip rows.
    """
    return Counter(dict(conn.execute(
        'SELECT trip_id, COUNT(*) FROM trip_skip GROUP BY trip_id',
    )))


@dataclass(frozen=True)
class RunReport:
    """
    What load_runs stored.

    Attributes
    ----------
    runs : int
        Distinct runs.
    trips : int
        Trips given a run.
    """

    runs: int
    trips: int


@dataclass
class RunTable:
    """Distinct runs found so far, with their trip-days and trip patterns."""

    days: dict[str, int]
    skips: Counter[str]
    ids: dict[tuple[str, str, tuple[str, ...]], int] = field(
        default_factory=dict,
    )
    trips: Counter[int] = field(default_factory=Counter)
    patterns: Counter[tuple[str, str, tuple[str, ...]]] = field(
        default_factory=Counter,
    )
    calls: dict[
        tuple[int, tuple[str, ...], tuple[int, ...], tuple[int, ...]], None,
    ] = field(default_factory=dict)
    mapped: int = 0

    def add(
        self, *, trip: TripCalls, calls: RunCalls, pattern: tuple[str, ...],
    ) -> int | None:
        """
        Add a trip's trip-days to its run and count its pattern.

        Parameters
        ----------
        trip : TripCalls
            A trip.
        calls : RunCalls
            Its run: the stations, those the trip and the trips it runs
            on as stop at, and the one-way positions among them.
        pattern : tuple[str, ...]
            Its own stations, cut by until_repeated_hop.

        Returns
        -------
        int | None
            Run ID; None for a hidden trip or one with no dates left after
            its skips.
        """
        weight = self.days.get(trip.service, 0) - self.skips[trip.trip_id]
        if not trip.shown or weight <= 0:
            return None
        key = (str(trip.kind), trip.route, calls.stations)
        run_id = self.ids.setdefault(key, len(self.ids) + 1)
        self.trips[run_id] += weight
        self.patterns[(trip.route_id, trip.headsign, pattern)] += 1
        self.calls[(run_id, calls.stops, calls.no_board, calls.no_alight)] = (
            None
        )
        self.mapped += 1
        return run_id

    def run_rows(self) -> Iterator[tuple[object, ...]]:
        """
        Give the rows of the runs table.

        Yields
        ------
        tuple[object, ...]
            Run ID, kind, route, stations as JSON and trip-days.
        """
        for (kind, route, stations), run_id in self.ids.items():
            yield (
                run_id, kind, route, json.dumps(stations),
                self.trips[run_id],
            )

    def stop_rows(self) -> Iterator[tuple[object, ...]]:
        """
        Give the rows of the run_stops table, one per pass of a station.

        Yields
        ------
        tuple[object, ...]
            Station ID, run ID and position, in run order.
        """
        for (_, _, stations), run_id in self.ids.items():
            for pos, station in enumerate(stations):
                yield station, run_id, pos

    def call_rows(self) -> Iterator[tuple[object, ...]]:
        """
        Give the rows of the run_calls table, one per stopping pattern.

        Trips sharing a run can stop at different stations of it, or
        allow only boarding or alighting at some, so a run has a row for
        each distinct set of stations and one-way positions.

        Yields
        ------
        tuple[object, ...]
            Run ID, and stopping stations, positions with no pickup and
            positions with no drop-off as JSON.
        """
        for run_id, stops, no_board, no_alight in self.calls:
            yield (
                run_id, json.dumps(stops), json.dumps(no_board),
                json.dumps(no_alight),
            )

    def pattern_rows(self) -> Iterator[tuple[object, ...]]:
        """
        Give the rows of the route_patterns table.

        Yields
        ------
        tuple[object, ...]
            Route ID, headsign, stations as JSON and plain trip count.
        """
        for (route_id, headsign, stations), trips in self.patterns.items():
            yield route_id, headsign, json.dumps(stations), trips


def plain_trip_runs(
    *, trips: Iterable[TripCalls], table: RunTable, blocks: list[TripCalls],
) -> Iterator[tuple[str, int]]:
    """
    Give each trip outside a block its run, setting block trips aside.

    Block trips with the same stations, pass-throughs or one-way
    positions are given one shared tuple.

    Parameters
    ----------
    trips : Iterable[TripCalls]
        Trips in trip ID order.
    table : RunTable
        Runs so far.
    blocks : list[TripCalls]
        Filled with the trips that have a block.

    Yields
    ------
    tuple[str, int]
        Trip ID and run ID, for trips with a run.
    """
    shared: dict[tuple[str, ...], tuple[str, ...]] = {}
    skipped: dict[tuple[int, ...], tuple[int, ...]] = {}
    for trip in trips:
        if trip.block is not None:
            blocks.append(dataclasses.replace(
                trip, stations=shared.setdefault(trip.stations, trip.stations),
                passes=skipped.setdefault(trip.passes, trip.passes),
                no_board=skipped.setdefault(trip.no_board, trip.no_board),
                no_alight=skipped.setdefault(trip.no_alight, trip.no_alight),
            ))
            continue
        calls = cut_run(
            run=trip.stations, passes=trip.passes, no_board=trip.no_board,
            no_alight=trip.no_alight,
        )
        run_id = table.add(trip=trip, calls=calls, pattern=calls.stations)
        if run_id is not None:
            yield trip.trip_id, run_id


def block_trip_runs(
    *, trips: Iterable[TripCalls], nexts: dict[str, TripCalls],
    table: RunTable,
) -> Iterator[tuple[str, int]]:
    """
    Give each shown block trip the run it makes along its block.

    Parameters
    ----------
    trips : Iterable[TripCalls]
        Block trips in trip ID order, hidden ones included.
    nexts : dict[str, TripCalls]
        Trip ID to its next trip on the block, from next_trips.
    table : RunTable
        Runs so far.

    Yields
    ------
    tuple[str, int]
        Trip ID and run ID, for trips with a run.
    """
    for trip in trips:
        run_id = table.add(
            trip=trip, calls=run_calls_of(trip=trip, nexts=nexts),
            pattern=until_repeated_hop(stations=trip.stations),
        )
        if run_id is not None:
            yield trip.trip_id, run_id


def load_block_runs(
    *, conn: sqlite3.Connection, table: RunTable, blocks: list[TripCalls],
) -> None:
    """
    Chain block trips into runs and store trip_runs and trip_next rows.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable with the run tables.
    table : RunTable
        Runs so far.
    blocks : list[TripCalls]
        Block trips in trip ID order; emptied once chained.
    """
    nexts = next_trips(trips=blocks)
    tags = carry_ons(trips=blocks, nexts=nexts)
    insert_many(conn=conn, sql='INSERT INTO trip_runs VALUES (?, ?)',
                rows=block_trip_runs(trips=blocks, nexts=nexts, table=table))
    blocks.clear()
    insert_many(conn=conn, sql='INSERT INTO trip_next VALUES (?, ?, ?, ?)',
                rows=(
                    (trip_id, *tag)
                    for trip_id, tag in tags.items()
                ))


def load_runs(*, conn: sqlite3.Connection) -> RunReport:
    """
    Store every shown trip's run, the runs, their stops and the patterns.

    Each run is weighted by its trips' trip-days in the window: the dates
    a trip's service runs less the dates it is skipped on.

    Parameters
    ----------
    conn : sqlite3.Connection
        Built timetable with the RUN_SCHEMA tables.

    Returns
    -------
    RunReport
        Number of runs and of trips given one.
    """
    table = RunTable(days=trip_days(conn=conn), skips=trip_skips(conn=conn))
    blocks: list[TripCalls] = []
    insert_many(conn=conn, sql='INSERT INTO trip_runs VALUES (?, ?)',
                rows=plain_trip_runs(trips=trip_calls(conn=conn),
                                     table=table, blocks=blocks))
    load_block_runs(conn=conn, table=table, blocks=blocks)
    insert_many(conn=conn, sql='INSERT INTO runs VALUES (?, ?, ?, ?, ?)',
                rows=table.run_rows())
    insert_many(conn=conn, sql='INSERT INTO run_stops VALUES (?, ?, ?)',
                rows=table.stop_rows())
    conn.execute(RUN_STOPS_INDEX)
    insert_many(conn=conn, sql='INSERT INTO run_calls VALUES (?, ?, ?, ?)',
                rows=table.call_rows())
    conn.execute(RUN_CALLS_INDEX)
    insert_many(conn=conn,
                sql='INSERT INTO route_patterns VALUES (?, ?, ?, ?)',
                rows=table.pattern_rows())
    return RunReport(runs=len(table.ids), trips=table.mapped)
