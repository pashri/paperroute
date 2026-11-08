"""Tests for trimming bundle TrainLink trips that the trains feed repeats."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing

import pytest

from paperroute.build.timetable import create_schema
from paperroute.build.trainlink import (
    MATCH_SECONDS,
    Action,
    Cut,
    TrimReport,
    apply_group,
    kept_calls,
    load_trips,
    trim_trainlink,
)
from paperroute.city import REGIONAL_AGENCIES, TRAINLINK

DAY = '20261020'
NEXT = '20261021'
HOUR = 3600
Calls = list[tuple[str, int, int]]


@pytest.fixture(name='conn')
def conn_fixture() -> Iterator[sqlite3.Connection]:
    """
    Make a timetable with a bundle route and a trains-feed route.

    Yields
    ------
    sqlite3.Connection
        In-memory database with the schema.
    """
    with closing(sqlite3.connect(':memory:')) as conn:
        create_schema(conn=conn)
        conn.executemany(
            'INSERT INTO routes VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)',
            [
                ('b', '31', 'Bundle', 106, '711', 'bundle'),
                ('n', 'WST', 'Western', 2, 'NSWTrains', 'sydneytrains'),
            ],
        )
        yield conn


def add_trip(
    *, conn: sqlite3.Connection, trip_id: str, route: str, calls: Calls,
    dates: tuple[str, ...] = (DAY,), service: int = 1,
) -> None:
    """
    Add a trip, its service and its calls.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip_id : str
        Trip ID; its service ID is ``s-`` and the trip ID.
    route : str
        Route ID.
    calls : Calls
        Station, arrival and departure in seconds, in order.
    dates : tuple[str, ...]
        Dates it runs on.
    service : int
        1 for a trip in service, 0 for one out of service.
    """
    source = 'bundle' if route == 'b' else 'sydneytrains'
    agency = '711' if route == 'b' else 'NSWTrains'
    conn.execute(
        "INSERT INTO trips VALUES (?, ?, ?, '', NULL, NULL, ?, ?, NULL, ?)",
        (trip_id, route, f's-{trip_id}', agency, source, service),
    )
    conn.executemany(
        'INSERT INTO services VALUES (?, ?)',
        [(f's-{trip_id}', date) for date in dates],
    )
    for seq, (station, arr, dep) in enumerate(calls):
        conn.execute(
            "INSERT OR IGNORE INTO stops VALUES (?, ?, NULL, 0, 0, '')",
            (station, station),
        )
        conn.execute(
            'INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, 0, 0)',
            (trip_id, seq, station, arr, dep),
        )


def stations(*, conn: sqlite3.Connection, trip_id: str) -> list[str]:
    """
    List the stations a trip calls at.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip_id : str
        Trip ID.

    Returns
    -------
    list[str]
        Stations in order.
    """
    return [
        station
        for station, in conn.execute(
            'SELECT stop_id FROM stop_times WHERE trip_id = ? ORDER BY seq',
            (trip_id,),
        )
    ]


def trip_ids(*, conn: sqlite3.Connection) -> set[str]:
    """
    List every trip ID.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.

    Returns
    -------
    set[str]
        Trip IDs.
    """
    return {trip for trip, in conn.execute('SELECT trip_id FROM trips')}


THROUGH: Calls = [
    ('C', 8 * HOUR, 8 * HOUR),
    ('M', 9 * HOUR, 9 * HOUR),
    ('W', 10 * HOUR, 10 * HOUR),
    ('A', 12 * HOUR, 12 * HOUR),
]


def test_same_ends_within_the_window_drop_the_bundle_trip(
    conn: sqlite3.Connection,
) -> None:
    """Same first and last station, leaving 61 s apart: drop the bundle."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=[
        ('C', 8 * HOUR + 61, 8 * HOUR + 61),
        ('A', 12 * HOUR + 5, 12 * HOUR + 5),
    ])
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=1, cut=0)
    assert trip_ids(conn=conn) == {'n1'}
    assert conn.execute('SELECT * FROM trip_aliases').fetchall() == [
        ('b1', 'n1'),
    ]
    assert conn.execute(
        "SELECT agency_id FROM trips WHERE trip_id = 'n1'",
    ).fetchall() == [('711',)]


def test_departures_further_apart_than_the_window_are_kept(
    conn: sqlite3.Connection,
) -> None:
    """A twin leaving more than the window later is a different trip."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=[
        ('C', 8 * HOUR + MATCH_SECONDS + 1, 8 * HOUR + MATCH_SECONDS + 1),
        ('A', 12 * HOUR, 12 * HOUR),
    ])
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=0, cut=0)
    assert trip_ids(conn=conn) == {'b1', 'n1'}


def test_twin_ending_early_starts_the_bundle_trip_there(
    conn: sqlite3.Connection,
) -> None:
    """Same first station, twin ends at W: the bundle trip starts at W."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH[:3])
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=0, cut=1)
    assert stations(conn=conn, trip_id='b1') == ['W', 'A']
    assert conn.execute(
        "SELECT arr, dep FROM stop_times WHERE trip_id = 'b1' AND seq = 2",
    ).fetchall() == [(10 * HOUR, 10 * HOUR)]
    assert stations(conn=conn, trip_id='n1') == ['C', 'M', 'W']


