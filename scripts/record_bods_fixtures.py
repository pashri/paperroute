"""Record live BODS feeds and a timetable slice as Cambridge test fixtures."""

import argparse
import asyncio
import io
import json
import math
import sys
import tempfile
import zipfile
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Final
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import aiohttp

from paperroute_cambridge.adapters.bods import (
    COACH_OPERATORS,
    BodsRequest,
    key_path_from_env,
    raw_coaches,
    raw_positions,
)
from paperroute_cambridge.adapters.bods_positions import named, parse_positions
from paperroute_cambridge.adapters.siri_vm import (
    SIRI,
    CoachSighting,
    coach_sightings,
    to_sighting,
)
from paperroute_cambridge.area import CAMBRIDGE_BBOX, CAMBRIDGE_PROJECTION
from paperroute_device.adapters.secrets_file import SecretsFile
from paperroute_transit.adapters.feed_errors import FeedError
from paperroute_transit.build.gtfs_files import active_dates, read_rows
from paperroute_transit.build.gtfs_slice import rows_where, write_tables
from paperroute_transit.core.geo import (
    DegreeBox,
    Projection,
    to_lonlat,
    to_metres,
)
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)

KEPT: Final[tuple[tuple[str, str], ...]] = (
    ('SCCM', 'PR2'),
    ('SCCM', '4'),
    ('SCCM', 'A'),
    ('WHIP', 'U1'),
    ('NATX', '777'),
    ('FLIX', 'UK083'),
)
BY_ROUTE: Final[tuple[str, str]] = ('SCCM', '4')
FAR_LIMIT: Final[float] = 30_000  # metres
ZONE: Final[ZoneInfo] = ZoneInfo('Europe/London')
POSITIONS_RAW: Final[str] = 'bods_positions_raw.pb'
POSITIONS_JOINED: Final[str] = 'bods_positions.pb'
ZIP_NAME: Final[str] = 'bods.zip'
RECORDED_NAME: Final[str] = 'recorded.json'
SIRI_NAMES: Final[dict[str, str]] = {
    operator: f'siri_{operator.lower()}.xml' for operator in COACH_OPERATORS
}
JOURNEY_TAGS: Final[tuple[str, ...]] = (
    'LineRef',
    'PublishedLineName',
    'OperatorRef',
    'VehicleRef',
)
Route = tuple[str, str]


def route_index(*, source: Path) -> dict[str, Route]:
    """
    Map each route id to its operator code and short name.

    Parameters
    ----------
    source : Path
        The timetable zip.

    Returns
    -------
    dict[str, Route]
        Route id to (operator code, route short name). The operator code
        is the agency's agency_noc, or its agency_id when it has none.
    """
    nocs = {
        row['agency_id']: row.get('agency_noc') or row['agency_id']
        for row in read_rows(archive=source, member='agency.txt')
    }
    return {
        row['route_id']: (
            nocs.get(row.get('agency_id', ''), ''),
            row['route_short_name'],
        )
        for row in read_rows(archive=source, member='routes.txt')
    }


def running_services(*, source: Path, day: date) -> set[str]:
    """
    List the services that run on a day.

    Parameters
    ----------
    source : Path
        The timetable zip.
    day : date
        The day.

    Returns
    -------
    set[str]
        Service ids, with calendar_dates additions and removals applied.
    """
    return set(active_dates(archive=source, start=day, days=1))


