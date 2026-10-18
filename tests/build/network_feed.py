"""A small rail and bus network with blocks, for run and chooser tests."""

from pathlib import Path
from typing import Final

from tests.build.feeds import DAILY, stop_times
from tests.helpers import write_feed

LAT: Final[float] = -33.88
LON: Final[float] = 151.0
STEP_LAT: Final[float] = 0.018  # about 2 km north
STEP_LON: Final[float] = 0.0216  # about 2 km east
HOP: Final[int] = 180  # seconds between stations
STATIONS: Final[dict[str, tuple[float, float]]] = {
    'W2': (0, 0), 'W1': (1, 0), 'J': (2, 0), 'C': (3, 0), 'E1': (4, 0),
    'E2': (5, 0), 'R1': (2, 1), 'R2': (2, 2), 'R3': (2, 3), 'L1': (3, 1),
    'L2': (2.5, 1),
}
BUS_STOPS: Final[dict[str, tuple[float, float]]] = {
    'B1': (6, 0), 'B2': (7, 0), 'B3': (8, 0),
}
EAST_WEST: Final[str] = 'E2 E1 C J W1 W2'
EAST_NORTH: Final[str] = 'E2 E1 C J R1 R2 R3'


def stop_row(*, stop_id: str, at: tuple[float, float],
             parent: str | None) -> dict[str, str]:
    """
    Build a stops.txt row at a grid position.

    Parameters
    ----------
    stop_id : str
        Stop ID.
    at : tuple[float, float]
        (east, north) in 2 km steps.
    parent : str | None
        Parent station of a platform, else None.

    Returns
    -------
    dict[str, str]
        Row.
    """
    return {
        'stop_id': stop_id, 'stop_name': f'{stop_id} Stop',
        'location_type': '0',
        'parent_station': parent or '',
        'stop_lat': str(LAT + at[1] * STEP_LAT),
        'stop_lon': str(LON + at[0] * STEP_LON),
    }


def network_stops() -> list[dict[str, str]]:
    """
    Parent stations, a platform under each, and plain bus stops.

    Returns
    -------
    list[dict[str, str]]
        stops.txt rows.
    """
    stations = [
        {**stop_row(stop_id=station, at=at, parent=None),
         'location_type': '1'}
        for station, at in STATIONS.items()
    ]
    platforms = [
        stop_row(stop_id=f'{station}_1', at=at, parent=station)
        for station, at in STATIONS.items()
    ]
    buses = [
        stop_row(stop_id=stop, at=at, parent=None)
        for stop, at in BUS_STOPS.items()
    ]
    return [*stations, *platforms, *buses]


def clock(seconds: int) -> str:
    """
    Format seconds as a GTFS time.

    Parameters
    ----------
    seconds : int
        Seconds after the service day's start.

    Returns
    -------
    str
        'HH:MM:SS'.
    """
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


