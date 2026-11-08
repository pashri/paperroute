"""Tests for merging duplicate rail trips."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from paperroute.build.dedupe import merge_rail, relabel_trainlink
from paperroute.build.timetable import create_schema
from paperroute.city import REGIONAL_AGENCIES, TRAINLINK
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.city import MergeReport
from tests.build.feeds import (
    DAILY,
    STATIONS,
    build_db,
    bundle_rail_feed,
    bus_feed,
    regional_sources,
    stop_times,
    sydneytrains_feed,
)

START = date(2026, 10, 16)
TRAINS_ROUTE = [
    {'route_id': 'sT1', 'route_short_name': 'T1', 'route_type': '2'}
]


def merged(*, tmp_path: Path) -> tuple[Path, int]:
    """
    Build Sydney Trains then bundle rail and bus, then merge.

    Parameters
    ----------
    tmp_path : Path
        Working directory.

    Returns
    -------
    tuple[Path, int]
        Database file and trips dropped.
    """
    sources = [
        Source(
            name='sydneytrains',
            archive=sydneytrains_feed(path=tmp_path / 's.zip'),
        ),
        Source(
            name='bundle',
            archive=bundle_rail_feed(path=tmp_path / 'r.zip'),
        ),
        Source(name='bundle', archive=bus_feed(path=tmp_path / 'b.zip')),
    ]
    db = build_db(tmp_path=tmp_path, start=START, days=2, sources=sources)
    with closing(sqlite3.connect(db)) as conn:
        return db, merge_rail(conn=conn).dropped


def trip_ids(*, db: Path) -> set[str]:
    """
    All trip IDs in a database.

    Parameters
    ----------
    db : Path
        Database file.

    Returns
    -------
    set[str]
        Trip IDs.
    """
    with closing(sqlite3.connect(db)) as conn:
        return {row[0] for row in conn.execute('SELECT trip_id FROM trips')}


def one_day(*, service: str, day: str) -> dict[str, str]:
    """
    A calendar.txt row for a service running on one date.

    Parameters
    ----------
    service : str
        Service ID.
    day : str
        Date as YYYYMMDD.

    Returns
    -------
    dict[str, str]
        Calendar row.
    """
    return {
        **DAILY,
        'service_id': service,
        'start_date': day,
        'end_date': day,
    }


def rail_db(*, tmp_path: Path, trains: Path) -> Path:
    """
    Build a database from a Sydney Trains archive and the bundle rail feed.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    trains : Path
        Sydney Trains archive.

    Returns
    -------
    Path
        Database file.
    """
    sources = [
        Source(name='sydneytrains', archive=trains),
        Source(
            name='bundle',
            archive=bundle_rail_feed(path=tmp_path / 'r.zip'),
        ),
    ]
    return build_db(tmp_path=tmp_path, start=START, days=2, sources=sources)


def test_merge_drops_duplicate_and_aliases_it(tmp_path: Path) -> None:
    """The bundle's copy of a Sydney Trains run is dropped and aliased."""
    db, dropped = merged(tmp_path=tmp_path)
    assert dropped == 1
    assert 'B-0835' not in trip_ids(db=db)
    with closing(sqlite3.connect(db)) as conn:
        alias = conn.execute(
            "SELECT trip_id FROM trip_aliases WHERE alias_id = 'B-0835'"
        )
        assert alias.fetchall() == [('S-0835',)]
        times = conn.execute(
            "SELECT COUNT(*) FROM stop_times WHERE trip_id = 'B-0835'"
        )
        assert times.fetchone() == (0,)