def far_route(*, source: Path) -> tuple[str, str]:
    """
    Find the first route none of whose stops is near the centre.

    Parameters
    ----------
    source : Path
        The timetable zip.

    Returns
    -------
    tuple[str, str]
        Its route id and short name.

    Raises
    ------
    ValueError
        When every route has a stop within the limit.
    """
    near_stops = {
        row['stop_id']
        for row in read_rows(archive=source, member='stops.txt')
        if math.hypot(
            *to_metres(
                lon=float(row['stop_lon']),
                lat=float(row['stop_lat']),
                projection=CAMBRIDGE_PROJECTION,
            ),
        )
        <= FAR_LIMIT
    }
    trip_route = {
        row['trip_id']: row['route_id']
        for row in read_rows(archive=source, member='trips.txt')
    }
    served: set[str] = set()
    near: set[str] = set()
    for row in read_rows(archive=source, member='stop_times.txt'):
        route = trip_route[row['trip_id']]
        served.add(route)
        if row['stop_id'] in near_stops:
            near.add(route)
    for row in read_rows(archive=source, member='routes.txt'):
        if row['route_id'] in served - near:
            return row['route_id'], row['route_short_name']
    raise ValueError('no route far from the centre')


def chosen_trips(
    *,
    source: Path,
    kept: Collection[str],
    by_route: Collection[str],
    day: date,
    live: Collection[str],
    each: int,
) -> set[str]:
    """
    Pick the trips of the kept routes.

    Parameters
    ----------
    source : Path
        The timetable zip.
    kept : Collection[str]
        Route ids to keep.
    by_route : Collection[str]
        Route ids whose live trips are left out.
    day : date
        The day.
    live : Collection[str]
        Trip ids of the recorded vehicles.
    each : int
        Trips to keep in each direction.

    Returns
    -------
    set[str]
        The first trips running on the day in each direction, but not
        the live ones of a by-route route, and every live trip of the
        other routes.
    """
    services = running_services(source=source, day=day)
    counts: dict[tuple[str, str], int] = {}
    chosen: set[str] = set()
    for row in read_rows(archive=source, member='trips.txt'):
        route, trip = row['route_id'], row['trip_id']
        if route not in kept or (route in by_route and trip in live):
            continue
        side = (route, row.get('direction_id', ''))
        if row['service_id'] in services and counts.get(side, 0) < each:
            counts[side] = counts.get(side, 0) + 1
            chosen.add(trip)
        elif trip in live:
            chosen.add(trip)
    return chosen


def tables_for(
    *,
    source: Path,
    trips: Collection[str],
) -> dict[str, list[dict[str, str]]]:
    """
    Pick the rows of each table linked to the given trips.

    Parameters
    ----------
    source : Path
        The timetable zip.
    trips : Collection[str]
        Trip ids to keep.

    Returns
    -------
    dict[str, list[dict[str, str]]]
        Rows by member name; stops include their parent stations.
    """
    trip_rows = rows_where(
        archive=source, member='trips.txt', key='trip_id', keep=trips,
    )
    times = rows_where(
        archive=source, member='stop_times.txt', key='trip_id', keep=trips,
    )
    stop_ids = {row['stop_id'] for row in times}
    stops = rows_where(
        archive=source, member='stops.txt', key='stop_id', keep=stop_ids,
    )
    stop_ids |= {row.get('parent_station', '') for row in stops}
    routes = rows_where(
        archive=source,
        member='routes.txt',
        key='route_id',
        keep={row['route_id'] for row in trip_rows},
    )
    services = {row['service_id'] for row in trip_rows}
    return {
        'agency.txt': rows_where(
            archive=source,
            member='agency.txt',
            key='agency_id',
            keep={row.get('agency_id', '') for row in routes},
        ),
        'routes.txt': routes,
        'trips.txt': trip_rows,
        'stop_times.txt': times,
        'stops.txt': rows_where(
            archive=source, member='stops.txt', key='stop_id', keep=stop_ids,
        ),
        'calendar.txt': rows_where(
            archive=source,
            member='calendar.txt',
            key='service_id',
            keep=services,
        ),
        'calendar_dates.txt': rows_where(
            archive=source,
            member='calendar_dates.txt',
            key='service_id',
            keep=services,
        ),
        'shapes.txt': rows_where(
            archive=source,
            member='shapes.txt',
            key='shape_id',
            keep={row.get('shape_id', '') for row in trip_rows} - {''},
        ),
        'feed_info.txt': list(
            read_rows(archive=source, member='feed_info.txt'),
        ),
    }