def network_trip(
    *, trip: str, route: str, calls: str, start: str,
    block: str = '', headsign: str | None = None,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """
    Build one trip and its stop times, a hop every three minutes.

    Parameters
    ----------
    trip : str
        Trip ID.
    route : str
        Route ID.
    calls : str
        Station or bus stop IDs, space-separated; stations are served at
        their '_1' platform.
    start : str
        'HH:MM' of the first departure.
    block : str
        Block ID, '' for none.
    headsign : str | None
        Headsign; None for the last station ID.

    Returns
    -------
    tuple[dict[str, str], list[dict[str, str]]]
        trips.txt row and stop_times.txt rows.
    """
    stops = calls.split()
    hours, minutes = start.split(':')
    first = int(hours) * 3600 + int(minutes) * 60
    row = {
        'route_id': route, 'service_id': 'daily', 'trip_id': trip,
        'trip_headsign': stops[-1] if headsign is None else headsign,
        'direction_id': '0', 'block_id': block,
    }
    times = [
        (stop if stop in BUS_STOPS else f'{stop}_1', clock(first + i * HOP))
        for i, stop in enumerate(stops)
    ]
    return row, stop_times(trip=trip, times=times)


def daily_trips() -> list[tuple[dict[str, str], list[dict[str, str]]]]:
    """
    The T1 and T2 trips outside blocks, as network_trip results.

    Returns
    -------
    list[tuple[dict[str, str], list[dict[str, str]]]]
        Trips with their stop times.
    """
    west_east = ' '.join(reversed(EAST_WEST.split()))
    north_east = ' '.join(reversed(EAST_NORTH.split()))
    shapes = [
        ('ew', 'T1', EAST_WEST, 5), ('we', 'T1', west_east, 6),
        ('er', 'T1', EAST_NORTH, 3), ('re', 'T1', north_east, 3),
        ('cc', 'T2', 'C L1 L2 J C E1 E2', 2),
    ]
    return [
        network_trip(trip=f'{prefix}{n}', route=route, calls=calls,
                     start=f'{5 + n * 2:02}:{offset * 10:02}')
        for offset, (prefix, route, calls, count) in enumerate(shapes)
        for n in range(1, count + 1)
    ]


def block_trips() -> list[tuple[dict[str, str], list[dict[str, str]]]]:
    """
    The block trips and the Empty Train, as network_trip results.

    Returns
    -------
    list[tuple[dict[str, str], list[dict[str, str]]]]
        Trips with their stop times.
    """
    return [
        network_trip(trip='x1', route='T1', calls='E2 E1 C', start='08:14',
                     block='b1'),
        network_trip(trip='x2', route='T1', calls='C J W1', start='08:25',
                     block='b1'),
        network_trip(trip='y1', route='T1', calls=EAST_WEST, start='07:41',
                     block='b2'),
        network_trip(trip='y2', route='T1', calls='W2 W1 J', start='08:05',
                     block='b2'),
        network_trip(trip='z1', route='T1', calls='C E1 E2', start='09:04',
                     block='b3'),
        network_trip(trip='z2', route='T1', calls='R3 R2 R1 J',
                     start='09:30', block='b3'),
        network_trip(trip='v1', route='809', calls='B1 B2', start='09:57',
                     block='b4'),
        network_trip(trip='v2', route='811', calls='B2 B3', start='10:05',
                     block='b4', headsign='Parramatta Stn'),
        network_trip(trip='empty1', route='T1', calls='C J R1',
                     start='11:00', headsign='Empty Train'),
    ]


def network_files() -> dict[str, list[dict[str, str]]]:
    """
    Sydney Trains-like feed of the east-west line, its branch and loop.

    The T2 loop trips 'cc1' and 'cc2' run C L1 L2 J C E1 E2, the City
    Circle shape. 'y1' is one of the six T1 trips E2 to W2. Every headsign
    is the trip's last station ID, except the bus 'v2' ('Parramatta Stn')
    and the 'Empty Train' 'empty1'.

    Returns
    -------
    dict[str, list[dict[str, str]]]
        Member name to rows.
    """
    trips = [*daily_trips(), *block_trips()]
    routes = [
        ('T1', '2', 'SydneyTrains'), ('T2', '2', 'SydneyTrains'),
        ('809', '700', '2436'), ('811', '700', '2436'),
    ]
    return {
        'stops.txt': network_stops(),
        'routes.txt': [
            {'route_id': route, 'agency_id': agency,
             'route_short_name': route, 'route_long_name': f'Line {route}',
             'route_type': route_type}
            for route, route_type, agency in routes
        ],
        'calendar.txt': [{'service_id': 'daily', **DAILY}],
        'trips.txt': [row for row, _ in trips],
        'stop_times.txt': [time for _, times in trips for time in times],
    }


def network_feed(*, path: Path) -> Path:
    """
    Write the feed of network_files.

    Parameters
    ----------
    path : Path
        Zip to write.

    Returns
    -------
    Path
        The zip.
    """
    return write_feed(path=path, files=network_files())