def test_merge_commits_its_changes(tmp_path: Path) -> None:
    """A second connection sees the merge while the first is still open."""
    db = rail_db(
        tmp_path=tmp_path, trains=sydneytrains_feed(path=tmp_path / 's.zip')
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
        with closing(sqlite3.connect(db)) as other:
            alias = other.execute(
                "SELECT trip_id FROM trip_aliases WHERE alias_id = 'B-0835'"
            )
            assert alias.fetchall() == [('S-0835',)]
            gone = other.execute(
                "SELECT COUNT(*) FROM trips WHERE trip_id = 'B-0835'"
            )
            assert gone.fetchone() == (0,)


def test_merge_rolls_back_when_a_step_fails(tmp_path: Path) -> None:
    """A failure midway leaves the timetable as it was."""
    db = rail_db(
        tmp_path=tmp_path, trains=sydneytrains_feed(path=tmp_path / 's.zip')
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            'CREATE TRIGGER stop_delete BEFORE DELETE ON trips '
            "BEGIN SELECT RAISE(ABORT, 'no'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            merge_rail(conn=conn)
        times = conn.execute(
            "SELECT COUNT(*) FROM stop_times WHERE trip_id = 'B-0835'"
        )
        assert times.fetchone() != (0,)
        assert conn.execute('SELECT * FROM trip_aliases').fetchall() == []


def test_merge_keeps_unmatched_rail_and_buses(tmp_path: Path) -> None:
    """Bundle rail with no duplicate, and non-rail trips, stay."""
    db, _ = merged(tmp_path=tmp_path)
    kept = {'B-0900', 'B-NB', 'bus-0803', 'S-0835', 'S-0848'}
    assert kept <= trip_ids(db=db)


def test_merge_keeps_partial_duplicates(tmp_path: Path) -> None:
    """A bundle trip covered on only some of its dates is kept."""
    db = rail_db(
        tmp_path=tmp_path, trains=sydneytrains_feed(path=tmp_path / 's.zip')
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "DELETE FROM services WHERE service_id = 'sydneytrains:s-daily' "
            "AND date = '2026-10-17'"
        )
        report = merge_rail(conn=conn)
    assert report.dropped == 0
    assert report.partial == 1
    assert 'B-0835' in trip_ids(db=db)


def test_merge_ignores_pass_through_stops(tmp_path: Path) -> None:
    """Rows where the train passes without stopping don't change the key."""
    passing = {
        'trip_id': 'S-0835',
        'stop_sequence': '1',
        'stop_id': '2000321',
        'arrival_time': '08:20:00',
        'departure_time': '08:20:00',
        'pickup_type': '1',
        'drop_off_type': '1',
    }
    calls = [
        {**row, 'stop_sequence': str(index + 2)}
        for index, row in enumerate(
            stop_times(
                trip='S-0835',
                times=[('2150411', '08:35:00'), ('2000321', '09:02:00')],
            )
        )
    ]
    trains = write_feed(
        path=tmp_path / 's.zip',
        files={
            'stops.txt': STATIONS,
            'routes.txt': TRAINS_ROUTE,
            'calendar.txt': [{'service_id': 's-daily', **DAILY}],
            'trips.txt': [
                {
                    'route_id': 'sT1',
                    'service_id': 's-daily',
                    'trip_id': 'S-0835',
                }
            ],
            'stop_times.txt': [passing, *calls],
        },
    )
    db = rail_db(tmp_path=tmp_path, trains=trains)
    with closing(sqlite3.connect(db)) as conn:
        assert merge_rail(conn=conn).dropped == 1
    assert 'B-0835' not in trip_ids(db=db)


def test_merge_aliases_every_kept_copy(tmp_path: Path) -> None:
    """A bundle trip covered by a different feed trip each day aliases both."""
    trains = write_feed(
        path=tmp_path / 's.zip',
        files={
            'stops.txt': STATIONS,
            'routes.txt': TRAINS_ROUTE,
            'calendar.txt': [
                one_day(service='d16', day='20261016'),
                one_day(service='d17', day='20261017'),
            ],
            'trips.txt': [
                {
                    'route_id': 'sT1',
                    'service_id': f'd{n}',
                    'trip_id': f'S-{n}',
                }
                for n in (16, 17)
            ],
            'stop_times.txt': [
                row
                for n in (16, 17)
                for row in stop_times(
                    trip=f'S-{n}',
                    times=[('2150411', '08:35:00'), ('2000321', '09:02:00')],
                )
            ],
        },
    )
    db = rail_db(tmp_path=tmp_path, trains=trains)
    with closing(sqlite3.connect(db)) as conn:
        assert merge_rail(conn=conn).dropped == 1
        aliases = conn.execute(
            'SELECT trip_id FROM trip_aliases '
            "WHERE alias_id = 'B-0835' ORDER BY trip_id"
        )
        assert aliases.fetchall() == [('S-16',), ('S-17',)]


def test_merge_drops_regional_copy_under_another_line_name(
    tmp_path: Path,
) -> None:
    """An XPT a second apart under another line name is the same train."""
    db = build_db(
        tmp_path=tmp_path,
        start=START,
        days=2,
        sources=regional_sources(tmp_path=tmp_path),
    )
    with closing(sqlite3.connect(db)) as conn:
        assert merge_rail(conn=conn).dropped == 1
        aliases = conn.execute('SELECT alias_id, trip_id FROM trip_aliases')
        assert aliases.fetchall() == [('NT31-bundle', 'NT31-feed')]
        agency = conn.execute(
            "SELECT agency_id FROM trips WHERE trip_id = 'NT31-feed'"
        )
        assert agency.fetchall() == [('711',)]
    assert trip_ids(db=db) == {'NT31-feed'}


def test_merge_skips_partial_duplicate_on_covered_dates(
    tmp_path: Path,
) -> None:
    """A partly covered bundle trip is skipped only where it's covered."""
    db = build_db(
        tmp_path=tmp_path,
        start=START,
        days=2,
        sources=regional_sources(tmp_path=tmp_path),
    )
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "DELETE FROM services WHERE service_id = 'sydneytrains:s-daily' "
            "AND date = '2026-10-17'"
        )
        report = merge_rail(conn=conn)
        skips = conn.execute('SELECT trip_id, date FROM trip_skip')
        assert skips.fetchall() == [('NT31-bundle', '2026-10-16')]
        aliases = conn.execute('SELECT alias_id, trip_id FROM trip_aliases')
        assert aliases.fetchall() == [('NT31-bundle', 'NT31-feed')]
        agency = conn.execute(
            "SELECT agency_id FROM trips WHERE trip_id = 'NT31-feed'"
        )
        assert agency.fetchall() == [('711',)]
    assert report == MergeReport(dropped=0, partial=1, overnight=0)
    assert trip_ids(db=db) == {'NT31-bundle', 'NT31-feed'}


def trainlink_db(
    *, routes: dict[str, tuple[str, str, int, str, tuple[str, ...]]],
) -> sqlite3.Connection:
    """
    Make a timetable of single-trip routes.

    Parameters
    ----------
    routes : dict[str, tuple[str, str, int, str, tuple[str, ...]]]
        Route ID to short name, long name, route type, agency and the
        stations its one trip calls at.

    Returns
    -------
    sqlite3.Connection
        Open in-memory database.
    """
    conn = sqlite3.connect(':memory:')
    create_schema(conn=conn)
    for route_id, (short, long, kind, agency, calls) in routes.items():
        conn.execute(
            'INSERT INTO routes VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)',
            (route_id, short, long, kind, agency, 'x'),
        )
        conn.execute(
            "INSERT INTO trips VALUES (?, ?, 's', '', NULL, NULL, ?, 'x', "
            'NULL, 1)',
            (f't-{route_id}', route_id, agency),
        )
        for seq, station in enumerate(calls):
            conn.execute(
                "INSERT OR IGNORE INTO stops VALUES (?, ?, NULL, 0, 0, '')",
                (station, station),
            )
            conn.execute(
                'INSERT INTO stop_times VALUES (?, ?, ?, 0, 0, 0, 0)',
                (f't-{route_id}', seq, station),
            )
    return conn


LINES: dict[str, tuple[str, str, int, str, tuple[str, ...]]] = {
    'wst': ('WST', 'Sydney to Dubbo', 2, 'NSWTrains',
            ('Central', 'Lithgow', 'Orange', 'Dubbo')),
    'sth': ('STH', 'Sydney to Melbourne', 2, 'NSWTrains',
            ('Central', 'Goulburn', 'Albury')),
}


def names_after(
    *, extra: dict[str, tuple[str, str, int, str, tuple[str, ...]]],
) -> dict[str, tuple[str, str]]:
    """
    Relabel a timetable of the LINES and some bundle routes.

    Parameters
    ----------
    extra : dict[str, tuple[str, str, int, str, tuple[str, ...]]]
        Bundle routes to add.

    Returns
    -------
    dict[str, tuple[str, str]]
        Short and long name by route ID of the bundle routes.
    """
    with closing(trainlink_db(routes={**LINES, **extra})) as conn:
        relabel_trainlink(
            conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
        )
        return {
            route_id: (short, long)
            for route_id, short, long in conn.execute(
                'SELECT route_id, short_name, long_name FROM routes '
                "WHERE agency_id = '711'",
            )
        }


def test_relabel_names_a_bundle_route_after_its_line() -> None:
    """A Broken Hill-style route sharing Central to Orange is WST."""
    found = names_after(extra={
        'b446': ('446', 'Sydney to Broken Hill', 106, '711',
                 ('Central', 'Lithgow', 'Orange', 'Bathurst', 'Broken Hill')),
        'b644': ('644', 'Sydney to Griffith', 106, '711',
                 ('Central', 'Goulburn', 'Wagga', 'Griffith')),
    })
    assert found == {
        'b446': ('WST', 'Sydney to Dubbo'),
        'b644': ('STH', 'Sydney to Melbourne'),
    }


def test_relabel_keeps_a_route_with_no_overlap() -> None:
    """A route sharing under two stations with every line keeps its name."""
    found = names_after(extra={
        'b9': ('9', 'Elsewhere', 106, '711', ('Central', 'Nowhere')),
    })
    assert found == {'b9': ('9', 'Elsewhere')}


def test_relabel_breaks_a_tie_in_trainlink_order() -> None:
    """Equal overlap goes to the line first in TRAINLINK order."""
    found = names_after(extra={
        'b1': ('1', 'Tie', 106, '711', ('Central', 'Goulburn', 'Lithgow')),
    })
    assert found['b1'][0] == 'STH'


def test_relabel_without_nswtrains_routes_changes_nothing() -> None:
    """With no NSWTrains lines loaded, bundle numbers stay."""
    with closing(trainlink_db(routes={
        'b1': ('1', 'One', 106, '711', ('Central', 'Lithgow')),
    })) as conn:
        assert relabel_trainlink(
            conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
        ) == 0


@pytest.mark.parametrize('trip_id', ['S-0835', 'B-0835'])
def test_dedupe_ignores_non_service(tmp_path: Path, trip_id: str) -> None:
    """An out-of-service twin is neither dropped nor kept over its copy."""
    sources = [
        Source(
            name='sydneytrains',
            archive=sydneytrains_feed(path=tmp_path / 's.zip'),
        ),
        Source(
            name='bundle',
            archive=bundle_rail_feed(path=tmp_path / 'r.zip'),
        ),
    ]
    db = build_db(tmp_path=tmp_path, start=START, days=2, sources=sources)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            'UPDATE trips SET service = 0 WHERE trip_id = ?', (trip_id,),
        )
        conn.commit()
        merge_rail(conn=conn)
    assert {'S-0835', 'B-0835'} <= trip_ids(db=db)