def test_twin_starting_late_ends_the_bundle_trip_there(
    conn: sqlite3.Connection,
) -> None:
    """Same last station, twin starts at M: the bundle trip ends at M."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=[
        ('M', 9 * HOUR + 30, 9 * HOUR + 30),
        ('W', 10 * HOUR, 10 * HOUR),
        ('A', 12 * HOUR + 60, 12 * HOUR + 60),
    ])
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=0, cut=1)
    assert stations(conn=conn, trip_id='b1') == ['C', 'M']


def test_cut_leaving_one_call_drops_the_trip(
    conn: sqlite3.Connection,
) -> None:
    """A cut that leaves under two calls drops the trip."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    trip = load_trips(
        conn=conn, where="t.trip_id = 'b1'", params=(),
    )[0]
    assert apply_group(
        conn=conn, bundle=trip, keep=trip.calls[-1:], dates={DAY},
        twin_ids={'n1'}, name='~0',
    ) == (1, 0)
    assert trip_ids(conn=conn) == set()


def test_cut_on_some_dates_makes_a_copy(conn: sqlite3.Connection) -> None:
    """Twin on one of two dates: skip the bundle trip there, add a copy."""
    add_trip(
        conn=conn, trip_id='b1', route='b', calls=THROUGH,
        dates=(DAY, NEXT),
    )
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH[:3])
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=0, cut=1)
    assert stations(conn=conn, trip_id='b1') == ['C', 'M', 'W', 'A']
    assert conn.execute('SELECT * FROM trip_skip').fetchall() == [
        ('b1', DAY),
    ]
    copy = next(trip for trip in trip_ids(conn=conn) if '~' in trip)
    assert stations(conn=conn, trip_id=copy) == ['W', 'A']
    assert conn.execute(
        'SELECT date FROM services WHERE service_id = '
        '(SELECT service_id FROM trips WHERE trip_id = ?)',
        (copy,),
    ).fetchall() == [(DAY,)]
    assert conn.execute(
        'SELECT service FROM trips WHERE trip_id = ?', (copy,),
    ).fetchall() == [(1,)]


def test_drop_on_some_dates_skips_those_dates(
    conn: sqlite3.Connection,
) -> None:
    """A twin on one of two dates only hides the bundle trip there."""
    add_trip(
        conn=conn, trip_id='b1', route='b', calls=THROUGH,
        dates=(DAY, NEXT),
    )
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH)
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=0, cut=0)
    assert conn.execute('SELECT * FROM trip_skip').fetchall() == [
        ('b1', DAY),
    ]


def test_trips_on_different_dates_are_left_alone(
    conn: sqlite3.Connection,
) -> None:
    """With no shared date nothing is dropped or cut."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(
        conn=conn, trip_id='n1', route='n', calls=THROUGH, dates=(NEXT,),
    )
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=0, cut=0)
    assert trip_ids(conn=conn) == {'b1', 'n1'}


def test_through_train_carried_as_two_trips_is_dropped(
    conn: sqlite3.Connection,
) -> None:
    """Cut by the first twin, the rest repeats the second: drop it all."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH[:3])
    add_trip(conn=conn, trip_id='n2', route='n', calls=THROUGH[2:])
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=1, cut=0)
    assert trip_ids(conn=conn) == {'n1', 'n2'}
    assert conn.execute(
        'SELECT trip_id FROM trip_aliases ORDER BY trip_id',
    ).fetchall() == [('n1',), ('n2',)]


def agency_of(*, conn: sqlite3.Connection, trip_id: str) -> object:
    """
    Read a trip's agency.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip_id : str
        Trip ID.

    Returns
    -------
    object
        The agency ID.
    """
    return conn.execute(
        'SELECT agency_id FROM trips WHERE trip_id = ?', (trip_id,),
    ).fetchone()[0]


def test_twin_of_a_head_cut_keeps_its_agency(
    conn: sqlite3.Connection,
) -> None:
    """A twin of a cut trip is not retagged and gets no alias."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH[:3])
    trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    )
    assert agency_of(conn=conn, trip_id='n1') == 'NSWTrains'
    assert conn.execute('SELECT * FROM trip_aliases').fetchall() == []


def test_twin_of_a_tail_cut_keeps_its_agency(
    conn: sqlite3.Connection,
) -> None:
    """The same holds when the twin starts late."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH[1:])
    trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    )
    assert stations(conn=conn, trip_id='b1') == ['C', 'M']
    assert agency_of(conn=conn, trip_id='n1') == 'NSWTrains'
    assert conn.execute('SELECT * FROM trip_aliases').fetchall() == []


