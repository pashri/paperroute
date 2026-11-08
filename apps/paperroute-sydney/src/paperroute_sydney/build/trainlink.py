"""Drop or trim bundle NSW TrainLink trips that the trains feed repeats."""

import hashlib
import sqlite3
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Final

MATCH_SECONDS: Final[int] = 180
MIN_CALLS: Final[int] = 2
BUNDLE_TRIPS: Final[str] = """
t.source = 'bundle' AND r.type = 106 AND r.agency_id IN ({agencies})
"""
TWIN_TRIPS: Final[str] = """
t.source <> 'bundle' AND r.type = 2 AND r.short_name IN ({lines})
"""
CALLS: Final[str] = """
SELECT t.trip_id, t.service_id, st.seq, COALESCE(s.parent_station, s.stop_id),
  st.arr, st.dep, st.pickup <> 1 OR st.dropoff <> 1
FROM trips t JOIN routes r ON r.route_id = t.route_id
  JOIN stop_times st ON st.trip_id = t.trip_id
  JOIN stops s ON s.stop_id = st.stop_id
WHERE t.service = 1 AND {where}
ORDER BY t.trip_id, st.seq
"""


class Cut(IntEnum):
    """How a bundle trip overlaps a trains-feed trip; lower wins."""

    DROP = 0
    HEAD = 1
    TAIL = 2


@dataclass(frozen=True)
class Call:
    """One stop of a trip, by parent station; active when it takes people."""

    seq: int
    station: str
    arr: int
    dep: int
    active: bool


@dataclass(frozen=True)
class Trip:
    """A trip, the dates it runs and its calls in order."""

    trip_id: str
    service_id: str
    dates: frozenset[str]
    calls: tuple[Call, ...]

    @property
    def stops(self) -> tuple[Call, ...]:
        """
        Pick the calls where the train takes people.

        Returns
        -------
        tuple[Call, ...]
            Calls with a pickup or drop-off, in order.
        """
        return tuple(call for call in self.calls if call.active)


@dataclass(frozen=True)
class Action:
    """What to do with a bundle trip: drop it, or cut it at a station."""

    cut: Cut
    station: str


@dataclass(frozen=True)
class TrimReport:
    """What trimming did: trips removed and trips shortened."""

    dropped: int
    cut: int


def load_trips(
    *, conn: sqlite3.Connection, where: str, params: tuple[object, ...],
) -> list[Trip]:
    """
    Read trips with their dates and calls.

    Parameters
    ----------
    conn : sqlite3.Connection
        Loaded timetable.
    where : str
        Condition on ``t`` (trips) and ``r`` (routes).
    params : tuple[object, ...]
        Values for the condition.

    Returns
    -------
    list[Trip]
        Trips in trip ID order, with the dates it runs and is not already
        skipped on; a trip with none is left out.
    """
    dates: dict[str, set[str]] = defaultdict(set)
    for service, date in conn.execute('SELECT service_id, date FROM services'):
        dates[service].add(date)
    skipped: dict[str, set[str]] = defaultdict(set)
    for trip, date in conn.execute('SELECT trip_id, date FROM trip_skip'):
        skipped[trip].add(date)
    calls: dict[tuple[str, str], list[Call]] = defaultdict(list)
    for trip, service, seq, station, arr, dep, active in conn.execute(
        CALLS.format(where=where), params,
    ):
        calls[trip, service].append(Call(
            seq=seq, station=station, arr=arr, dep=dep, active=bool(active),
        ))
    return [
        Trip(
            trip_id=trip, service_id=service,
            dates=frozenset(dates[service] - skipped[trip]),
            calls=tuple(found),
        )
        for (trip, service), found in calls.items()
        if dates[service] - skipped[trip]
    ]


def action_for(*, bundle: Trip, twin: Trip) -> Action | None:
    """
    Say how a bundle trip overlaps a trains-feed trip.

    Parameters
    ----------
    bundle : Trip
        Bundle trip.
    twin : Trip
        Trains-feed trip.

    Returns
    -------
    Action | None
        DROP when the trips leave together from the same station and end at
        the same station; HEAD when they leave together and the twin ends
        elsewhere, cut at the twin's last station; TAIL when they arrive
        together and the twin starts elsewhere, cut at the twin's first
        station; None when they do not overlap.
    """
    mine, theirs = bundle.stops, twin.stops
    if len(mine) < MIN_CALLS or len(theirs) < MIN_CALLS:
        return None
    left = mine[0].station == theirs[0].station and (
        abs(mine[0].dep - theirs[0].dep) <= MATCH_SECONDS
    )
    right = mine[-1].station == theirs[-1].station and (
        abs(mine[-1].arr - theirs[-1].arr) <= MATCH_SECONDS
    )
    if left and mine[-1].station == theirs[-1].station:
        return Action(cut=Cut.DROP, station=theirs[0].station)
    if left and theirs[-1].station in {call.station for call in mine[1:]}:
        return Action(cut=Cut.HEAD, station=theirs[-1].station)
    if (
        right
        and mine[0].station != theirs[0].station
        and theirs[0].station in {call.station for call in mine[:-1]}
    ):
        return Action(cut=Cut.TAIL, station=theirs[0].station)
    return None