def cut_zip(
    *,
    source: Path,
    out: Path,
    day: date,
    live_trips: Collection[str],
    each: int = 4,
) -> str:
    """
    Write a slice of the timetable: the kept routes and one far route.

    Parameters
    ----------
    source : Path
        The full timetable zip.
    out : Path
        The slice zip to write.
    day : date
        The day whose trips are kept.
    live_trips : Collection[str]
        Trip ids of the recorded vehicles.
    each : int
        Trips to keep in each direction of each route.

    Returns
    -------
    str
        The short name of the far route added.
    """
    routes = route_index(source=source)
    far_id, far_name = far_route(source=source)
    kept = {rid for rid, route in routes.items() if route in KEPT}
    trips = chosen_trips(
        source=source,
        kept=kept | {far_id},
        by_route={rid for rid, route in routes.items() if route == BY_ROUTE},
        day=day,
        live=set(live_trips),
        each=each,
    )
    write_tables(out=out, tables=tables_for(source=source, trips=trips))
    return far_name


def outside(
    *,
    snapshot: PositionsSnapshot,
    box: DegreeBox,
    projection: Projection,
) -> int:
    """
    Count the vehicles outside a box.

    Parameters
    ----------
    snapshot : PositionsSnapshot
        The vehicles.
    box : DegreeBox
        West, south, east, north.
    projection : Projection
        The projection the snapshot's points are in.

    Returns
    -------
    int
        How many vehicles lie outside the box.
    """
    return sum(
        not inside(
            place=to_lonlat(point=vehicle.point, projection=projection),
            box=box,
        )
        for vehicle in snapshot.vehicles
    )


def inside(*, place: tuple[float, float], box: DegreeBox) -> bool:
    """
    Say whether a longitude and latitude lie in a box.

    Parameters
    ----------
    place : tuple[float, float]
        Longitude and latitude.
    box : DegreeBox
        West, south, east, north.

    Returns
    -------
    bool
        True when the place is in the box, edges included.
    """
    west, south, east, north = box
    lon, lat = place
    return west <= lon <= east and south <= lat <= north


def leaks(*, files: Mapping[str, bytes], key: str) -> list[str]:
    """
    Name the files that hold the key.

    Parameters
    ----------
    files : Mapping[str, bytes]
        File contents by name.
    key : str
        The API key.

    Returns
    -------
    list[str]
        The names of the files that hold the key in their own bytes or,
        for a zip, in any member's.
    """
    needle = key.encode()
    found = []
    for name, data in files.items():
        held = [data]
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                held += [archive.read(member) for member in archive.namelist()]
        if any(needle in body for body in held):
            found.append(name)
    return found


def record(*, out: Path, files: Mapping[str, bytes], key: str) -> list[str]:
    """
    Write every file unless one holds the key.

    Parameters
    ----------
    out : Path
        The directory to write to.
    files : Mapping[str, bytes]
        File contents by name.
    key : str
        The API key.

    Returns
    -------
    list[str]
        The names of the files holding the key; nothing is written then.
    """
    holding = leaks(files=files, key=key)
    if holding:
        return holding
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
    return []


async def fetch_all(*, key: str) -> dict[str, bytes]:
    """
    Download the positions and each coach operator's activities.

    Parameters
    ----------
    key : str
        The API key.

    Returns
    -------
    dict[str, bytes]
        The bodies by fixture name.
    """
    async with aiohttp.ClientSession() as session:
        request = BodsRequest(session=session, key=key, box=CAMBRIDGE_BBOX)
        bodies = {POSITIONS_RAW: await raw_positions(request=request)}
        for operator, name in SIRI_NAMES.items():
            bodies[name] = await raw_coaches(
                request=request, operator=operator,
            )
    return bodies


