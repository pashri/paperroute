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
  st.arr, st.dep, st.pickup <> 1 OR st.dropoff <> 1
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
CREATE TABLE run_calls (run_id INTEGER NOT NULL, stops TEXT NOT NULL);
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
type CallRow = tuple[str, str, int, int, int]
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
    through without stopping.
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


def passes_of(*, calls: Iterable[CallRow]) -> tuple[int, ...]:
    """
    Find the stations a trip runs through without stopping.

    Parameters
    ----------
    calls : Iterable[CallRow]
        Rows of CALLS for one trip, in stop order.

    Returns
    -------
    tuple[int, ...]
        Positions, among the stations with consecutive repeats dropped,
        where no call has a pickup or drop-off.
    """
    return tuple(
        pos
        for pos, (_, group) in enumerate(groupby(calls, key=itemgetter(1)))
        if not any(call[4] for call in group)
    )


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
    return TripCalls(
        trip_id=trip_id, route_id=sys.intern(route_id),
        kind=kind_for(
            route_type=route_type, agency_id=agency, short_name=route,
        ),
        route=sys.intern(route), headsign=sys.intern(headsign),
        block=block, service=sys.intern(service),
        first_dep=calls[0][3], last_arr=calls[-1][2],
        stations=collapsed(stations=(call[1] for call in calls)),
        rail=route_type in RAIL_TYPES, passes=passes_of(calls=calls),
        revenue=bool(revenue),
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


def cut_run(
    *, run: Sequence[str], passes: Collection[int],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Cut a run before its first repeated hop.

    Parameters
    ----------
    run : Sequence[str]
        Stations of the run.
    passes : Collection[int]
        Positions in run that are not stopped at.

    Returns
    -------
    tuple[tuple[str, ...], tuple[str, ...]]
        The cut stations, and those of them the run stops at.
    """
    cut = until_repeated_hop(stations=run)
    return cut, stopping(stations=cut, passes=passes)


def run_calls_of(
    *, trip: TripCalls, nexts: dict[str, TripCalls],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
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
    tuple[tuple[str, ...], tuple[str, ...]]
        Stations of the run, and those of them it stops at.
    """
    run = list(trip.stations)
    passes = set(trip.passes)
    joined = {trip.trip_id}
    current = trip
    while runs_on(
        current=current, after=nexts.get(current.trip_id), joined=joined,
    ):
        current = nexts[current.trip_id]
        passes = joined_passes(passes=passes, at=len(run) - 1, after=current)
        run.extend(current.stations[1:])
        if len(until_repeated_hop(stations=run)) < len(run):
            return cut_run(run=run, passes=passes)
        joined.add(current.trip_id)
    return cut_run(run=run, passes=passes)


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
    return run_calls_of(trip=trip, nexts=nexts)[0]


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
    calls: dict[tuple[int, tuple[str, ...]], None] = field(
        default_factory=dict,
    )
    mapped: int = 0

    def add(
        self, *, trip: TripCalls, run: tuple[str, ...],
        pattern: tuple[str, ...], stops: tuple[str, ...],
    ) -> int | None:
        """
        Add a trip's trip-days to its run and count its pattern.

        Parameters
        ----------
        trip : TripCalls
            A trip.
        run : tuple[str, ...]
            Stations of its run.
        pattern : tuple[str, ...]
            Its own stations, cut by until_repeated_hop.
        stops : tuple[str, ...]
            The stations of run the trip and the trips it runs on as stop
            at.

        Returns
        -------
        int | None
            Run ID; None for a hidden trip or one with no dates left after
            its skips.
        """
        weight = self.days.get(trip.service, 0) - self.skips[trip.trip_id]
        if not trip.shown or weight <= 0:
            return None
        key = (str(trip.kind), trip.route, run)
        run_id = self.ids.setdefault(key, len(self.ids) + 1)
        self.trips[run_id] += weight
        self.patterns[(trip.route_id, trip.headsign, pattern)] += 1
        self.calls[(run_id, stops)] = None
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

        Trips sharing a run can stop at different stations of it, so a run
        has a row for each distinct set of stations its trips stop at.

        Yields
        ------
        tuple[object, ...]
            Run ID and stopping stations as JSON.
        """
        for run_id, stops in self.calls:
            yield run_id, json.dumps(stops)

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

    Block trips with the same stations or pass-throughs are given one
    shared tuple.

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
            ))
            continue
        cut, stops = cut_run(run=trip.stations, passes=trip.passes)
        run_id = table.add(trip=trip, run=cut, pattern=cut, stops=stops)
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
        run, stops = run_calls_of(trip=trip, nexts=nexts)
        run_id = table.add(
            trip=trip, run=run, stops=stops,
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
    insert_many(conn=conn, sql='INSERT INTO run_calls VALUES (?, ?)',
                rows=table.call_rows())
    conn.execute(RUN_CALLS_INDEX)
    insert_many(conn=conn,
                sql='INSERT INTO route_patterns VALUES (?, ?, ?, ?)',
                rows=table.pattern_rows())
    return RunReport(runs=len(table.ids), trips=table.mapped)
