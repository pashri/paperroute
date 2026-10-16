"""Tests for loading GTFS sources into SQLite."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from paperroute.build.timetable import Source
from tests.build.feeds import build_db, bus_feed, sydneytrains_feed
from tests.helpers import write_feed


def query(db: Path, sql: str) -> list[tuple[object, ...]]:
    """
    Run a query against a built database.

    Parameters
    ----------
    db : Path
        Database file.
    sql : str
        Query.

    Returns
    -------
    list[tuple[object, ...]]
        Rows.
    """
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql).fetchall()


def bundle_bus(tmp_path: Path) -> list[Source]:
    """
    Make the bus feed as a single source named 'bundle'.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip.

    Returns
    -------
    list[Source]
        One source.
    """
    return [Source(name='bundle',
                   archive=bus_feed(path=tmp_path / 'b.zip'))]


def test_load_namespaces_routes_and_services(tmp_path: Path) -> None:
    """Route and service IDs are prefixed with the source name."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=2,
                  sources=bundle_bus(tmp_path))
    assert query(db, 'SELECT route_id, type, agency_id FROM routes') == [
        ('bundle:r606', 700, '2436')]
    assert query(db, 'SELECT DISTINCT service_id FROM services') == [
        ('bundle:daily',)]


def test_load_stores_times_as_seconds(tmp_path: Path) -> None:
    """Stop times are stored as seconds after the service day's start."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=bundle_bus(tmp_path))
    rows = query(db, "SELECT dep FROM stop_times "
                     "WHERE trip_id = 'bus-2510' ORDER BY seq")
    assert rows == [(90600,), (92400,)]


def test_load_derives_platform_from_name(tmp_path: Path) -> None:
    """Platform and stand codes come from stop names when not given."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=bundle_bus(tmp_path))
    assert query(db, "SELECT platform FROM stops "
                     "WHERE stop_id = '2150109'") == [('B4',)]
    assert query(db, "SELECT platform FROM stops "
                     "WHERE stop_id = '2150411'") == [('1',)]


def test_load_skips_trips_outside_window(tmp_path: Path) -> None:
    """Trips whose service never runs in the window aren't loaded."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 11, 5), days=2,
                  sources=bundle_bus(tmp_path))
    assert query(db, 'SELECT COUNT(*) FROM trips') == [(0,)]
    assert query(db, 'SELECT COUNT(*) FROM stop_times') == [(0,)]


def test_first_source_wins_for_identical_trip_ids(tmp_path: Path) -> None:
    """A trip ID present in two sources keeps the first source's copy."""
    trains = sydneytrains_feed(path=tmp_path / 's.zip')
    again = write_feed(path=tmp_path / 'again.zip', files={
        'stops.txt': [{'stop_id': '2150411', 'stop_name': 'P1'}],
        'routes.txt': [{'route_id': 'x', 'route_short_name': 'T1',
                        'route_type': '2'}],
        'calendar.txt': [{'service_id': 'd', 'monday': '1', 'tuesday': '1',
                          'wednesday': '1', 'thursday': '1', 'friday': '1',
                          'saturday': '1', 'sunday': '1',
                          'start_date': '20261001',
                          'end_date': '20261031'}],
        'trips.txt': [{'route_id': 'x', 'service_id': 'd',
                       'trip_id': 'S-0835'}],
        'stop_times.txt': [{'trip_id': 'S-0835', 'stop_sequence': '1',
                            'stop_id': '2150411',
                            'arrival_time': '10:00:00',
                            'departure_time': '10:00:00'}]})
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=[Source(name='sydneytrains', archive=trains),
                           Source(name='bundle', archive=again)])
    assert query(db, "SELECT source FROM trips "
                     "WHERE trip_id = 'S-0835'") == [('sydneytrains',)]
    assert query(db, "SELECT dep FROM stop_times "
                     "WHERE trip_id = 'S-0835' AND seq = 1") == [(30900,)]