def fetched(*, key: str) -> dict[str, bytes]:
    """
    Download the three live bodies.

    Parameters
    ----------
    key : str
        The API key.

    Returns
    -------
    dict[str, bytes]
        The bodies by fixture name.

    Raises
    ------
    FeedError
        When a request fails; its text names the feed and the status.
    """
    return asyncio.run(fetch_all(key=key))


def siri_root(*, body: bytes) -> ElementTree.Element:
    """
    Parse a SIRI-VM body.

    Parameters
    ----------
    body : bytes
        The XML document.

    Returns
    -------
    ElementTree.Element
        The root element.

    Raises
    ------
    FeedError
        When the body is not XML.
    """
    try:
        return ElementTree.fromstring(body)
    except (ElementTree.ParseError, LookupError) as error:
        raise FeedError('SIRI-VM body is not XML') from error


def activity_places(*, body: bytes) -> list[tuple[float, float]]:
    """
    List the VehicleLocation of each activity that has one.

    Parameters
    ----------
    body : bytes
        The SIRI-VM document.

    Returns
    -------
    list[tuple[float, float]]
        Longitude and latitude of each located activity.
    """
    places = []
    for activity in siri_root(body=body).iter(f'{SIRI}VehicleActivity'):
        place = activity.find(f'.//{SIRI}VehicleLocation')
        if place is None:
            continue
        try:
            places.append(
                (
                    float(place.findtext(f'{SIRI}Longitude') or ''),
                    float(place.findtext(f'{SIRI}Latitude') or ''),
                ),
            )
        except ValueError:
            continue
    return places


def activity_count(*, body: bytes) -> int:
    """
    Count the VehicleActivity elements in a SIRI-VM body.

    Parameters
    ----------
    body : bytes
        The SIRI-VM document.

    Returns
    -------
    int
        The count.
    """
    return sum(1 for _ in siri_root(body=body).iter(f'{SIRI}VehicleActivity'))


def cut_siri(*, body: bytes) -> bytes:
    """
    Keep only the elements the sightings reader uses.

    Parameters
    ----------
    body : bytes
        The SIRI-VM document.

    Returns
    -------
    bytes
        A document with each complete activity's RecordedAtTime and the
        journey's line, operator and vehicle elements.
    """
    ElementTree.register_namespace('', SIRI[1:-1])
    root = ElementTree.Element(f'{SIRI}Siri')
    delivery = ElementTree.SubElement(
        ElementTree.SubElement(root, f'{SIRI}ServiceDelivery'),
        f'{SIRI}VehicleMonitoringDelivery',
    )
    for activity in siri_root(body=body).iter(f'{SIRI}VehicleActivity'):
        journey = activity.find(f'{SIRI}MonitoredVehicleJourney')
        if to_sighting(activity=activity) is None or journey is None:
            continue
        kept = ElementTree.SubElement(delivery, f'{SIRI}VehicleActivity')
        stamp = ElementTree.SubElement(kept, f'{SIRI}RecordedAtTime')
        stamp.text = activity.findtext(f'{SIRI}RecordedAtTime')
        kept_journey = ElementTree.SubElement(
            kept, f'{SIRI}MonitoredVehicleJourney',
        )
        for tag in JOURNEY_TAGS:
            child = journey.find(f'{SIRI}{tag}')
            if child is not None:
                kept_journey.append(child)
    return ElementTree.tostring(root, xml_declaration=True)