def closeness(
    *, bundle: Trip, action: Action, twin: Trip,
) -> tuple[int, int, int, str]:
    """
    Rank a twin's action on a bundle trip; the smallest is applied first.

    Parameters
    ----------
    bundle : Trip
        Bundle trip.
    action : Action
        What the twin would do.
    twin : Trip
        The twin.

    Returns
    -------
    tuple[int, int, int, str]
        The kind of action (a drop first), then how far apart the trips'
        times are at the end the twin matched (the departure, or the
        arrival for a TAIL cut), then the negated number of the twin's
        calls (the longest overlap first), then the twin's trip ID.
    """
    mine, theirs = bundle.stops, twin.stops
    gap = (
        abs(mine[-1].arr - theirs[-1].arr)
        if action.cut is Cut.TAIL
        else abs(mine[0].dep - theirs[0].dep)
    )
    return int(action.cut), gap, -len(theirs), twin.trip_id


def trimmed(
    *, bundle: Trip, twins: list[Trip],
) -> tuple[Trip | None, set[str]]:
    """
    Apply every overlap with the trains-feed trips to a bundle trip.

    A trip cut by one twin can then repeat another: a through train the
    trains feed carries as two trips is cut by the first and dropped as a
    copy of the second.

    Parameters
    ----------
    bundle : Trip
        Bundle trip.
    twins : list[Trip]
        Trains-feed trips that run on the date being decided.

    Returns
    -------
    tuple[Trip | None, set[str]]
        The trip as it ends up, None when it is dropped, and the IDs of the
        twins that changed it.
    """
    current, used = bundle, set[str]()
    while True:
        found = sorted(
            (
                (action, twin)
                for twin in twins
                if (action := action_for(bundle=current, twin=twin))
            ),
            key=lambda pair: closeness(
                bundle=current, action=pair[0], twin=pair[1],
            ),
        )
        if not found:
            return current, used
        action, twin = found[0]
        used.add(twin.trip_id)
        if action.cut is Cut.DROP:
            return None, used
        current = replace(
            current, calls=kept_calls(trip=current, action=action),
        )
        if len(current.stops) < MIN_CALLS:
            return None, used


def trimmed_dates(
    *, bundle: Trip, twins: list[Trip],
) -> dict[tuple[int, ...], tuple[set[str], set[str]]]:
    """
    Group the dates of a bundle trip by how it ends up trimmed.

    Parameters
    ----------
    bundle : Trip
        Bundle trip.
    twins : list[Trip]
        Trains-feed trips.

    Returns
    -------
    dict[tuple[int, ...], tuple[set[str], set[str]]]
        For each trimmed result, given as the ``seq`` of the calls it keeps
        (empty when dropped), the dates it applies to and the twin trip
        IDs behind it. Dates no twin touches are left out.
    """
    groups: dict[tuple[int, ...], tuple[set[str], set[str]]] = {}
    for date in sorted(bundle.dates):
        trip, used = trimmed(
            bundle=bundle,
            twins=[twin for twin in twins if date in twin.dates],
        )
        if used:
            seqs = () if trip is None else tuple(c.seq for c in trip.calls)
            dates, ids = groups.setdefault(seqs, (set(), set()))
            dates.add(date)
            ids |= used
    return groups


def kept_calls(*, trip: Trip, action: Action) -> tuple[Call, ...]:
    """
    Cut a trip's calls at the action's station.

    Parameters
    ----------
    trip : Trip
        Bundle trip.
    action : Action
        A HEAD or TAIL cut.

    Returns
    -------
    tuple[Call, ...]
        HEAD keeps the calls from the station on, TAIL up to it. The
        station keeps its own times.
    """
    calls = trip.calls
    if action.cut is Cut.HEAD:
        first = next(
            index
            for index, call in enumerate(calls)
            if call.station == action.station and index > 0
        )
        return calls[first:]
    last = max(
        index
        for index, call in enumerate(calls[:-1])
        if call.station == action.station
    )
    return calls[:last + 1]


