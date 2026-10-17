"""Small real GTFS feeds for build and repository tests."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import (
    Window,
    create_indexes,
    create_schema,
    load_source,
)
from tests.helpers import write_feed

DAILY: Final[dict[str, str]] = {
    'monday': '1', 'tuesday': '1', 'wednesday': '1', 'thursday': '1',
    'friday': '1', 'saturday': '1', 'sunday': '1',
    'start_date': '20261001', 'end_date': '20261031'}
STATIONS: Final[list[dict[str, str]]] = [
    {'stop_id': '215020', 'stop_name': 'Parramatta Station',
     'location_type': '1', 'parent_station': '',
     'stop_lat': '-33.8175', 'stop_lon': '151.0053'},
    {'stop_id': '2150411', 'stop_name': 'Parramatta Station, Platform 1',
     'location_type': '0', 'parent_station': '215020',
     'stop_lat': '-33.8175', 'stop_lon': '151.0053'},
    {'stop_id': '200060', 'stop_name': 'Central Station',
     'location_type': '1', 'parent_station': '',
     'stop_lat': '-33.8832', 'stop_lon': '151.2069'},
    {'stop_id': '2000321', 'stop_name': 'Central Station, Platform 1',
     'location_type': '0', 'parent_station': '200060',
     'stop_lat': '-33.8832', 'stop_lon': '151.2069'},
]


def stop_times(*, trip: str, times: list[tuple[str, str]],
               pickup_first: str = '0') -> list[dict[str, str]]:
    """
    Build stop_times rows for one trip.

    Parameters
    ----------
    trip : str
        Trip ID.
    times : list[tuple[str, str]]
        (stop ID, 'HH:MM:SS') in order.
    pickup_first : str
        pickup_type at the first stop.

    Returns
    -------
    list[dict[str, str]]
        Rows.
    """
    return [{'trip_id': trip, 'stop_sequence': str(index + 1),
             'stop_id': stop, 'arrival_time': at, 'departure_time': at,
             'pickup_type': pickup_first if index == 0 else '0',
             'drop_off_type': '0'}
            for index, (stop, at) in enumerate(times)]


def bus_files() -> dict[str, list[dict[str, str]]]:
    """
    Bundle-like feed: the 606 Hollis St to Parramatta, one late trip.

    Returns
    -------
    dict[str, list[dict[str, str]]]
        Member name to rows.
    """
    stops = [*STATIONS,
             {'stop_id': 'G2145615',
              'stop_name': 'Hollis St Opp Constitution Hill Library',
              'location_type': '1', 'parent_station': '',
              'stop_lat': '-33.7924', 'stop_lon': '150.9746'},
             {'stop_id': '2145615',
              'stop_name': 'Hollis St Opp Constitution Hill Library',
              'location_type': '0', 'parent_station': 'G2145615',
              'stop_lat': '-33.7924', 'stop_lon': '150.9746'},
             {'stop_id': '2150109',
              'stop_name': 'Parramatta Station, Stand B4',
              'location_type': '0', 'parent_station': '215020',
              'stop_lat': '-33.8176', 'stop_lon': '151.0049'}]
    return {
        'stops.txt': stops,
        'routes.txt': [{'route_id': 'r606', 'agency_id': '2436',
                        'route_short_name': '606',
                        'route_long_name': 'Constitution Hill to Parramatta',
                        'route_type': '700', 'route_color': '00B5EF',
                        'route_text_color': 'FFFFFF'}],
        'calendar.txt': [{'service_id': 'daily', **DAILY}],
        'trips.txt': [
            {'route_id': 'r606', 'service_id': 'daily',
             'trip_id': 'bus-0803', 'trip_headsign': 'Parramatta',
             'direction_id': '0', 'block_id': ''},
            {'route_id': 'r606', 'service_id': 'daily',
             'trip_id': 'bus-2510', 'trip_headsign': 'Parramatta',
             'direction_id': '0', 'block_id': ''},
        ],
        'stop_times.txt': [
            *stop_times(trip='bus-0803', times=[('2145615', '08:03:00'),
                                                ('2150109', '08:39:00')]),
            *stop_times(trip='bus-2510', times=[('2145615', '25:10:00'),
                                                ('2150109', '25:40:00')]),
        ],
    }


def bus_feed(*, path: Path) -> Path:
    """
    Write the bus feed of bus_files.

    Parameters
    ----------
    path : Path
        Zip to write.

    Returns
    -------
    Path
        The zip.
    """
    return write_feed(path=path, files=bus_files())


def bundle_rail_files() -> dict[str, list[dict[str, str]]]:
    """
    Bundle-like rail: T1 'B-0835' duplicates the Sydney Trains feed's
    'S-0835'; 'B-0900' has no duplicate; 'B-NB' runs north (Central first).

    Returns
    -------
    dict[str, list[dict[str, str]]]
        Member name to rows.
    """
    return {
        'stops.txt': STATIONS,
        'routes.txt': [{'route_id': 'bT1', 'agency_id': 'x0001',
                        'route_short_name': 'T1',
                        'route_long_name': 'North Shore and Western',
                        'route_type': '2', 'route_color': 'F99D1C',
                        'route_text_color': 'FFFFFF'}],
        'calendar.txt': [{'service_id': 'b-daily', **DAILY}],
        'trips.txt': [
            {'route_id': 'bT1', 'service_id': 'b-daily', 'trip_id': trip,
             'trip_headsign': 'Hornsby', 'direction_id': '0',
             'block_id': ''}
            for trip in ('B-0835', 'B-0900', 'B-NB')],
        'stop_times.txt': [
            *stop_times(trip='B-0835', times=[('2150411', '08:35:00'),
                                              ('2000321', '09:02:00')]),
            *stop_times(trip='B-0900', times=[('2150411', '09:00:00'),
                                              ('2000321', '09:27:00')]),
            *stop_times(trip='B-NB', times=[('2000321', '08:10:00'),
                                            ('2150411', '08:37:00')]),
        ],
    }


def bundle_rail_feed(*, path: Path) -> Path:
    """
    Write the rail feed of bundle_rail_files.

    Parameters
    ----------
    path : Path
        Zip to write.

    Returns
    -------
    Path
        The zip.
    """
    return write_feed(path=path, files=bundle_rail_files())


def sydneytrains_files() -> dict[str, list[dict[str, str]]]:
    """
    Sydney Trains feed: 'S-0835' (same run as the bundle's 'B-0835') and
    'S-0848' with no pickup at Parramatta.

    Returns
    -------
    dict[str, list[dict[str, str]]]
        Member name to rows.
    """
    return {
        'stops.txt': STATIONS,
        'routes.txt': [{'route_id': 'sT1', 'agency_id': 'SydneyTrains',
                        'route_short_name': 'T1',
                        'route_long_name': 'North Shore and Western',
                        'route_type': '2', 'route_color': 'F99D1C',
                        'route_text_color': 'FFFFFF'}],
        'calendar.txt': [{'service_id': 's-daily', **DAILY}],
        'trips.txt': [
            {'route_id': 'sT1', 'service_id': 's-daily', 'trip_id': trip,
             'trip_headsign': 'Hornsby', 'direction_id': '0',
             'block_id': ''}
            for trip in ('S-0835', 'S-0848')],
        'stop_times.txt': [
            *stop_times(trip='S-0835', times=[('2150411', '08:35:00'),
                                              ('2000321', '09:02:00')]),
            *stop_times(trip='S-0848', times=[('2150411', '08:48:00'),
                                              ('2000321', '09:13:00')],
                        pickup_first='1'),
        ],
    }


def sydneytrains_feed(*, path: Path) -> Path:
    """
    Write the Sydney Trains feed of sydneytrains_files.

    Parameters
    ----------
    path : Path
        Zip to write.

    Returns
    -------
    Path
        The zip.
    """
    return write_feed(path=path, files=sydneytrains_files())


def build_db(*, tmp_path: Path, sources: list[Source], start: date,
             days: int) -> Path:
    """
    Load sources into a new timetable database, with indexes.

    Parameters
    ----------
    tmp_path : Path
        Directory for the database.
    sources : list[Source]
        Feeds in load order.
    start : date
        Window start.
    days : int
        Window length.

    Returns
    -------
    Path
        The database file.
    """
    db = tmp_path / 'timetable.db'
    window = Window(start=start, days=days)
    with closing(sqlite3.connect(db)) as conn:
        create_schema(conn=conn)
        for source in sources:
            load_source(conn=conn, source=source, window=window)
        create_indexes(conn=conn)
    return db


def regional_sources(*, tmp_path: Path) -> list[Source]:
    """
    An XPT run twice: as NRC in the Sydney Trains feed, as line 31 in the
    bundle.

    The bundle copy (NSW TrainLink, agency 711, route type 106) leaves
    Central a second earlier, as the real feeds do.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zips.

    Returns
    -------
    list[Source]
        Sydney Trains feed, then the bundle.
    """
    feed = write_feed(path=tmp_path / 'xpt-feed.zip', files={
        'stops.txt': STATIONS,
        'routes.txt': [{'route_id': 'sNRC', 'agency_id': 'NSWTrains',
                        'route_short_name': 'NRC', 'route_type': '2'}],
        'calendar.txt': [{'service_id': 's-daily', **DAILY}],
        'trips.txt': [{'route_id': 'sNRC', 'service_id': 's-daily',
                       'trip_id': 'NT31-feed', 'trip_headsign': 'Casino'}],
        'stop_times.txt': stop_times(trip='NT31-feed', times=[
            ('2000321', '07:15:01'), ('2150411', '07:40:00')])})
    bundle = write_feed(path=tmp_path / 'xpt-bundle.zip', files={
        'stops.txt': STATIONS,
        'routes.txt': [{'route_id': 'b31', 'agency_id': '711',
                        'route_short_name': '31', 'route_type': '106'}],
        'calendar.txt': [{'service_id': 'b-daily', **DAILY}],
        'trips.txt': [{'route_id': 'b31', 'service_id': 'b-daily',
                       'trip_id': 'NT31-bundle',
                       'trip_headsign': 'Casino'}],
        'stop_times.txt': stop_times(trip='NT31-bundle', times=[
            ('2000321', '07:15:00'), ('2150411', '07:40:30')])})
    return [Source(name='sydneytrains', archive=feed),
            Source(name='bundle', archive=bundle)]


def shape_rows(*, shape_id: str,
               points: list[tuple[float, float]]) -> list[dict[str, str]]:
    """
    Build shapes.txt rows for one shape.

    Parameters
    ----------
    shape_id : str
        Shape ID.
    points : list[tuple[float, float]]
        (lon, lat) in order.

    Returns
    -------
    list[dict[str, str]]
        Rows, numbered from 1.
    """
    return [{'shape_id': shape_id, 'shape_pt_lat': str(lat),
             'shape_pt_lon': str(lon), 'shape_pt_sequence': str(index + 1)}
            for index, (lon, lat) in enumerate(points)]


def shaped_bus_feed(*, path: Path,
                    points: list[tuple[float, float]]) -> Path:
    """
    Write the bus feed with both trips on shape 'S1', plus an unused 'S9'.

    Parameters
    ----------
    path : Path
        Zip to write.
    points : list[tuple[float, float]]
        (lon, lat) of S1.

    Returns
    -------
    Path
        The zip.
    """
    files = bus_files()
    return write_feed(path=path, files={
        **files,
        'trips.txt': [{**row, 'shape_id': 'S1'}
                      for row in files['trips.txt']],
        'shapes.txt': [*shape_rows(shape_id='S1', points=points),
                       *shape_rows(shape_id='S9', points=points)]})