def test_relabel_ignores_non_service() -> None:
    """An out-of-service line trip does not name a bundle route."""
    with closing(trainlink_db(routes={
        **LINES,
        'b446': ('446', 'Sydney to Broken Hill', 106, '711',
                 ('Central', 'Lithgow', 'Orange', 'Broken Hill')),
    })) as conn:
        conn.execute("UPDATE trips SET service = 0 WHERE trip_id = 't-wst'")
        relabel_trainlink(
            conn=conn, agencies=REGIONAL_AGENCIES, lines=TRAINLINK,
        )
        assert conn.execute(
            "SELECT short_name FROM routes WHERE route_id = 'b446'",
        ).fetchall() == [('446',)]


BUNDLE_NT34: Final[tuple[str, str, int, str, int]] = (
    'Casino', '2026-10-21', 70200, 'Central', 110460,
)
FEED_NT34: Final[tuple[str, str, int, str, int]] = (
    'Casino', '2026-10-22', 7380, 'Central', 24090,
)


def add_trip(
    *,
    conn: sqlite3.Connection,
    trip_id: str,
    source: str,
    calls: tuple[str, str, int, str, int],
) -> None:
    """
    Add a one-date rail trip with two calls.

    Parameters
    ----------
    conn : sqlite3.Connection
        Timetable.
    trip_id : str
        Trip ID, also its route and service ID.
    source : str
        Feed name.
    calls : tuple[str, str, int, str, int]
        First station, date, departure, last station and arrival.
    """
    first, day, dep, last, arr = calls
    conn.execute(
        'INSERT INTO routes VALUES (?, ?, ?, 2, NULL, NULL, ?, ?)',
        (trip_id, 'NT', 'North Coast', 'NSWTrains', source),
    )
    conn.execute(
        "INSERT INTO trips VALUES (?, ?, ?, '', NULL, NULL, NULL, ?, "
        'NULL, 1)',
        (trip_id, trip_id, trip_id, source),
    )
    conn.execute('INSERT INTO services VALUES (?, ?)', (trip_id, day))
    for seq, (station, time) in enumerate(((first, dep), (last, arr))):
        conn.execute(
            "INSERT OR IGNORE INTO stops VALUES (?, ?, NULL, 0, 0, '')",
            (station, station),
        )
        conn.execute(
            'INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, 0, 0)',
            (trip_id, seq, station, time, time),
        )


