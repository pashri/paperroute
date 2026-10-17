"""Tests for loading GTFS sources into SQLite."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import SCHEMA_VERSION, Window, build_timetable
from tests.build.feeds import (
    DAILY,
    build_db,
    bundle_rail_files,
    bus_feed,
    shape_rows,
    shaped_bus_feed,
    sydneytrains_feed,
    sydneytrains_files,
)
from tests.helpers import write_feed


def query(*, db: Path, sql: str) -> list[tuple[object, ...]]:
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


def bundle_bus(*, tmp_path: Path) -> list[Source]:
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
                  sources=bundle_bus(tmp_path=tmp_path))
    assert query(db=db,
                 sql='SELECT route_id, type, agency_id FROM routes') == [
        ('bundle:r606', 700, '2436')]
    assert query(db=db,
                 sql='SELECT DISTINCT service_id FROM services') == [
        ('bundle:daily',)]


def test_load_stores_times_as_seconds(tmp_path: Path) -> None:
    """Stop times are stored as seconds after the service day's start."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=bundle_bus(tmp_path=tmp_path))
    rows = query(db=db, sql="SELECT dep FROM stop_times "
                     "WHERE trip_id = 'bus-2510' ORDER BY seq")
    assert rows == [(90600,), (92400,)]


def test_load_derives_platform_from_name(tmp_path: Path) -> None:
    """Platform and stand codes come from stop names when not given."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=bundle_bus(tmp_path=tmp_path))
    assert query(db=db, sql="SELECT platform FROM stops "
                     "WHERE stop_id = '2150109'") == [('B4',)]
    assert query(db=db, sql="SELECT platform FROM stops "
                     "WHERE stop_id = '2150411'") == [('1',)]


def test_load_skips_trips_outside_window(tmp_path: Path) -> None:
    """Trips whose service never runs in the window aren't loaded."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 11, 5), days=2,
                  sources=bundle_bus(tmp_path=tmp_path))
    assert query(db=db, sql='SELECT COUNT(*) FROM trips') == [(0,)]
    assert query(db=db, sql='SELECT COUNT(*) FROM stop_times') == [(0,)]


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
    assert query(db=db, sql="SELECT source FROM trips "
                     "WHERE trip_id = 'S-0835'") == [('sydneytrains',)]
    assert query(db=db, sql="SELECT dep FROM stop_times "
                     "WHERE trip_id = 'S-0835' AND seq = 1") == [(30900,)]


def test_load_links_every_trip_to_a_service(tmp_path: Path) -> None:
    """Each trip's service_id has rows in services."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=2,
                  sources=bundle_bus(tmp_path=tmp_path))
    assert query(db=db, sql='SELECT COUNT(*) FROM trips') == [(2,)]
    assert query(db=db, sql=(
        'SELECT COUNT(*) FROM trips LEFT JOIN services '
        'ON trips.service_id = services.service_id '
        'WHERE services.service_id IS NULL')) == [(0,)]


def test_load_prefers_platform_code(tmp_path: Path) -> None:
    """A stop's platform_code is used over its name."""
    feed = write_feed(path=tmp_path / 'p.zip', files={
        'stops.txt': [{'stop_id': 'a', 'stop_name': 'X, Platform 1',
                       'platform_code': '9'}],
        'routes.txt': [{'route_id': 'x', 'route_type': '2'}],
        'calendar.txt': [{'service_id': 'd', **DAILY}],
        'trips.txt': [{'route_id': 'x', 'service_id': 'd',
                       'trip_id': 't'}],
        'stop_times.txt': []})
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=[Source(name='bundle', archive=feed)])
    assert query(db=db, sql='SELECT platform FROM stops') == [('9',)]


def test_load_stores_pickup_type(tmp_path: Path) -> None:
    """The first stop's pickup_type is stored as an integer."""
    trains = sydneytrains_feed(path=tmp_path / 's.zip')
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=[Source(name='sydneytrains', archive=trains)])
    assert query(db=db, sql="SELECT pickup FROM stop_times "
                            "WHERE trip_id = 'S-0848' AND seq = 1") == [
        (1,)]


def built(*, tmp_path: Path, sources: list[Source]) -> Path:
    """
    Build a two-day timetable file, merging rail.

    Parameters
    ----------
    tmp_path : Path
        Directory for the file.
    sources : list[Source]
        Feeds in load order.

    Returns
    -------
    Path
        The timetable file.
    """
    out = tmp_path / 'timetable.db'
    build_timetable(sources=sources,
                    window=Window(start=date(2026, 10, 16), days=2), out=out)
    return out


