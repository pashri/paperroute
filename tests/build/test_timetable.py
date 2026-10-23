"""Tests for loading GTFS sources into SQLite."""

import logging
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import SCHEMA_VERSION, Window, build_timetable
from tests.build.feeds import (
    DAILY,
    build_db,
    bundle_rail_files,
    bus_feed,
    bus_files,
    shape_rows,
    shaped_bus_feed,
    stop_times,
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
    assert SCHEMA_VERSION == '7'


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


def odd_trips() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Trips of the undefined category Z35 that loading marks out of service.

    'hidden' runs empty, 'short' stops only once, 'yard' is on a route
    with no trip of a defined category and 'perth' stops at PER, outside
    NSW. 'xpt', an A8, also stops at PER and is in service.

    Returns
    -------
    tuple[list[dict[str, str]], list[dict[str, str]]]
        trips.txt and stop_times.txt rows.
    """
    trips = [
        {'route_id': route, 'service_id': 'daily', 'trip_id': trip,
         'trip_headsign': headsign, 'direction_id': '0', 'block_id': '',
         'vehicle_category_id': 'Z35'}
        for trip, route, headsign in (
            ('hidden', 'r606', 'Empty Train'), ('short', 'r606', 'Parramatta'),
            ('yard', 'rX', 'Parramatta'), ('perth', 'r606', 'Perth'),
        )
    ]
    trips.append({**trips[-1], 'trip_id': 'xpt', 'vehicle_category_id': 'A8'})
    times = [('2145615', '09:00:00'), ('2150109', '09:30:00')]
    calls = [
        *stop_times(trip='hidden', times=times),
        *stop_times(trip='short', times=times, pickup_first='1'),
        *stop_times(trip='yard', times=times),
        *stop_times(trip='perth', times=[*times, ('PER', '23:00:00')]),
        *stop_times(trip='xpt', times=[*times, ('PER', '23:00:00')]),
    ]
    for call in calls[2:4]:
        call['drop_off_type'] = '1'
    return trips, calls


def categorised_bus(
    *, tmp_path: Path, categories: list[str] | None,
) -> list[Source]:
    """
    Make the bus feed with vehicle categories on its trips.

    'bus-0803' is an A8, the excursion 'bus-2510' a Z35 with two stopping
    calls, and odd_trips adds three more Z35 trips.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip.
    categories : list[str] | None
        Categories vehicle_categories.txt defines; None for no file.

    Returns
    -------
    list[Source]
        One bundle source.
    """
    files = bus_files()
    trips, calls = odd_trips()
    files['stops.txt'] = [*files['stops.txt'], {
        'stop_id': 'PER', 'stop_name': 'Perth Station', 'location_type': '0',
        'parent_station': '', 'stop_lat': '-31.95', 'stop_lon': '115.86',
    }]
    files['routes.txt'] = [
        *files['routes.txt'], {**files['routes.txt'][0], 'route_id': 'rX'},
    ]
    files['trips.txt'] = [
        *(
            {**row, 'vehicle_category_id': category}
            for row, category in zip(files['trips.txt'], ('A8', 'Z35'))
        ),
        *trips,
    ]
    files['stop_times.txt'] = [*files['stop_times.txt'], *calls]
    if categories is not None:
        files['vehicle_categories.txt'] = [
            {'vehicle_category_id': category,
             'vehicle_category_name': category}
            for category in categories
        ]
    archive = write_feed(path=tmp_path / 'b.zip', files=files)
    return [Source(name='bundle', archive=archive)]


def services(*, db: Path) -> dict[object, object]:
    """
    Read each trip's service flag.

    Parameters
    ----------
    db : Path
        Built timetable.

    Returns
    -------
    dict[object, object]
        Trip ID to its service column.
    """
    return {
        trip_id: service
        for trip_id, service in query(
            db=db, sql='SELECT trip_id, service FROM trips',
        )
    }


def test_hidden_trip_kept_as_non_service(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A Z35 trip on a non-revenue route stays, with its stop times."""
    with caplog.at_level(logging.INFO, logger='paperroute.build.timetable'):
        db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                      sources=categorised_bus(tmp_path=tmp_path,
                                              categories=['A8']))
    assert services(db=db)['yard'] == 0
    assert services(db=db)['hidden'] == 0
    assert query(
        db=db, sql="SELECT COUNT(*) FROM stop_times WHERE trip_id = 'yard'",
    ) == [(2,)]
    assert any(
        'kept 4 bundle trips out of service' in m for m in caplog.messages
    )


def test_out_of_nsw_suspect_kept_as_non_service(tmp_path: Path) -> None:
    """A suspect stopping outside NSW, or stopping once, is out of service."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=categorised_bus(tmp_path=tmp_path,
                                          categories=['A8']))
    assert services(db=db)['perth'] == 0
    assert services(db=db)['short'] == 0
    assert query(
        db=db, sql="SELECT COUNT(*) FROM stop_times WHERE trip_id = 'short'",
    ) == [(2,)]


def test_hidden_headsign_is_non_service(tmp_path: Path) -> None:
    """A trip of a defined category headed 'Empty Train' is out of service."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=categorised_bus(tmp_path=tmp_path,
                                          categories=None))
    assert services(db=db)['hidden'] == 0


def test_revenue_trip_is_service(tmp_path: Path) -> None:
    """An ordinary trip and a passing suspect (an excursion) are in service."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=categorised_bus(tmp_path=tmp_path,
                                          categories=['A8']))
    assert services(db=db)['bus-0803'] == 1
    assert services(db=db)['bus-2510'] == 1
    assert services(db=db)['xpt'] == 1


def test_trip_categories_are_kept_without_a_categories_file(
    tmp_path: Path,
) -> None:
    """A feed that defines no categories keeps every trip."""
    db = build_db(tmp_path=tmp_path, start=date(2026, 10, 16), days=1,
                  sources=categorised_bus(tmp_path=tmp_path,
                                          categories=None))
    assert query(db=db, sql='SELECT trip_id FROM trips ORDER BY trip_id') == [
        ('bus-0803',), ('bus-2510',), ('hidden',), ('perth',), ('short',),
        ('xpt',), ('yard',),
    ]