def delete_calls(
    *, conn: sqlite3.Connection, trip: Trip, keep: tuple[Call, ...],
) -> None:
    """
    Delete the stop_times of a trip that are not in ``keep``.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip : Trip
        Trip.
    keep : tuple[Call, ...]
        Calls to keep.
    """
    kept = {call.seq for call in keep}
    conn.executemany(
        'DELETE FROM stop_times WHERE trip_id = ? AND seq = ?',
        [(trip.trip_id, call.seq) for call in trip.calls
         if call.seq not in kept],
    )


def remove_trip(*, conn: sqlite3.Connection, trip: Trip) -> None:
    """
    Delete a trip and its stop_times.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip : Trip
        Trip.
    """
    conn.execute('DELETE FROM stop_times WHERE trip_id = ?', (trip.trip_id,))
    conn.execute('DELETE FROM trips WHERE trip_id = ?', (trip.trip_id,))


def skip_dates(
    *, conn: sqlite3.Connection, trip: Trip, dates: set[str],
) -> None:
    """
    Stop a trip being listed on some dates.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip : Trip
        Trip.
    dates : set[str]
        Dates to skip.
    """
    conn.executemany(
        'INSERT OR IGNORE INTO trip_skip VALUES (?, ?)',
        [(trip.trip_id, date) for date in sorted(dates)],
    )


def clone_cut(
    *, conn: sqlite3.Connection, trip: Trip, keep: tuple[Call, ...],
    dates: set[str], name: str,
) -> None:
    """
    Add a shortened copy of a trip that runs only on some dates.

    The original is skipped on those dates and aliased to the copy, so
    realtime notes under its ID reach the copy.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip : Trip
        Original trip.
    keep : tuple[Call, ...]
        Calls of the copy.
    dates : set[str]
        Dates the copy runs on.
    name : str
        Suffix that makes the copy's trip ID. The copy's service ID is the
        original's with a stamp of the dates, so trips cut on the same dates
        share a service.
    """
    stamp = hashlib.sha1(','.join(sorted(dates)).encode()).hexdigest()[:8]
    copy, service = f'{trip.trip_id}{name}', f'{trip.service_id}~{stamp}'
    conn.execute(
        'INSERT INTO trips SELECT ?, route_id, ?, headsign, direction_id, '
        'block_id, agency_id, source, shape_id, service FROM trips '
        'WHERE trip_id = ?',
        (copy, service, trip.trip_id),
    )
    conn.executemany(
        'INSERT OR IGNORE INTO services VALUES (?, ?)',
        [(service, date) for date in sorted(dates)],
    )
    conn.executemany(
        'INSERT INTO stop_times SELECT ?, seq, stop_id, arr, dep, pickup, '
        'dropoff FROM stop_times WHERE trip_id = ? AND seq = ?',
        [(copy, trip.trip_id, call.seq) for call in keep],
    )
    conn.execute(
        'INSERT OR IGNORE INTO trip_aliases VALUES (?, ?)',
        (trip.trip_id, copy),
    )
    skip_dates(conn=conn, trip=trip, dates=dates)


def alias_twins(
    *, conn: sqlite3.Connection, trip: Trip, twin_ids: set[str],
) -> None:
    """
    Make a dropped bundle trip an alias of the trips that replace it.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip : Trip
        Dropped bundle trip.
    twin_ids : set[str]
        Trains-feed trips covering it.
    """
    conn.executemany(
        'INSERT OR IGNORE INTO trip_aliases VALUES (?, ?)',
        [(trip.trip_id, twin_id) for twin_id in sorted(twin_ids)],
    )


def adopt_agency(
    *, conn: sqlite3.Connection, trip: Trip, twin_ids: set[str],
) -> None:
    """
    Give trains-feed trips the agency of the bundle trip they replace.

    Only for a dropped bundle trip: a twin of a cut trip keeps its agency.
    As in `merge_rail`, a trip kept from the trains feed keeps the bundle
    agency, so it stays Regional and is matched in the nswtrains feed.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip : Trip
        Bundle trip.
    twin_ids : set[str]
        Trains-feed trips that repeat it.
    """
    conn.executemany(
        'UPDATE trips SET agency_id = (SELECT agency_id FROM trips '
        'WHERE trip_id = ?) WHERE trip_id = ?',
        [(trip.trip_id, twin_id) for twin_id in sorted(twin_ids)],
    )


def hand_to_twins(
    *, conn: sqlite3.Connection, bundle: Trip, dates: set[str],
    twin_ids: set[str],
) -> None:
    """
    Leave a bundle trip's dates to its twins, which take its agency.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable being built.
    bundle : Trip
        The bundle trip.
    dates : set[str]
        The dates its twins run; all of them removes the trip.
    twin_ids : set[str]
        The twins' trip IDs.
    """
    adopt_agency(conn=conn, trip=bundle, twin_ids=twin_ids)
    alias_twins(conn=conn, trip=bundle, twin_ids=twin_ids)
    if dates == bundle.dates:
        remove_trip(conn=conn, trip=bundle)
    else:
        skip_dates(conn=conn, trip=bundle, dates=dates)