def test_trip_rows_carry_namespaced_shape_id(tmp_path: Path) -> None:
    """A trip's shape ID is prefixed with the source name."""
    feed = shaped_bus_feed(path=tmp_path / 'b.zip',
                           points=[(151.0, -33.8), (151.01, -33.81)])
    db = built(tmp_path=tmp_path,
               sources=[Source(name='bundle', archive=feed)])
    assert query(db=db, sql='SELECT DISTINCT shape_id FROM trips') == [
        ('bundle:S1',)]


def shaped_rail(*, tmp_path: Path) -> Source:
    """
    The bundle rail feed with B-0835 on shape RB-0835 and B-0900 on RB-0900.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip.

    Returns
    -------
    Source
        The feed, named 'bundle'.
    """
    files = bundle_rail_files()
    points = [(151.0, -33.8), (151.01, -33.81)]
    return Source(name='bundle', archive=write_feed(
        path=tmp_path / 'r.zip', files={
            **files,
            'trips.txt': [{**row, 'shape_id': f'R{row["trip_id"]}'}
                          for row in files['trips.txt']],
            'shapes.txt': [*shape_rows(shape_id='RB-0835', points=points),
                           *shape_rows(shape_id='RB-0900', points=points)]}))


def test_unused_shapes_are_dropped(tmp_path: Path) -> None:
    """A shape used only by a merged-away rail trip is deleted."""
    db = built(tmp_path=tmp_path, sources=[
        Source(name='sydneytrains',
               archive=sydneytrains_feed(path=tmp_path / 's.zip')),
        shaped_rail(tmp_path=tmp_path)])
    assert query(db=db, sql='SELECT shape_id FROM shapes') == [
        ('bundle:RB-0900',)]


def test_feed_without_shapes_builds(tmp_path: Path) -> None:
    """No shapes.txt and no shape_id column leave NULL IDs, no shapes."""
    db = built(tmp_path=tmp_path, sources=bundle_bus(tmp_path=tmp_path))
    assert query(db=db, sql='SELECT DISTINCT shape_id FROM trips') == [
        (None,)]
    assert query(db=db, sql='SELECT COUNT(*) FROM shapes') == [(0,)]


def test_meta_records_schema_version(tmp_path: Path) -> None:
    """The build writes the code's schema version to meta."""
    db = built(tmp_path=tmp_path, sources=bundle_bus(tmp_path=tmp_path))
    assert query(db=db, sql="SELECT value FROM meta "
                            "WHERE key = 'schema_version'") == [
        (SCHEMA_VERSION,)]
    assert SCHEMA_VERSION == '2'


def test_partly_merged_trip_keeps_its_shape(tmp_path: Path) -> None:
    """A bundle trip skipped on only some dates keeps its shape."""
    files = sydneytrains_files()
    trains = write_feed(path=tmp_path / 's.zip', files={
        **files,
        'calendar.txt': [{'service_id': 's-daily', **DAILY,
                          'start_date': '20261016',
                          'end_date': '20261016'}]})
    db = built(tmp_path=tmp_path, sources=[
        Source(name='sydneytrains', archive=trains),
        shaped_rail(tmp_path=tmp_path)])
    assert query(db=db, sql='SELECT trip_id, date FROM trip_skip') == [
        ('B-0835', '2026-10-16')]
    assert query(db=db, sql='SELECT shape_id FROM shapes '
                            'ORDER BY shape_id') == [
        ('bundle:RB-0835',), ('bundle:RB-0900',)]


def test_shared_shape_takes_the_lowest_route_type(tmp_path: Path) -> None:
    """A shape used by a bus and a train is stored as train, either way."""
    feed = write_feed(path=tmp_path / 'm.zip', files={
        'stops.txt': [],
        'routes.txt': [{'route_id': 'bus', 'route_type': '700'},
                       {'route_id': 'train', 'route_type': '2'}],
        'calendar.txt': [{'service_id': 'd', **DAILY}],
        'trips.txt': [{'route_id': route, 'service_id': 'd',
                       'trip_id': route, 'shape_id': 'S1'}
                      for route in ('bus', 'train')],
        'stop_times.txt': [],
        'shapes.txt': shape_rows(shape_id='S1',
                                 points=[(151.0, -33.8), (151.01, -33.81)])})
    db = built(tmp_path=tmp_path,
               sources=[Source(name='bundle', archive=feed)])
    assert query(db=db, sql='SELECT shape_id, mode FROM shapes') == [
        ('bundle:S1', 'train')]