def live_problem(
    *,
    bodies: Mapping[str, bytes],
    at: datetime,
) -> str | None:
    """
    Check the live bodies against the box and print their counts.

    Parameters
    ----------
    bodies : Mapping[str, bytes]
        The three live bodies by fixture name.
    at : datetime
        When they were fetched.

    Returns
    -------
    str | None
        What is wrong, or None when every vehicle is in the box.

    Raises
    ------
    FeedError
        When a body does not parse.
    """
    snapshot = parse_positions(
        data=bodies[POSITIONS_RAW],
        fetched_at=at,
        projection=CAMBRIDGE_PROJECTION,
    )
    print(f'GTFS-RT vehicles: {len(snapshot.vehicles)}')
    places = []
    for operator, name in SIRI_NAMES.items():
        print(f'{operator} activities: {activity_count(body=bodies[name])}')
        places += activity_places(body=bodies[name])
    if not snapshot.vehicles:
        return 'no vehicle in the GTFS-RT answer'
    far = outside(
        snapshot=snapshot, box=CAMBRIDGE_BBOX, projection=CAMBRIDGE_PROJECTION,
    ) + sum(not inside(place=place, box=CAMBRIDGE_BBOX) for place in places)
    if far:
        return (
            f'{far} of {len(snapshot.vehicles) + len(places)} vehicles '
            'lie outside the box'
        )
    return None


def joined_problem(
    *,
    joined: bytes,
    source: Path,
    at: datetime,
) -> str | None:
    """
    Check that the named positions can show every kind of vehicle.

    Parameters
    ----------
    joined : bytes
        The positions with the coaches named.
    source : Path
        The full timetable zip.
    at : datetime
        When the positions were fetched.

    Returns
    -------
    str | None
        What is missing, or None when nothing is.
    """
    vehicles = parse_positions(
        data=joined, fetched_at=at, projection=CAMBRIDGE_PROJECTION,
    ).vehicles
    if not any(v.label and not v.trip_id for v in vehicles):
        return 'no coach in the box: record again in the daytime'
    if not trips_of(vehicles=vehicles, source=source, only_by_route=True):
        return f'no live vehicle on route {BY_ROUTE[1]}'
    return None


def trips_of(
    *,
    vehicles: Collection[VehiclePosition],
    source: Path,
    only_by_route: bool,
) -> set[str]:
    """
    List the trips of the vehicles, or only those on the by-route route.

    Parameters
    ----------
    vehicles : Collection[VehiclePosition]
        The parsed vehicles.
    source : Path
        The full timetable zip.
    only_by_route : bool
        True to list only the trips on the by-route route.

    Returns
    -------
    set[str]
        Trip ids; a vehicle with none is left out.
    """
    wanted = {
        route_id
        for route_id, route in route_index(source=source).items()
        if route == BY_ROUTE or not only_by_route
    }
    trip_route = {
        row['trip_id']: row['route_id']
        for row in read_rows(archive=source, member='trips.txt')
    }
    return {
        vehicle.trip_id
        for vehicle in vehicles
        if vehicle.trip_id
        and trip_route.get(vehicle.trip_id, vehicle.route_id) in wanted
    }