def apply_group(
    *, conn: sqlite3.Connection, bundle: Trip, keep: tuple[Call, ...],
    dates: set[str], twin_ids: set[str], name: str,
) -> tuple[int, int]:
    """
    Trim a bundle trip on some of its dates.

    A trip trimmed on all its dates is shortened in place; one trimmed on
    only some dates is skipped there and given a shortened copy for them.
    A trip left with fewer than two calls is dropped on those dates.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    bundle : Trip
        Bundle trip.
    keep : tuple[Call, ...]
        Calls it keeps on those dates.
    dates : set[str]
        Dates the trim applies to.
    twin_ids : set[str]
        Trains-feed trips behind it.
    name : str
        Suffix for the IDs of a shortened copy.

    Returns
    -------
    tuple[int, int]
        1 in the first place when the trip was removed, 1 in the second
        when it was shortened.
    """
    whole = dates == bundle.dates
    if sum(call.active for call in keep) < MIN_CALLS:
        hand_to_twins(conn=conn, bundle=bundle, dates=dates, twin_ids=twin_ids)
        return int(whole), 0
    if whole:
        delete_calls(conn=conn, trip=bundle, keep=keep)
    else:
        clone_cut(
            conn=conn, trip=bundle, keep=keep, dates=dates, name=name,
        )
    return 0, 1


def trim_trainlink(
    *,
    conn: sqlite3.Connection,
    agencies: Collection[str],
    lines: Sequence[str],
) -> TrimReport:
    """
    Drop or shorten bundle NSW TrainLink trips that the trains feed repeats.

    The bundle numbers XPT and regional trips that the Sydney Trains feed
    also carries as NRC, NRW, STH and WST, with times a little different
    and, for trains that divide, a different far end. For each bundle trip
    and each trains-feed trip on a shared date:

    - the same first and last station, leaving within `MATCH_SECONDS`:
      the bundle trip is dropped;
    - the same first station and departure, ending elsewhere: the bundle
      trip starts at the trains-feed trip's last station;
    - the same last station and arrival, starting elsewhere: the bundle
      trip ends at the trains-feed trip's first station.

    The trains-feed trip is always kept. A bundle trip left with fewer than
    two calls is dropped. Changes are committed before returning.

    Parameters
    ----------
    conn : sqlite3.Connection
        Loaded timetable, before the bundle routes are renamed.
    agencies : Collection[str]
        The TrainLink agency IDs.
    lines : Sequence[str]
        The trains feed's TrainLink line names.

    Returns
    -------
    TrimReport
        Bundle trips removed outright, and trips shortened (a trip cut on
        only some dates counts once per cut).
    """
    marks = ', '.join('?' * len(agencies))
    bundles = load_trips(
        conn=conn, where=BUNDLE_TRIPS.format(agencies=marks),
        params=tuple(sorted(agencies)),
    )
    twins = load_trips(
        conn=conn,
        where=TWIN_TRIPS.format(lines=', '.join('?' * len(lines))),
        params=tuple(lines),
    )
    dropped = cut = 0
    for bundle in bundles:
        gone, shortened = trim_bundle(conn=conn, bundle=bundle, twins=twins)
        dropped += gone
        cut += shortened
    conn.commit()
    return TrimReport(dropped=dropped, cut=cut)


def trim_bundle(
    *, conn: sqlite3.Connection, bundle: Trip, twins: list[Trip],
) -> tuple[int, int]:
    """
    Trim one bundle trip against its NSW TrainLink twins.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable being built.
    bundle : Trip
        The bundle trip.
    twins : list[Trip]
        NSW TrainLink trips.

    Returns
    -------
    tuple[int, int]
        Trips dropped and trips cut short. A trip split into groups
        that together cover all its dates is removed.
    """
    dropped = cut = 0
    groups = trimmed_dates(bundle=bundle, twins=twins)
    for index, (seqs, (dates, ids)) in enumerate(groups.items()):
        gone, shortened = apply_group(
            conn=conn, bundle=bundle,
            keep=tuple(c for c in bundle.calls if c.seq in seqs),
            dates=dates, twin_ids=ids, name=f'~{index}',
        )
        dropped += gone
        cut += shortened
    if len(groups) > 1 and set().union(
        *(dates for dates, _ in groups.values()),
    ) == bundle.dates:
        remove_trip(conn=conn, trip=bundle)
        conn.execute(
            'DELETE FROM trip_skip WHERE trip_id = ?', (bundle.trip_id,),
        )
        dropped += 1
    return dropped, cut