def twin_merge(
    *,
    bundle: tuple[str, str, int, str, int],
    feed: tuple[str, str, int, str, int],
    service: int = 1,
) -> tuple[MergeReport, set[str], list[tuple[str, str]]]:
    """
    Merge a timetable of one bundle trip and one Sydney Trains trip.

    Parameters
    ----------
    bundle : tuple[str, str, int, str, int]
        The bundle trip's calls, as for add_trip.
    feed : tuple[str, str, int, str, int]
        The feed trip's calls.
    service : int
        The feed trip's service flag.

    Returns
    -------
    tuple[MergeReport, set[str], list[tuple[str, str]]]
        The report, the trips left and the aliases.
    """
    with closing(sqlite3.connect(':memory:')) as conn:
        create_schema(conn=conn)
        add_trip(conn=conn, trip_id='NT34-b', source='bundle', calls=bundle)
        add_trip(
            conn=conn, trip_id='NT34-f', source='sydneytrains', calls=feed,
        )
        conn.execute('UPDATE trips SET service = ? WHERE trip_id = ?',
                     (service, 'NT34-f'))
        conn.commit()
        report = merge_rail(conn=conn)
        trips = {row[0] for row in conn.execute('SELECT trip_id FROM trips')}
        aliases = conn.execute(
            'SELECT alias_id, trip_id FROM trip_aliases',
        ).fetchall()
    return report, trips, aliases


