"""Small real GTFS feeds for build and repository tests."""

import shutil
import sqlite3
import zipfile
import zlib
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

from paperroute_sydney.city import SYDNEY
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.sources import SourceCut
from paperroute_transit.build.timetable import (
    Window,
    create_indexes,
    create_schema,
    load_source,
)

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
            load_source(
                conn=conn, source=source, window=window, city=SYDNEY,
            )
        create_indexes(conn=conn)
    return db


def rail_sources(*, tmp_path: Path) -> list[Source]:
    """
    The Sydney Trains feed and the bundle's rail feed.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zips.

    Returns
    -------
    list[Source]
        Sydney Trains, then the bundle's rail.
    """
    return [
        Source(
            name='sydneytrains',
            archive=sydneytrains_feed(path=tmp_path / 's.zip'),
        ),
        Source(
            name='bundle',
            archive=bundle_rail_feed(path=tmp_path / 'r.zip'),
        ),
    ]


def rail_and_bus_sources(*, tmp_path: Path) -> list[Source]:
    """
    The Sydney Trains feed and the bundle's rail and bus feeds.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zips.

    Returns
    -------
    list[Source]
        Sydney Trains, then the bundle's rail, then its buses.
    """
    return [
        *rail_sources(tmp_path=tmp_path),
        Source(name='bundle', archive=bus_feed(path=tmp_path / 'b.zip')),
    ]


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


def shape_rows(
    *,
    shape_id: str,
    points: list[tuple[float, float]],
    distances: list[str] | None = None,
) -> list[dict[str, str]]:
    """
    Build shapes.txt rows for one shape.

    Parameters
    ----------
    shape_id : str
        Shape ID.
    points : list[tuple[float, float]]
        (lon, lat) in order.
    distances : list[str] | None
        shape_dist_traveled of each point; the column is left out when None.

    Returns
    -------
    list[dict[str, str]]
        Rows, numbered from 1.
    """
    rows = [{'shape_id': shape_id, 'shape_pt_lat': str(lat),
             'shape_pt_lon': str(lon), 'shape_pt_sequence': str(index + 1)}
            for index, (lon, lat) in enumerate(points)]
    if distances is None:
        return rows
    return [{**row, 'shape_dist_traveled': distance}
            for row, distance in zip(rows, distances, strict=True)]


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


MEASURED: Final[list[tuple[float, float]]] = [
    (150.9746, -33.7924), (150.9746, -33.8176), (151.0049, -33.8176),
]


def measured_bus_feed(
    *, path: Path, stop_figures: tuple[str, str] = ('0', '5.59'),
) -> Path:
    """
    Write the bus feed on shape 'S1', with feed distances in kilometres.

    S1 runs south from Hollis St, then east to Parramatta Stand B4.

    Parameters
    ----------
    path : Path
        Zip to write.
    stop_figures : tuple[str, str]
        shape_dist_traveled of each trip's first and second call.

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
        'stop_times.txt': [
            {**row, 'shape_dist_traveled':
             stop_figures[int(row['stop_sequence']) - 1]}
            for row in files['stop_times.txt']
        ],
        'shapes.txt': shape_rows(shape_id='S1', points=MEASURED,
                                 distances=['0', '2.79', '5.59'])})


CUT_COMMENT: Final[bytes] = b'cut'


def commented(*, archive: Path, out: Path) -> int:
    """
    Cut a zip by copying it with a fixed comment, as a city's cut writes one.

    Parameters
    ----------
    archive : Path
        The downloaded zip.
    out : Path
        Where the cut goes.

    Returns
    -------
    int
        Always 0.
    """
    shutil.copyfile(archive, out)
    with zipfile.ZipFile(out, 'a') as copy:
        copy.comment = CUT_COMMENT
    return 0


def refused(*, archive: Path, out: Path) -> int:
    """
    Refuse every zip, as a cut refuses a malformed one.

    Parameters
    ----------
    archive : Path
        The downloaded zip.
    out : Path
        Where the cut would go.

    Returns
    -------
    int
        Never returns.

    Raises
    ------
    ValueError
        Always.
    """
    del archive, out
    raise ValueError('stop_times.txt is not in trip order')


def damaged(*, archive: Path, out: Path) -> int:
    """
    Fail as a cut does over a damaged deflate stream.

    Parameters
    ----------
    archive : Path
        The downloaded zip.
    out : Path
        Where the cut would go.

    Returns
    -------
    int
        Never returns.

    Raises
    ------
    zlib.error
        Always.
    """
    del archive, out
    raise zlib.error('Error -3 while decompressing data')


def raising(*, error: Exception) -> SourceCut:
    """
    Make a cut that fails with the given error.

    Parameters
    ----------
    error : Exception
        What the cut raises.

    Returns
    -------
    SourceCut
        A cut that always raises error.
    """

    def cut(*, archive: Path, out: Path) -> int:
        """
        Raise the error.

        Parameters
        ----------
        archive : Path
            The downloaded zip.
        out : Path
            Where the cut would go.

        Returns
        -------
        int
            Never returns.
        """
        del archive, out
        raise error

    return cut
