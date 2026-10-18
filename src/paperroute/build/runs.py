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
    """One trip's route, block and the stations it calls at."""

    trip_id: str
    route_id: str
    kind: RouteKind
    route: str
    headsign: str
    block: str | None
    service: str
    first_dep: int
    last_arr: int
    stations: tuple[str, ...]
    rail: bool


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


def trip_from_rows(*, rows: Sequence[CallRow]) -> TripCalls | None:
    """
    Turn one trip's call rows into a TripCalls.

    Parameters
    ----------
    rows : Sequence[CallRow]
        Rows of TRIP_CALLS for one trip, in stop order.

    Returns
    -------
    TripCalls | None
        The trip; None when its headsign is hidden or its route has no
        kind.
    """
    trip_id, _, _, first_dep, route_id, route, route_type, agency, \
        headsign, block, service = rows[0]
    kind = kind_for(route_type=route_type, agency_id=agency, short_name=route)
    if kind is None or headsign in HIDDEN_HEADSIGNS:
        return None
    return TripCalls(
        trip_id=trip_id, route_id=route_id, kind=kind, route=route,
        headsign=headsign, block=block, service=service,
        first_dep=first_dep, last_arr=rows[-1][2],
        stations=collapsed(stations=(row[1] for row in rows)),
        rail=route_type in RAIL_TYPES,
    )


def trip_calls(*, conn: sqlite3.Connection) -> Iterator[TripCalls]:
    """
    Read every shown trip with the parent stations it calls at.

    Platforms are merged into their parent station, and a station called
    at twice in a row counts once.

    Parameters
    ----------
    conn : sqlite3.Connection
        Built timetable.

    Yields
    ------
    TripCalls
        One per trip, skipping hidden headsigns and routes with no kind.
    """
    for _, rows in groupby(conn.execute(TRIP_CALLS), key=itemgetter(0)):
        trip = trip_from_rows(rows=list(rows))
        if trip is not None:
            yield trip


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
        Candidate trips; those on another block or service are ignored.

    Returns
    -------
    TripCalls | None
        The block's next trip on the same service (the earliest leaving at
        or after trip arrives) when it starts at trip's last station; None
        otherwise, so a later trip from that station is never chained past
        one that starts elsewhere.
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
    return upcoming if upcoming.stations[0] == trip.stations[-1] else None


def next_trips(
    *, trips: Sequence[TripCalls],
) -> dict[str, TripCalls]:
    """
    Find each block trip's next trip on the block.

    Parameters
    ----------
    trips : Sequence[TripCalls]
        Trips.

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


def run_of(
    *, trip: TripCalls, nexts: dict[str, TripCalls],
) -> tuple[str, ...]:
    """
    Follow one trip along its block until the run repeats a hop.

    Rail trips run on into any route; others only into the same route
    short name.

    Parameters
    ----------
    trip : TripCalls
        First trip of the run.
    nexts : dict[str, TripCalls]
        Trip ID to its next trip on the block.

    Returns
    -------
    tuple[str, ...]
        Stations of the run.
    """
    run = list(trip.stations)
    joined = {trip.trip_id}
    current = trip
    while (after := nexts.get(current.trip_id)) is not None \
            and after.trip_id not in joined \
            and (current.rail or after.route == current.route):
        run.extend(after.stations[1:])
        if len(cut := until_repeated_hop(stations=run)) < len(run):
            return cut
        joined.add(after.trip_id)
        current = after
    return tuple(run)


def chained(*, trips: Sequence[TripCalls]) -> dict[str, tuple[str, ...]]:
    """
    Find the run of stations each trip's vehicle makes from its start.

    Parameters
    ----------
    trips : Sequence[TripCalls]
        Trips.

    Returns
    -------
    dict[str, tuple[str, ...]]
        Trip ID to its run, cut before the first repeated hop.
    """
    nexts = next_trips(trips=trips)
    return {
        trip.trip_id: until_repeated_hop(
            stations=run_of(trip=trip, nexts=nexts),
        )
        for trip in trips
    }


def carry_ons(*, trips: Sequence[TripCalls]) -> dict[str, tuple[str, str]]:
    """
    Find block trips that carry on as a route of another short name.

    Parameters
    ----------
    trips : Sequence[TripCalls]
        Trips.

    Returns
    -------
    dict[str, tuple[str, str]]
        Trip ID to the next trip's short name and headsign.
    """
    nexts = next_trips(trips=trips)
    by_id = {trip.trip_id: trip for trip in trips}
    return {
        trip_id: (after.route, after.headsign)
        for trip_id, after in nexts.items()
        if after.route != by_id[trip_id].route
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