UNCHANGED: Final[tuple[set[str], list[tuple[str, str]]]] = (
    {'NT34-b', 'NT34-f'}, [],
)


def test_overnight_twin_keeps_bundle() -> None:
    """A feed trip arriving with the bundle's next morning is dropped."""
    report, trips, aliases = twin_merge(bundle=BUNDLE_NT34, feed=FEED_NT34)
    assert report == MergeReport(dropped=0, partial=0, overnight=1)
    assert trips == {'NT34-b'}
    assert aliases == [('NT34-f', 'NT34-b')]


def test_overnight_twin_skipped_on_matched_date_only() -> None:
    """A feed trip running on an unmatched date too is only skipped."""
    with closing(sqlite3.connect(':memory:')) as conn:
        create_schema(conn=conn)
        add_trip(
            conn=conn, trip_id='NT34-b', source='bundle', calls=BUNDLE_NT34,
        )
        add_trip(
            conn=conn, trip_id='NT34-f', source='sydneytrains',
            calls=FEED_NT34,
        )
        conn.execute("INSERT INTO services VALUES ('NT34-f', '2026-10-25')")
        report = merge_rail(conn=conn)
        skips = conn.execute('SELECT trip_id, date FROM trip_skip')
        assert skips.fetchall() == [('NT34-f', '2026-10-22')]
    assert report.overnight == 1


