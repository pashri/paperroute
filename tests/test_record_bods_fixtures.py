"""Tests for the BODS fixture recording script."""

import csv
import importlib.util
import io
import json
import sys
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

import pytest
from google.transit import gtfs_realtime_pb2

from paperroute_cambridge.area import CAMBRIDGE_BBOX, CAMBRIDGE_PROJECTION
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_transit.adapters.feed_errors import FeedError
from paperroute_transit.core.geo import to_metres
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'record_bods_fixtures.py'
KEY = 'k-secret'
DAY = date(2026, 11, 16)
SIRI = 'http://www.siri.org.uk/siri'
STAMP = 1_000
EVERY_DAY = {
    day: '1'
    for day in (
        'monday',
        'tuesday',
        'wednesday',
        'thursday',
        'friday',
        'saturday',
        'sunday',
    )
}
NO_DAY = {day: '0' for day in EVERY_DAY}


def load_script() -> ModuleType:
    """
    Import the recording script.

    Returns
    -------
    ModuleType
        The script as a module.
    """
    spec = importlib.util.spec_from_file_location(
        'record_bods_fixtures', SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recorder = load_script()


def trip_rows(
    *,
    route: str,
    count: int,
    other: int = 1,
) -> list[dict[str, str]]:
    """
    Make the trips of one route: some on the day, some on another day.

    Parameters
    ----------
    route : str
        The route's short name; also the route id's tail.
    count : int
        Trips running every day.
    other : int
        Trips that never run.

    Returns
    -------
    list[dict[str, str]]
        Trip rows, in one direction.
    """
    ran = [
        {
            'route_id': f'r-{route}',
            'service_id': 'wk',
            'trip_id': f'{route.lower()}-{n}',
            'direction_id': '0',
        }
        for n in range(1, count + 1)
    ]
    idle = [
        {
            'route_id': f'r-{route}',
            'service_id': 'none',
            'trip_id': f'{route.lower()}-{count + n}',
            'direction_id': '0',
        }
        for n in range(1, other + 1)
    ]
    return ran + idle


def source_zip(*, path: Path) -> Path:
    """
    Write a small timetable zip with near, kept and far routes.

    Parameters
    ----------
    path : Path
        Zip to write.

    Returns
    -------
    Path
        The zip.
    """
    routes = [
        ('PR2', 'OP1'),
        ('4', 'OP1'),
        ('777', 'OP5050'),
        ('9', 'OP1'),
        ('X98', 'OP9'),
        ('X99', 'OP9'),
    ]
    trips = [
        row
        for short, _ in routes
        for row in trip_rows(route=short, count=0 if short == 'X98' else 6)
    ] + [
        {**row, 'route_id': 'r-4-far', 'trip_id': row['trip_id'] + 'far'}
        for row in trip_rows(route='4', count=6)
    ]
    far = {'r-X98', 'r-X99', 'r-4-far'}
    return write_feed(
        path=path,
        files={
            'agency.txt': [
                {'agency_id': 'OP1', 'agency_noc': 'SCCM'},
                {'agency_id': 'OP5050', 'agency_noc': 'NATX'},
                {'agency_id': 'OP9', 'agency_noc': 'FARO'},
            ],
            'routes.txt': [
                {
                    'route_id': f'r-{short}',
                    'agency_id': agency,
                    'route_short_name': short,
                }
                for short, agency in routes
            ]
            + [
                {
                    'route_id': 'r-4-far',
                    'agency_id': 'OP1',
                    'route_short_name': '4',
                },
            ],
            'trips.txt': trips,
            'stop_times.txt': [
                {
                    'trip_id': row['trip_id'],
                    'stop_id': 's-far' if row['route_id'] in far else 's-near',
                    'stop_sequence': '1',
                }
                for row in trips
            ],
            'stops.txt': [
                {'stop_id': 's-near', 'stop_lon': '0.12', 'stop_lat': '52.2'},
                {'stop_id': 's-far', 'stop_lon': '1.5', 'stop_lat': '53.5'},
            ],
            'calendar.txt': [
                {
                    'service_id': 'wk',
                    'start_date': '20200101',
                    'end_date': '20991231',
                    **EVERY_DAY,
                },
                {
                    'service_id': 'none',
                    'start_date': '20200101',
                    'end_date': '20991231',
                    **NO_DAY,
                },
            ],
            'feed_info.txt': [{'feed_version': 'v1'}],
        },
    )


def members(*, path: Path, name: str) -> list[dict[str, str]]:
    """
    Read one CSV member of a zip.

    Parameters
    ----------
    path : Path
        The zip.
    name : str
        Member name.

    Returns
    -------
    list[dict[str, str]]
        Its rows.
    """
    with zipfile.ZipFile(path) as archive:
        text = archive.read(name).decode()
    return list(csv.DictReader(io.StringIO(text)))


def names(path: Path) -> set[str]:
    """
    List a zip's member names.

    Parameters
    ----------
    path : Path
        The zip.

    Returns
    -------
    set[str]
        The names.
    """
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def short_names(path: Path) -> set[str]:
    """
    List the route short names in a zip.

    Parameters
    ----------
    path : Path
        The zip.

    Returns
    -------
    set[str]
        The short names.
    """
    return {
        row['route_short_name']
        for row in members(path=path, name='routes.txt')
    }


def trip_ids(path: Path, *, route: str) -> set[str]:
    """
    List a route's trip ids in a zip.

    Parameters
    ----------
    path : Path
        The zip.
    route : str
        The route's short name.

    Returns
    -------
    set[str]
        The trip ids.
    """
    return {
        row['trip_id']
        for row in members(path=path, name='trips.txt')
        if row['route_id'] == f'r-{route}'
    }


def all_trip_ids(path: Path) -> set[str]:
    """
    List every trip id in a zip.

    Parameters
    ----------
    path : Path
        The zip.

    Returns
    -------
    set[str]
        The trip ids.
    """
    return {row['trip_id'] for row in members(path=path, name='trips.txt')}


def stop_time_trips(path: Path) -> set[str]:
    """
    List the trips that have stop times in a zip.

    Parameters
    ----------
    path : Path
        The zip.

    Returns
    -------
    set[str]
        The trip ids.
    """
    return {
        row['trip_id'] for row in members(path=path, name='stop_times.txt')
    }


def zip_of(files: dict[str, bytes]) -> bytes:
    """
    Make a deflated zip.

    Parameters
    ----------
    files : dict[str, bytes]
        Member contents by name.

    Returns
    -------
    bytes
        The zip.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def snapshot_at(*places: tuple[float, float]) -> PositionsSnapshot:
    """
    Make a snapshot with a vehicle at each longitude and latitude.

    Parameters
    ----------
    *places : tuple[float, float]
        Longitude and latitude of each vehicle.

    Returns
    -------
    PositionsSnapshot
        The snapshot.
    """
    vehicles = tuple(
        VehiclePosition(
            trip_id='',
            route_id='',
            label='',
            point=to_metres(
                lon=lon, lat=lat, projection=CAMBRIDGE_PROJECTION,
            ),
            bearing=None,
            timestamp=STAMP,
            start_date=None,
        )
        for lon, lat in places
    )
    return PositionsSnapshot(
        fetched_at=recorder.datetime.now(recorder.ZONE),
        header_time=STAMP,
        vehicles=vehicles,
    )


def positions_body(
    vehicles: tuple[tuple[str, str, str], ...] = (
        ('c1', '', ''),
        ('b1', '4-2', 'r-4'),
    ),
    label: str = '',
) -> bytes:
    """
    Build a positions body, by default a coach and a vehicle on route 4.

    Parameters
    ----------
    vehicles : tuple[tuple[str, str, str], ...]
        Vehicle id, trip id and route id of each.
    label : str
        Label of every vehicle.

    Returns
    -------
    bytes
        The serialised message, every vehicle inside the box.
    """
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = '2.0'
    message.header.timestamp = STAMP
    for number, (vehicle, trip, route) in enumerate(vehicles):
        entity = message.entity.add()
        entity.id = str(number)
        entity.vehicle.vehicle.id = vehicle
        entity.vehicle.vehicle.label = label
        entity.vehicle.trip.trip_id = trip
        entity.vehicle.trip.route_id = route
        entity.vehicle.timestamp = STAMP
        entity.vehicle.position.longitude = 0.12
        entity.vehicle.position.latitude = 52.2
    return bytes(message.SerializeToString())


def siri_body(*, vehicles: tuple[str, ...], leak: str = '') -> bytes:
    """
    Build a SIRI-VM body with a coach activity for each vehicle.

    Parameters
    ----------
    vehicles : tuple[str, ...]
        Vehicle refs.
    leak : str
        Text to put in an attribute of the root, as a URL would.

    Returns
    -------
    bytes
        The XML document.
    """
    ElementTree.register_namespace('', SIRI)
    root = ElementTree.Element(f'{{{SIRI}}}Siri')
    if leak:
        root.set('href', f'?api_key={leak}')
    delivery = ElementTree.SubElement(root, f'{{{SIRI}}}VehicleMonitoring')
    for vehicle in vehicles:
        activity = ElementTree.SubElement(
            delivery, f'{{{SIRI}}}VehicleActivity',
        )
        ElementTree.SubElement(
            activity, f'{{{SIRI}}}RecordedAtTime',
        ).text = '1970-01-01T00:16:40+00:00'
        journey = ElementTree.SubElement(
            activity, f'{{{SIRI}}}MonitoredVehicleJourney',
        )
        for tag, text in (
            ('PublishedLineName', '777'),
            ('OperatorRef', 'NATX'),
            ('VehicleRef', vehicle),
        ):
            ElementTree.SubElement(journey, f'{{{SIRI}}}{tag}').text = text
        place = ElementTree.SubElement(
            journey, f'{{{SIRI}}}VehicleLocation',
        )
        ElementTree.SubElement(place, f'{{{SIRI}}}Longitude').text = '0.12'
        ElementTree.SubElement(place, f'{{{SIRI}}}Latitude').text = '52.2'
    return ElementTree.tostring(root, xml_declaration=True)


def raising(error: Exception) -> Callable[..., dict[str, bytes]]:
    """
    Make a stand-in for `fetched` that raises.

    Parameters
    ----------
    error : Exception
        What to raise.

    Returns
    -------
    Callable[..., dict[str, bytes]]
        The stand-in.
    """

    def fetch(*, key: str) -> dict[str, bytes]:
        raise error

    return fetch


def live() -> dict[str, bytes]:
    """
    Make three clean live bodies.

    Returns
    -------
    dict[str, bytes]
        The bodies by fixture name.
    """
    return {
        'bods_positions_raw.pb': positions_body(),
        'siri_natx.xml': siri_body(vehicles=('c1',)),
    }


@pytest.fixture(name='keyed')
def keyed_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """
    Point the key file variable at a file holding the test key.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory.
    monkeypatch : pytest.MonkeyPatch
        Environment patcher.

    Returns
    -------
    Path
        The temporary directory.
    """
    (tmp_path / 'secrets').write_text(KEY)
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_BODS', str(tmp_path / 'secrets'))
    return tmp_path


def test_cut_zip_keeps_the_named_routes_and_one_far_route(
    tmp_path: Path,
) -> None:
    """Cut zip keeps the named routes and one far route."""
    source = source_zip(path=tmp_path / 'source.zip')
    far = recorder.cut_zip(
        source=source, out=tmp_path / 'bods.zip', day=DAY, live_trips=(),
    )
    assert far == 'X99'
    assert short_names(tmp_path / 'bods.zip') == {'PR2', '4', '777', 'X99'}


def test_cut_zip_keeps_four_trips_a_direction_and_the_live_ones(
    tmp_path: Path,
) -> None:
    """Cut zip keeps four trips a direction and the live ones."""
    source = source_zip(path=tmp_path / 'source.zip')
    out = tmp_path / 'bods.zip'
    recorder.cut_zip(source=source, out=out, day=DAY, live_trips={'pr2-6'})
    assert trip_ids(out, route='PR2') == {
        'pr2-1',
        'pr2-2',
        'pr2-3',
        'pr2-4',
        'pr2-6',
    }
    assert stop_time_trips(out) == all_trip_ids(out)
    assert 'feed_info.txt' in names(out)


def test_cut_zip_leaves_out_the_live_trips_of_the_by_route_route(
    tmp_path: Path,
) -> None:
    """Cut zip leaves out the live trips of the by route route."""
    source = source_zip(path=tmp_path / 'source.zip')
    out = tmp_path / 'bods.zip'
    recorder.cut_zip(
        source=source, out=out, day=DAY, live_trips={'4-2', '4-6'},
    )
    assert trip_ids(out, route='4') == {'4-1', '4-3', '4-4', '4-5'}


def test_outside_counts_vehicles_beyond_the_box() -> None:
    """Outside counts vehicles beyond the box."""
    snapshot = snapshot_at((0.12, 52.20), (0.12, 53.00), (-0.60, 52.20))
    assert (
        recorder.outside(
            snapshot=snapshot,
            box=CAMBRIDGE_BBOX,
            projection=CAMBRIDGE_PROJECTION,
        )
        == 2
    )


def test_leaks_names_the_files_holding_the_key() -> None:
    """Leaks names the files holding the key."""
    files = {
        'a.pb': b'xx k-secret yy',
        'b.xml': b'clean',
        'c.zip': zip_of({'trips.txt': b'x,' + KEY.encode() * 40}),
    }
    assert KEY.encode() not in files['c.zip']
    assert recorder.leaks(files=files, key=KEY) == ['a.pb', 'c.zip']


def test_record_refuses_a_body_holding_the_key(tmp_path: Path) -> None:
    """Record refuses a body holding the key."""
    files = {
        'a.pb': b'clean',
        'siri_natx.xml': b'<a href="?api_key=k-secret"/>',
    }
    assert recorder.record(out=tmp_path, files=files, key=KEY) == [
        'siri_natx.xml',
    ]
    assert not list(tmp_path.iterdir())


def test_record_writes_every_clean_file(tmp_path: Path) -> None:
    """Record writes every clean file."""
    files = {'a.pb': b'1', 'b.xml': b'2'}
    assert recorder.record(out=tmp_path, files=files, key=KEY) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        'a.pb',
        'b.xml',
    ]


def test_main_writes_nothing_and_hides_the_key_when_a_body_holds_it(
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main writes nothing and hides the key when a body holds it."""
    source = source_zip(path=keyed / 'source.zip')
    leaky = live() | {'siri_natx.xml': siri_body(vehicles=('c1',), leak=KEY)}
    monkeypatch.setattr(recorder, 'fetched', lambda *, key: leaky)
    out = keyed / 'out'
    assert recorder.main(['--gtfs', str(source), '--out', str(out)]) == 1
    assert not out.exists()
    printed = capsys.readouterr()
    assert KEY not in printed.out + printed.err
    assert 'the key is in siri_natx.xml; nothing written' in printed.err


def test_main_prints_only_the_feed_errors_own_text(
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main prints only the feed errors own text."""
    source = source_zip(path=keyed / 'source.zip')
    monkeypatch.setattr(
        recorder, 'fetched', raising(FeedError('bods/positions: HTTP 403')),
    )
    args = ['--gtfs', str(source), '--out', str(keyed / 'out')]
    assert recorder.main(args) == 1
    assert capsys.readouterr().err == 'record: bods/positions: HTTP 403\n'


def test_main_records_the_files_and_prints_only_sizes(
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main records the files and prints only sizes."""
    source = source_zip(path=keyed / 'source.zip')
    monkeypatch.setattr(recorder, 'fetched', lambda *, key: live())
    out = keyed / 'out'
    assert recorder.main(['--gtfs', str(source), '--out', str(out)]) == 0
    assert sorted(path.name for path in out.iterdir()) == [
        'bods.zip',
        'bods_positions.pb',
        'bods_positions_raw.pb',
        'recorded.json',
        'siri_natx.xml',
    ]
    summary = json.loads((out / 'recorded.json').read_text())
    assert summary['far_route'] == 'X99'
    assert summary['by_route'] == '4'
    assert summary['operators'] == {'NATX': 1}
    assert '4-2' not in all_trip_ids(out / 'bods.zip')
    printed = capsys.readouterr()
    assert KEY not in printed.out + printed.err
    assert 'replay from: ' in printed.out
    assert 'SCCM 4: 1 routes: r-4\n' in printed.out


def test_main_check_writes_nothing(
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Main check writes nothing."""
    monkeypatch.setattr(recorder, 'fetched', lambda *, key: live())
    assert recorder.main(['--check']) == 0
    assert [path.name for path in keyed.iterdir()] == ['secrets']


def test_main_stops_when_a_vehicle_lies_outside_the_box(
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main stops when a vehicle lies outside the box."""
    message = gtfs_realtime_pb2.FeedMessage()
    message.ParseFromString(positions_body())
    message.entity[0].vehicle.position.latitude = 53.5
    bodies = live() | {
        'bods_positions_raw.pb': bytes(message.SerializeToString()),
    }
    monkeypatch.setattr(recorder, 'fetched', lambda *, key: bodies)
    assert recorder.main(['--check']) == 1
    assert '1 of 3 vehicles lie outside the box' in capsys.readouterr().err


def test_main_exits_2_without_a_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main exits 2 without a key."""
    monkeypatch.setenv('PAPERROUTE_KEY_FILE_BODS', str(tmp_path / 'missing'))
    assert recorder.main(['--check']) == 2
    assert capsys.readouterr().err == 'record: no key file\n'


def test_cut_zip_adds_the_first_far_route_that_runs_on_the_day(
    tmp_path: Path,
) -> None:
    """Cut zip skips a far route with no trip on the day."""
    source = source_zip(path=tmp_path / 'source.zip')
    out = tmp_path / 'bods.zip'
    assert recorder.cut_zip(
        source=source, out=out, day=DAY, live_trips=(),
    ) == 'X99'
    assert trip_ids(out, route='X99')
    assert not trip_ids(out, route='X98')


def test_cut_zip_leaves_out_a_same_named_route_far_from_the_centre(
    tmp_path: Path,
) -> None:
    """Cut zip keeps a KEPT route only when it has a stop near the centre."""
    source = source_zip(path=tmp_path / 'source.zip')
    out = tmp_path / 'bods.zip'
    recorder.cut_zip(source=source, out=out, day=DAY, live_trips=())
    assert trip_ids(out, route='4')
    assert not trip_ids(out, route='4-far')
    assert not {t for t in all_trip_ids(out) if t.endswith('far')}


def run_main(
    *,
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
    bodies: dict[str, bytes],
) -> tuple[int, Path]:
    """
    Run the recorder offline on the given bodies.

    Parameters
    ----------
    keyed : Path
        The directory holding the key file.
    monkeypatch : pytest.MonkeyPatch
        Environment patcher.
    bodies : dict[str, bytes]
        What `fetched` gives.

    Returns
    -------
    tuple[int, Path]
        The exit status and the output directory.
    """
    source = source_zip(path=keyed / 'source.zip')
    monkeypatch.setattr(recorder, 'fetched', lambda *, key: bodies)
    out = keyed / 'out'
    return recorder.main(['--gtfs', str(source), '--out', str(out)]), out


def test_main_refuses_a_live_vehicle_whose_trip_the_timetable_lacks(
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main refuses a kept-route vehicle whose trip is not in the zip."""
    bodies = live() | {
        'bods_positions_raw.pb': positions_body(
            vehicles=(
                ('c1', '', ''),
                ('b1', '4-2', 'r-4'),
                ('b2', 'ghost', 'r-PR2'),
            ),
        ),
    }
    status, out = run_main(keyed=keyed, monkeypatch=monkeypatch, bodies=bodies)
    assert status == 1
    assert not out.exists()
    assert capsys.readouterr().err == (
        'record: a live vehicle on PR2 has a trip the timetable lacks: '
        'download the timetable again\n'
    )


def test_main_refuses_a_key_in_the_raw_positions_body(
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main writes nothing when the raw positions body holds the key."""
    bodies = live() | {'bods_positions_raw.pb': positions_body(label=KEY)}
    status, out = run_main(keyed=keyed, monkeypatch=monkeypatch, bodies=bodies)
    printed = capsys.readouterr()
    assert status == 1
    assert not out.exists()
    assert (
        'the key is in bods_positions_raw.pb; nothing written' in printed.err
    )
    assert KEY not in printed.out + printed.err


@pytest.mark.parametrize(
    ('vehicles', 'natx', 'message'),
    [
        ((), ('c1',), 'no vehicle in the GTFS-RT answer'),
        (
            (('b1', '4-2', 'r-4'),),
            ('c1',),
            'no coach in the box: record again in the daytime',
        ),
        (
            (('c1', '', ''), ('b1', '4far-2', 'r-4-far')),
            ('c1',),
            'no live vehicle on route 4',
        ),
    ],
)
def test_main_names_what_the_recording_lacks(
    *,
    keyed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    vehicles: tuple[tuple[str, str, str], ...],
    natx: tuple[str, ...],
    message: str,
) -> None:
    """Main exits 1 naming the missing kind of vehicle, writing nothing."""
    bodies = live() | {
        'bods_positions_raw.pb': positions_body(vehicles=vehicles),
        'siri_natx.xml': siri_body(vehicles=natx),
    }
    status, out = run_main(keyed=keyed, monkeypatch=monkeypatch, bodies=bodies)
    assert status == 1
    assert not out.exists()
    assert f'record: {message}\n' in capsys.readouterr().err