def feed_version(*, source: Path) -> str:
    """
    Read the timetable's feed version.

    Parameters
    ----------
    source : Path
        The full timetable zip.

    Returns
    -------
    str
        The feed_version of the first feed_info row; '' when there is
        none.
    """
    for row in read_rows(archive=source, member='feed_info.txt'):
        return row.get('feed_version', '')
    return ''


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """
    Read the command line.

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments after the program name; the process's when None.

    Returns
    -------
    argparse.Namespace
        Parsed arguments; --gtfs and --out are both given unless --check
        is.
    """
    parser = argparse.ArgumentParser(prog='record_bods_fixtures')
    parser.add_argument('--gtfs', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if not args.check and not (args.gtfs and args.out):
        parser.error('give --gtfs and --out, or --check')
    return args


def fixtures(
    *,
    bodies: Mapping[str, bytes],
    source: Path,
    at: datetime,
    work: Path,
) -> dict[str, bytes]:
    """
    Make every fixture file from the live bodies and the timetable.

    Parameters
    ----------
    bodies : Mapping[str, bytes]
        The three live bodies by fixture name.
    source : Path
        The full timetable zip.
    at : datetime
        When the bodies were fetched.
    work : Path
        A directory to cut the zip in.

    Returns
    -------
    dict[str, bytes]
        The files to write, by name; empty when a check fails, after
        printing why.
    """
    cut = {name: cut_siri(body=bodies[name]) for name in SIRI_NAMES.values()}
    sightings: list[CoachSighting] = [
        found for body in cut.values() for found in coach_sightings(data=body)
    ]
    joined = named(data=bodies[POSITIONS_RAW], sightings=sightings)
    problem = joined_problem(joined=joined, source=source, at=at)
    if problem:
        print(f'record: {problem}', file=sys.stderr)
        return {}
    vehicles = parse_positions(
        data=joined, fetched_at=at, projection=CAMBRIDGE_PROJECTION,
    ).vehicles
    far = cut_zip(
        source=source,
        out=work / ZIP_NAME,
        day=at.date(),
        live_trips=trips_of(
            vehicles=vehicles, source=source, only_by_route=False,
        ),
    )
    summary = {
        'at': at.isoformat(timespec='seconds'),
        'feed_version': feed_version(source=source),
        'far_route': far,
        'by_route': BY_ROUTE[1],
        'operators': {
            operator: activity_count(body=cut[name])
            for operator, name in SIRI_NAMES.items()
        },
    }
    return {
        ZIP_NAME: (work / ZIP_NAME).read_bytes(),
        POSITIONS_JOINED: joined,
        POSITIONS_RAW: bodies[POSITIONS_RAW],
        **cut,
        RECORDED_NAME: json.dumps(summary, indent=2).encode(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """
    Record the fixtures, or with --check only test the live feeds.

    Parameters
    ----------
    argv : Sequence[str] | None
        Arguments after the program name; the process's when None.

    Returns
    -------
    int
        0 on success, 1 when a check fails or a request does, 2 when
        there is no key. Nothing printed holds the key, a URL or a body.
    """
    args = parse_args(argv)
    key = SecretsFile(path=key_path_from_env()).read()
    if key is None:
        print('record: no key file', file=sys.stderr)
        return 2
    try:
        return run(args=args, key=key)
    except (FeedError, OSError, ValueError, zipfile.BadZipFile) as error:
        print(f'record: {error}', file=sys.stderr)
        return 1


def run(*, args: argparse.Namespace, key: str) -> int:
    """
    Fetch the feeds, check them and write the fixtures.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.
    key : str
        The API key.

    Returns
    -------
    int
        The exit status.
    """
    bodies = fetched(key=key)
    at = datetime.now(ZONE)
    holding = leaks(files=bodies, key=key)
    if holding:
        print(
            f'record: the key is in {", ".join(holding)}; nothing written',
            file=sys.stderr,
        )
        return 1
    problem = live_problem(bodies=bodies, at=at)
    if problem:
        print(f'record: {problem}', file=sys.stderr)
        return 1
    if args.check:
        return 0
    return write_all(args=args, bodies=bodies, at=at, key=key)


def write_all(
    *,
    args: argparse.Namespace,
    bodies: Mapping[str, bytes],
    at: datetime,
    key: str,
) -> int:
    """
    Cut the fixtures and write them, unless one holds the key.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments, with --gtfs and --out.
    bodies : Mapping[str, bytes]
        The three live bodies by fixture name.
    at : datetime
        When they were fetched.
    key : str
        The API key.

    Returns
    -------
    int
        The exit status.
    """
    with tempfile.TemporaryDirectory() as work:
        files = fixtures(
            bodies=bodies, source=args.gtfs, at=at, work=Path(work),
        )
    if not files:
        return 1
    holding = record(out=args.out, files=files, key=key)
    if holding:
        print(
            f'record: the key is in {", ".join(holding)}; nothing written',
            file=sys.stderr,
        )
        return 1
    for name, data in files.items():
        print(f'{name}: {len(data)} bytes')
    print(f'replay from: {at.isoformat(timespec="seconds")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
