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
from paperroute_cambridge.adapters.siri_cut import (
    activity_count,
    activity_places,
    cut_siri,
)
from paperroute_cambridge.adapters.siri_vm import (
    CoachSighting,
    coach_sightings,
)
from paperroute_cambridge.area import (
    CAMBRIDGE_BBOX,
    CAMBRIDGE_PROJECTION,
    TRIM_RADIUS,
)
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
UNKNOWN_TRIP: Final[str] = (
    'a live vehicle on {short} has a trip the timetable lacks: '
    'download the timetable again'
)
ZONE: Final[ZoneInfo] = ZoneInfo('Europe/London')
POSITIONS_RAW: Final[str] = 'bods_positions_raw.pb'
POSITIONS_JOINED: Final[str] = 'bods_positions.pb'
ZIP_NAME: Final[str] = 'bods.zip'
RECORDED_NAME: Final[str] = 'recorded.json'
SIRI_NAMES: Final[dict[str, str]] = {
    operator: f'siri_{operator.lower()}.xml' for operator in COACH_OPERATORS
}
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


def route_reach(*, source: Path) -> dict[str, float]:
    """
    Find how close each route comes to the centre.

    Parameters
    ----------
    source : Path
        The timetable zip.

    Returns
    -------
    dict[str, float]
        Route id to the distance in metres of its nearest stop; a route
        with no stop times is absent.
    """
    metres = {
        row['stop_id']: math.hypot(
            *to_metres(
                lon=float(row['stop_lon']),
                lat=float(row['stop_lat']),
                projection=CAMBRIDGE_PROJECTION,
            ),
        )
        for row in read_rows(archive=source, member='stops.txt')
    }
    trip_route = trip_routes(source=source)
    reach: dict[str, float] = {}
    for row in read_rows(archive=source, member='stop_times.txt'):
        route = trip_route[row['trip_id']]
        reach[route] = min(
            reach.get(route, math.inf), metres.get(row['stop_id'], math.inf),
        )
    return reach


def trip_routes(*, source: Path) -> dict[str, str]:
    """
    Map each trip id to its route id.

    Parameters
    ----------
    source : Path
        The timetable zip.

    Returns
    -------
    dict[str, str]
        Route id by trip id.
    """
    return {
        row['trip_id']: row['route_id']
        for row in read_rows(archive=source, member='trips.txt')
    }


def matched_routes(
    *,
    source: Path,
    reach: Mapping[str, float],
) -> dict[str, Route]:
    """
    Find the routes that match a KEPT pair and lie near the centre.

    Parameters
    ----------
    source : Path
        The timetable zip.
    reach : Mapping[str, float]
        Each route's nearest stop, from ``route_reach``.

    Returns
    -------
    dict[str, Route]
        Route id to its operator code and short name; a route with no
        stop within the trim radius is left out.
    """
    return {
        route_id: route
        for route_id, route in route_index(source=source).items()
        if route in KEPT and reach.get(route_id, math.inf) <= TRIM_RADIUS
    }


def far_route(
    *,
    source: Path,
    day: date,
    reach: Mapping[str, float],
) -> tuple[str, str]:
    """
    Find the first far route with a trip running on the day.

    Parameters
    ----------
    source : Path
        The timetable zip.
    day : date
        The day.
    reach : Mapping[str, float]
        Each route's nearest stop, from ``route_reach``.

    Returns
    -------
    tuple[str, str]
        Its route id and short name.

    Raises
    ------
    ValueError
        When no route with a trip on the day lies beyond the limit.
    """
    services = running_services(source=source, day=day)
    running = {
        row['route_id']
        for row in read_rows(archive=source, member='trips.txt')
        if row['service_id'] in services
    }
    for row in read_rows(archive=source, member='routes.txt'):
        route = row['route_id']
        if route in running and reach.get(route, 0.0) > FAR_LIMIT:
            return route, row['route_short_name']
    raise ValueError('no route far from the centre runs on the day')


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
    reach = route_reach(source=source)
    routes = matched_routes(source=source, reach=reach)
    far_id, far_name = far_route(source=source, day=day, reach=reach)
    kept = set(routes)
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
    matched: Mapping[str, Route],
    trip_route: Mapping[str, str],
    at: datetime,
) -> str | None:
    """
    Check that the named positions can show every kind of vehicle.

    Parameters
    ----------
    joined : bytes
        The positions with the coaches named.
    matched : Mapping[str, Route]
        The kept routes near the centre, by route id.
    trip_route : Mapping[str, str]
        The timetable's route id by trip id.
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
    by_route = {rid for rid, route in matched.items() if route == BY_ROUTE}
    trips = trips_on(vehicles=vehicles, routes=by_route, trip_route=trip_route)
    if not trips:
        return f'no live vehicle on route {BY_ROUTE[1]}'
    for vehicle in vehicles:
        route = matched.get(vehicle.route_id)
        if (
            route
            and route != BY_ROUTE
            and vehicle.trip_id
            and vehicle.trip_id not in trip_route
        ):
            return UNKNOWN_TRIP.format(short=route[1])
    return None


def trips_on(
    *,
    vehicles: Collection[VehiclePosition],
    routes: Collection[str],
    trip_route: Mapping[str, str],
) -> set[str]:
    """
    List the trips of the vehicles that are on the given routes.

    Parameters
    ----------
    vehicles : Collection[VehiclePosition]
        The parsed vehicles.
    routes : Collection[str]
        Route ids.
    trip_route : Mapping[str, str]
        The timetable's route id by trip id.

    Returns
    -------
    set[str]
        Trip ids; a vehicle with none is left out. A trip the timetable
        lacks is placed by the vehicle's own route id.
    """
    return {
        vehicle.trip_id
        for vehicle in vehicles
        if vehicle.trip_id
        and trip_route.get(vehicle.trip_id, vehicle.route_id) in routes
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
    matched = matched_routes(source=source, reach=route_reach(source=source))
    for pair in KEPT:
        ids = sorted(rid for rid, route in matched.items() if route == pair)
        print(f'{pair[0]} {pair[1]}: {len(ids)} routes: {", ".join(ids)}')
    problem = joined_problem(
        joined=joined,
        matched=matched,
        trip_route=trip_routes(source=source),
        at=at,
    )
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
        live_trips={v.trip_id for v in vehicles if v.trip_id},
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