def test_overnight_twin_needs_same_ends() -> None:
    """A different last station is a different train."""
    feed = ('Casino', '2026-10-22', 7380, 'Strathfield', 24090)
    _, trips, aliases = twin_merge(bundle=BUNDLE_NT34, feed=feed)
    assert (trips, aliases) == UNCHANGED


def test_overnight_twin_needs_close_arrival() -> None:
    """Arrivals two minutes apart don't match."""
    feed = ('Casino', '2026-10-22', 7380, 'Central', 24180)
    _, trips, aliases = twin_merge(bundle=BUNDLE_NT34, feed=feed)
    assert (trips, aliases) == UNCHANGED


def test_overnight_twin_only_next_day() -> None:
    """A feed trip two days on is not a twin."""
    feed = ('Casino', '2026-10-23', 7380, 'Central', 24090)
    _, trips, aliases = twin_merge(bundle=BUNDLE_NT34, feed=feed)
    assert (trips, aliases) == UNCHANGED


def test_overnight_twin_ignores_service_zero() -> None:
    """An out-of-service feed trip is left alone."""
    _, trips, aliases = twin_merge(
        bundle=BUNDLE_NT34, feed=FEED_NT34, service=0,
    )
    assert (trips, aliases) == UNCHANGED


def test_retimed_twin_left_alone() -> None:
    """Same ends but both times 36 minutes apart on one date: no change."""
    bundle = ('Casino', '2026-10-21', 70200, 'Central', 80000)
    feed = ('Casino', '2026-10-21', 72360, 'Central', 82160)
    report, trips, aliases = twin_merge(bundle=bundle, feed=feed)
    assert report == MergeReport(dropped=0, partial=0, overnight=0)
    assert (trips, aliases) == UNCHANGED


def test_same_date_twin_matched_on_arrival_only() -> None:
    """Same date, arrivals within a minute, departures far apart: bundle kept."""
    bundle = ('Casino', '2026-10-21', 70200, 'Central', 80000)
    feed = ('Casino', '2026-10-21', 72360, 'Central', 80030)
    report, trips, aliases = twin_merge(bundle=bundle, feed=feed)
    assert report == MergeReport(dropped=0, partial=0, overnight=1)
    assert trips == {'NT34-b'}
    assert aliases == [('NT34-f', 'NT34-b')]


def test_first_departure_rule_unchanged(tmp_path: Path) -> None:
    """A same-date first-departure duplicate still drops the bundle copy."""
    db = rail_db(
        tmp_path=tmp_path, trains=sydneytrains_feed(path=tmp_path / 's.zip')
    )
    with closing(sqlite3.connect(db)) as conn:
        report = merge_rail(conn=conn)
    assert report == MergeReport(dropped=1, partial=0, overnight=0)
    assert 'B-0835' not in trip_ids(db=db)
    assert 'S-0835' in trip_ids(db=db)
