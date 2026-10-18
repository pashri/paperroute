"""Follow trips through their blocks into runs of stations."""

import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from typing import Final

from paperroute.core.model import RouteKind, kind_for, until_repeated_hop

HIDDEN_HEADSIGNS: Final[frozenset[str]] = frozenset(
    {'Empty Train', 'Out Of Service', 'Non Revenue'},
)
RAIL_TYPES: Final[frozenset[int]] = frozenset({2, 401, 900})
TRIP_CALLS: Final[str] = """
SELECT st.trip_id, COALESCE(NULLIF(s.parent_station, ''), s.stop_id),
  st.arr, st.dep, t.route_id, r.short_name, r.type,
  COALESCE(t.agency_id, r.agency_id), t.headsign, t.block_id, t.service_id
FROM stop_times st JOIN stops s USING (stop_id) JOIN trips t USING (trip_id)
JOIN routes r USING (route_id)
ORDER BY st.trip_id, st.seq
"""
type CallRow = tuple[
    str, str, int, int, str, str, int, str | None, str, str | None, str,
]


@dataclass(frozen=True)
class TripCalls:
    """
    One trip's route, block and the stations it calls at.

    Hidden trips (an empty-running headsign, or a route with no kind) are
    kept so they still break block chains; check ``shown`` before offering
    a trip.
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

    @property
    def shown(self) -> bool:
        """
        Whether the trip carries passengers on a route with a kind.

        Returns
        -------
        bool
            False for a hidden headsign or a route with no kind.
        """
        return self.kind is not None and self.headsign not in HIDDEN_HEADSIGNS


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


def trip_from_rows(*, rows: Sequence[CallRow]) -> TripCalls:
    """
    Turn one trip's call rows into a TripCalls.

    Parameters
    ----------
    rows : Sequence[CallRow]
        Rows of TRIP_CALLS for one trip, in stop order.

    Returns
    -------
    TripCalls
        The trip.
    """
    (
        trip_id, _, _, first_dep, route_id, route, route_type, agency,
        headsign, block, service,
    ) = rows[0]
    _, _, last_arr, *_ = rows[-1]
    return TripCalls(
        trip_id=trip_id, route_id=route_id,
        kind=kind_for(
            route_type=route_type, agency_id=agency, short_name=route,
        ),
        route=route, headsign=headsign, block=block, service=service,
        first_dep=first_dep, last_arr=last_arr,
        stations=collapsed(stations=(station for _, station, *_ in rows)),
        rail=route_type in RAIL_TYPES,
    )


def trip_calls(*, conn: sqlite3.Connection) -> Iterator[TripCalls]:
    """
    Read every trip with the parent stations it calls at.

    Platforms are merged into their parent station, and a station called
    at twice in a row counts once. Hidden trips are included (their
    ``shown`` is False).

    Parameters
    ----------
    conn : sqlite3.Connection
        Built timetable.

    Yields
    ------
    TripCalls
        One per trip.
    """
    for _, rows in groupby(conn.execute(TRIP_CALLS), key=itemgetter(0)):
        yield trip_from_rows(rows=list(rows))


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


def run_of(
    *, trip: TripCalls, nexts: dict[str, TripCalls],
) -> tuple[str, ...]:
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
    tuple[str, ...]
        Stations of the run.
    """
    run = list(trip.stations)
    joined = {trip.trip_id}
    current = trip
    while runs_on(
        current=current, after=nexts.get(current.trip_id), joined=joined,
    ):
        current = nexts[current.trip_id]
        run.extend(current.stations[1:])
        if len(cut := until_repeated_hop(stations=run)) < len(run):
            return cut
        joined.add(current.trip_id)
    return until_repeated_hop(stations=run)


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
) -> dict[str, tuple[str, str]]:
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
    dict[str, tuple[str, str]]
        Trip ID to the next trip's short name and headsign.
    """
    return {
        trip.trip_id: (after.route, after.headsign)
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