def test_copy_of_a_trimmed_trip_is_an_alias_of_it(
    conn: sqlite3.Connection,
) -> None:
    """Realtime under the original ID reaches the shortened copy."""
    add_trip(
        conn=conn, trip_id='b1', route='b', calls=THROUGH,
        dates=(DAY, NEXT),
    )
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH[:3])
    trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    )
    copy = next(trip for trip in trip_ids(conn=conn) if '~' in trip)
    assert conn.execute('SELECT * FROM trip_aliases').fetchall() == [
        ('b1', copy),
    ]


def test_nearest_twin_decides_a_cut(conn: sqlite3.Connection) -> None:
    """Of two twins that would cut a trip, the nearer departure wins."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=[
        ('C', 8 * HOUR + 100, 8 * HOUR + 100), THROUGH[1],
    ])
    add_trip(conn=conn, trip_id='n2', route='n', calls=[
        ('C', 8 * HOUR + 10, 8 * HOUR + 10), THROUGH[1], THROUGH[2],
    ])
    trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    )
    assert stations(conn=conn, trip_id='b1') == ['W', 'A']


def test_longest_overlap_breaks_a_tie(conn: sqlite3.Connection) -> None:
    """With equal departures the twin covering more of the trip wins."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH[:2])
    add_trip(conn=conn, trip_id='n2', route='n', calls=THROUGH[:3])
    trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    )
    assert stations(conn=conn, trip_id='b1') == ['W', 'A']


def test_original_with_every_date_handled_is_deleted(
    conn: sqlite3.Connection,
) -> None:
    """A trip dropped on one date and copied on the other is removed."""
    add_trip(
        conn=conn, trip_id='b1', route='b', calls=THROUGH,
        dates=(DAY, NEXT),
    )
    add_trip(conn=conn, trip_id='n1', route='n', calls=THROUGH)
    add_trip(
        conn=conn, trip_id='n2', route='n', calls=THROUGH[:3],
        dates=(NEXT,),
    )
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=1, cut=1)
    assert 'b1' not in trip_ids(conn=conn)
    assert stations(conn=conn, trip_id='b1') == []
    assert conn.execute(
        "SELECT * FROM trip_skip WHERE trip_id = 'b1'",
    ).fetchall() == []


def test_kept_calls_cut_through_pass_through_calls(
    conn: sqlite3.Connection,
) -> None:
    """A cut keeps the calls where the train only passes."""
    conn.execute('UPDATE routes SET type = 106 WHERE route_id = ?', ('b',))
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH)
    conn.execute(
        "UPDATE stop_times SET pickup = 1, dropoff = 1 WHERE seq = 1",
    )
    trip = load_trips(conn=conn, where="t.trip_id = 'b1'", params=())[0]
    assert len(trip.stops) == 3
    assert [
        call.station
        for call in kept_calls(
            trip=trip, action=Action(cut=Cut.HEAD, station='W'),
        )
    ] == ['W', 'A']
    assert [
        call.station
        for call in kept_calls(
            trip=trip, action=Action(cut=Cut.TAIL, station='W'),
        )
    ] == ['C', 'M', 'W']


def test_date_skipped_by_the_rail_merge_is_not_revived(
    conn: sqlite3.Connection,
) -> None:
    """A copy runs only on dates the bundle trip is still listed on."""
    add_trip(
        conn=conn, trip_id='b1', route='b', calls=THROUGH,
        dates=(DAY, NEXT),
    )
    conn.execute("INSERT INTO trip_skip VALUES ('b1', ?)", (NEXT,))
    add_trip(
        conn=conn, trip_id='n1', route='n', calls=THROUGH[:3],
        dates=(DAY, NEXT),
    )
    trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    )
    assert stations(conn=conn, trip_id='b1') == ['W', 'A']
    assert conn.execute(
        'SELECT date FROM trip_skip ORDER BY date',
    ).fetchall() == [(NEXT,)]


@pytest.mark.parametrize('empty', ['b1', 'n1'])
def test_trim_ignores_non_service(
    conn: sqlite3.Connection, empty: str,
) -> None:
    """An out-of-service trip neither trims nor is trimmed by a twin."""
    add_trip(conn=conn, trip_id='b1', route='b', calls=THROUGH,
             service=int(empty != 'b1'))
    add_trip(conn=conn, trip_id='n1', route='n', calls=[
        ('C', 8 * HOUR, 8 * HOUR), ('A', 12 * HOUR, 12 * HOUR),
    ], service=int(empty != 'n1'))
    assert trim_trainlink(
        conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
    ) == TrimReport(dropped=0, cut=0)
    assert trip_ids(conn=conn) == {'b1', 'n1'}
    assert stations(conn=conn, trip_id='b1') == ['C', 'M', 'W', 'A']
