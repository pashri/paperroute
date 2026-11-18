"""Tests for Cambridge's trains, built beside its buses."""

import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from paperroute_cambridge.__main__ import CAMBRIDGE_PARTS
from paperroute_cambridge.build.__main__ import CAMBRIDGE_BUILD
from paperroute_cambridge.build.bods_sources import CAMBRIDGE_SOURCES
from paperroute_cambridge.build.rail_cut import crs_of, cut_rail
from paperroute_cambridge.city import CAMBRIDGE, CAMBRIDGE_REF, CAMBRIDGE_ZONE
from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.pin_options import SqlitePinOptions
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build import swap
from paperroute_transit.build.cli import given_sources, parse_args
from paperroute_transit.build.gtfs_files import Source, read_rows
from paperroute_transit.build.refresh import refresh
from paperroute_transit.build.source_state import (
    SourceError,
    SourceState,
    load_states,
)
from paperroute_transit.build.sources import (
    SourceSpec,
    UrlDownloader,
    file_sha256,
)
from paperroute_transit.build.timetable import Window, build_timetable
from paperroute_transit.core.chooser import Level
from paperroute_transit.core.model import Pin, StopPlace
from tests.build.test_rail_cut import XC_TRIP, changed
from tests.conftest import FIXTURES, RECORDED_DAY

BODS: Final[Path] = FIXTURES / 'bods.zip'
RAIL: Final[Path] = FIXTURES / 'rail.zip'
STATION: Final[StopPlace] = StopPlace(
    stop_id='rail:CBG', name='Cambridge', point=None,
)
SUNDAY: Final[date] = date(2026, 11, 29)


@dataclass
class Shelf:
    """Hand out timetable zips from local files, as the network would."""

    files: dict[str, Path]
    gone: set[str] = field(default_factory=set)

    def head(
        self,
        *,
        spec: SourceSpec,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> SourceState:
        """
        Give a source's validators, its file's digest as the ETag.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        timeout_s : float
            Ignored.
        limiter : SyncStartLimiter | None
            Ignored.

        Returns
        -------
        SourceState
            The validators.

        Raises
        ------
        SourceError
            For a source that is gone.
        """
        del timeout_s, limiter
        if spec.name in self.gone:
            raise SourceError(f'{spec.name}: 404')
        etag = f'"{file_sha256(path=self.files[spec.name])}"'
        return SourceState(etag=etag, last_modified=None, sha256=None)

    def download(
        self,
        *,
        spec: SourceSpec,
        out: Path,
        timeout_s: float,
        limiter: SyncStartLimiter | None,
    ) -> str:
        """
        Copy a source's file.

        Parameters
        ----------
        spec : SourceSpec
            Source.
        out : Path
            File to write.
        timeout_s : float
            Ignored.
        limiter : SyncStartLimiter | None
            Ignored.

        Returns
        -------
        str
            The copy's SHA-256.

        Raises
        ------
        SourceError
            For a source that is gone.
        """
        del timeout_s, limiter
        if spec.name in self.gone:
            raise SourceError(f'{spec.name}: 404')
        shutil.copyfile(self.files[spec.name], out)
        return file_sha256(path=out)


def built(*, out: Path, start: date, days: int) -> Path:
    """
    Build Cambridge's timetable from the bus fixture and the rail cut.

    Parameters
    ----------
    out : Path
        Timetable file.
    start : date
        First day.
    days : int
        Days covered.

    Returns
    -------
    Path
        The timetable file.
    """
    cut = out.with_name('rail-cut.zip')
    cut_rail(archive=RAIL, out=cut)
    bods, rail = CAMBRIDGE_SOURCES
    build_timetable(
        sources=[
            Source(name=bods.name, archive=BODS),
            Source(name=rail.name, archive=cut, optional=rail.optional),
        ],
        window=Window(start=start, days=days),
        out=out,
        city_ref=CAMBRIDGE_REF,
    )
    return out


def departures(*, path: Path, day: date) -> list[tuple[str, ...]]:
    """
    Read a day's departures from Cambridge station.

    Parameters
    ----------
    path : Path
        Timetable file.
    day : date
        Service day.

    Returns
    -------
    list[tuple[str, ...]]
        Time, badge, platform, headsign, source and kind of each.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=CAMBRIDGE_ZONE)
    pin = Pin(origin=STATION, kind='train')
    with closing(SqliteTimetableRepository(city=CAMBRIDGE, path=path)) as repo:
        found = repo.departures(
            pin=pin, start=start, end=start + timedelta(days=1),
        )
    return [
        (
            dep.departs.astimezone(CAMBRIDGE_ZONE).strftime('%H:%M'),
            dep.route, dep.platform, dep.headsign, dep.source,
            str(dep.facts.kind),
        )
        for dep in found
    ]


def trip_counts(*, data_dir: Path) -> dict[str, int]:
    """
    Count the live timetable's trips by source.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    dict[str, int]
        Trips by source name.
    """
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        rows = conn.execute(
            'SELECT source, COUNT(*) FROM trips GROUP BY source',
        ).fetchall()
    return {str(source): int(count) for source, count in rows}


@pytest.fixture(name='rail_built', scope='module')
def rail_built_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the recorded day with trains.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the directory for the file.

    Returns
    -------
    Path
        The timetable file.
    """
    out = tmp_path_factory.mktemp('rail') / 'timetable.db'
    return built(out=out, start=RECORDED_DAY, days=1)


def test_sources_are_the_bus_zip_then_the_optional_rail_cut() -> None:
    """Buses load first; the rail zip is cut and optional."""
    bods, rail = CAMBRIDGE_SOURCES
    assert (bods.name, bods.cut, bods.optional) == ('bods', None, False)
    assert (rail.name, rail.cut, rail.optional) == ('rail', cut_rail, True)
    assert CAMBRIDGE_PARTS.sources == (('bods', 'bus'), ('rail', 'rail'))


def test_each_source_is_asked_for_at_its_published_address() -> None:
    """The bus address is unchanged; rail is the latest release's asset."""
    downloader = CAMBRIDGE_BUILD.downloader
    assert isinstance(downloader, UrlDownloader)
    assert [
        downloader.request(spec=spec, method='HEAD').full_url
        for spec in CAMBRIDGE_BUILD.sources
    ] == [
        'https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/'
        'east_anglia/',
        'https://github.com/planarnetwork/gb-transit/releases/latest/'
        'download/gtfs-national-rail-only.zip',
    ]


def test_trains_leave_cambridge_with_their_badge_and_platform(
    rail_built: Path,
) -> None:
    """Each train shows its operator's badge and timetable platform."""
    assert departures(path=rail_built, day=RECORDED_DAY) == [
        ('07:02', 'GN', '8', 'London Kings Cross', 'train', 'train'),
        ('07:06', 'XC', '4', 'Birmingham New Street', 'train', 'train'),
        ('07:14', 'GA', '3', 'London Liverpool Street', 'train', 'train'),
        ('07:31', 'TL', '7', 'Brighton', 'train', 'train'),
        ('07:40', 'GA', '1', 'Cambridge North', 'train', 'train'),
    ]


def test_a_replacement_bus_is_a_train_with_no_platform(tmp_path: Path) -> None:
    """The Sunday replacement bus is a GN train departure, platform blank."""
    path = built(out=tmp_path / 'timetable.db', start=SUNDAY, days=1)
    assert departures(path=path, day=SUNDAY) == [
        ('06:10', 'GN', '', 'Royston', 'train', 'train'),
    ]


def test_each_rail_station_is_named_by_its_crs(rail_built: Path) -> None:
    """Every rail station's id carries its CRS; platforms hang under it."""
    with closing(sqlite3.connect(rail_built)) as conn:
        stations = conn.execute(
            "SELECT stop_id, name FROM stops WHERE stop_id LIKE 'rail:%'"
            " AND COALESCE(parent_station, '') = ''",
        ).fetchall()
        parents = {row[0] for row in conn.execute(
            "SELECT parent_station FROM stops"
            " WHERE stop_id LIKE 'rail:9100CAMBDGE%'",
        )}
    assert all(crs_of(stop_id=stop_id) for stop_id, _ in stations)
    assert {
        ('rail:CBG', 'Cambridge'),
        ('rail:CMB', 'Cambridge North'),
        ('rail:CMS', 'Cambridge South'),
    } <= set(stations)
    assert parents == {'rail:CBG'}


def test_a_rail_station_is_never_merged_with_a_bus_stop(
    rail_built: Path,
) -> None:
    """Searching Cambridge finds the station alone, trains only."""
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=rail_built),
    ) as network:
        matches = SqlitePinOptions(network=network).search(text='Cambridge')
    first = matches[0]
    assert (first.place.stop_id, first.place.name, first.kinds) == (
        'rail:CBG', 'Cambridge', ('train',),
    )
    assert first.routes == ('GA', 'GN', 'TL', 'XC')
    for match in matches:
        rail = match.place.stop_id.startswith('rail:')
        assert rail == ('train' in match.kinds), match
        assert not rail or match.place.stop_id in {
            'rail:CBG', 'rail:CMB', 'rail:CMS',
        }
    with closing(sqlite3.connect(rail_built)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM stops WHERE parent_station = 'rail:CBG'"
            " AND stop_id NOT LIKE 'rail:%'",
        ).fetchone() == (0,)
        assert conn.execute(
            'SELECT COUNT(*) FROM line_aliases',
        ).fetchone() == (0,)


def test_the_train_chooser_lists_one_line_per_operator(
    rail_built: Path,
) -> None:
    """Train lists GA, GN, TL and XC, each by its stations, then To."""
    line = ((Level.KIND, 'train'), (Level.LINE, 'train:GN'))
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=rail_built),
    ) as network:
        kinds = network.kind_column()
        lines = network.next_column(path=((Level.KIND, 'train'),))
        stations = network.next_column(path=line)
        towards = network.next_column(
            path=(*line, (Level.STATION, 'rail:CBG')),
        )
    assert [choice.label for choice in kinds.items] == [
        'Bus', 'Coach', 'Train',
    ]
    assert lines is not None and stations is not None and towards is not None
    assert [(c.key, c.badge, c.label) for c in lines.items] == [
        ('train:GA', 'GA', 'Greater Anglia'),
        ('train:GN', 'GN', 'Great Northern'),
        ('train:TL', 'TL', 'Thameslink'),
        ('train:XC', 'XC', 'CrossCountry'),
    ]
    assert 'rail:CBG' in [choice.key for choice in stations.items]
    assert 'rail:KGX' in [choice.key for choice in towards.items]


def test_the_rail_end_does_not_shorten_the_bus_timetable(
    tmp_path: Path,
) -> None:
    """The rail calendar ends 11 Dec; the timetable still runs to its end."""
    path = built(out=tmp_path / 'timetable.db', start=RECORDED_DAY, days=30)
    with closing(SqliteTimetableRepository(city=CAMBRIDGE, path=path)) as repo:
        assert repo.last_day() == date(2026, 12, 15)
        assert repo.source_dates() == {
            'bods': date(2026, 11, 16), 'rail': date(2026, 11, 16),
        }


def test_buses_keep_their_timetable_whatever_the_rail_source_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No rail builds buses alone; rail adds trains; rail gone keeps them."""
    monkeypatch.setattr(swap, 'MIN_TRIPS', 1)
    monkeypatch.setattr(
        refresh_module, 'yesterday', lambda *, zone: RECORDED_DAY,
    )
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    parts = replace(CAMBRIDGE_BUILD, downloader=shelf)
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    reports, counts, stored = [], [], []
    for gone in ({'rail'}, set(), {'rail'}):
        shelf.gone = gone
        reports.append(refresh(
            data_dir=data_dir, city=CAMBRIDGE, parts=parts, days=1, force=True,
        ))
        counts.append(trip_counts(data_dir=data_dir))
        stored.append(load_states(path=data_dir / 'sources' / 'sources.json'))
    assert [(r.failed, r.all_failed, r.rebuilt) for r in reports] == [
        (('rail',), False, True), ((), False, True), (('rail',), False, True),
    ]
    assert reports[1].changed == ('rail',)
    assert 'rail' not in stored[0] and stored[2]['rail'] == stored[1]['rail']
    assert counts == [
        {'bods': 67}, {'bods': 67, 'rail': 5}, {'bods': 67, 'rail': 5},
    ]
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        ends = conn.execute(
            "SELECT key FROM meta WHERE key LIKE 'source_end:%'",
        ).fetchall()
    assert ends == [('source_end:bods',)]


def test_the_build_command_cuts_the_rail_zip_it_is_given(
    tmp_path: Path,
) -> None:
    """The gtfs command cuts the national zip beside its output."""
    out = tmp_path / 'timetable.db'
    args = parse_args(city=CAMBRIDGE, parts=CAMBRIDGE_BUILD, argv=[
        'gtfs', '--bods', str(BODS), '--rail', str(RAIL), '--out', str(out),
    ])
    sources = given_sources(args=args, parts=CAMBRIDGE_BUILD)
    assert sources == [
        Source(name='bods', archive=BODS),
        Source(
            name='rail',
            archive=tmp_path / 'timetable.rail.zip',
            optional=True,
        ),
    ]
    stops = read_rows(archive=sources[1].archive, member='stops.txt')
    assert 'rail:CBG' in {row['stop_id'] for row in stops}


def bad_route_type(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Give every route a route_type the loader cannot read.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    for row in tables['routes.txt']:
        row['route_type'] = 'rail'


def blank_time(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Blank the times of one kept call.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    call = next(
        row for row in tables['stop_times.txt'] if row['trip_id'] == XC_TRIP
    )
    call['arrival_time'] = ''
    call['departure_time'] = ''


def no_trips_file(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Remove trips.txt, as a release missing a table does.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    del tables['trips.txt']


@pytest.fixture(name='data_dir')
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    An empty data directory, a fixed window start and a low trip floor.

    Parameters
    ----------
    tmp_path : Path
        Test directory.
    monkeypatch : pytest.MonkeyPatch
        Patcher.

    Returns
    -------
    Path
        The data directory.
    """
    monkeypatch.setattr(swap, 'MIN_TRIPS', 1)
    monkeypatch.setattr(
        refresh_module, 'yesterday', lambda *, zone: RECORDED_DAY,
    )
    path = tmp_path / 'data'
    path.mkdir()
    return path


def night(*, data_dir: Path, shelf: Shelf) -> swap.RefreshReport:
    """
    Refresh Cambridge's one-day timetable from the shelf.

    Parameters
    ----------
    data_dir : Path
        Data directory.
    shelf : Shelf
        Where the sources come from.

    Returns
    -------
    swap.RefreshReport
        The refresh's report.
    """
    return refresh(
        data_dir=data_dir,
        city=CAMBRIDGE,
        parts=replace(CAMBRIDGE_BUILD, downloader=shelf),
        days=1,
        force=True,
    )


def rail_date(*, data_dir: Path) -> date | None:
    """
    Read the date of the rail source in the live timetable.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    date | None
        The rail source's date, or None when the timetable has none.
    """
    path = data_dir / 'timetable.db'
    with closing(SqliteTimetableRepository(city=CAMBRIDGE, path=path)) as repo:
        return repo.source_dates().get('rail')


FAULTS: Final[dict[str, Callable[[dict[str, list[dict[str, str]]]], None]]] = {
    'route_type': bad_route_type,
    'blank_time': blank_time,
    'no_trips_file': no_trips_file,
}


@pytest.mark.parametrize('fault', list(FAULTS))
def test_a_rail_file_the_loader_rejects_leaves_the_buses_built(
    data_dir: Path, tmp_path: Path, fault: str,
) -> None:
    """On a first night, buses build alone and the exit is a success."""
    bad = changed(out=tmp_path / 'bad.zip', change=FAULTS[fault])
    shelf = Shelf(files={'bods': BODS, 'rail': bad})
    report = night(data_dir=data_dir, shelf=shelf)
    assert (report.failed, report.all_failed, report.rebuilt) == (
        ('rail',), False, True,
    )
    assert trip_counts(data_dir=data_dir) == {'bods': 67}
    states = load_states(path=data_dir / 'sources' / 'sources.json')
    assert 'rail' not in states
    assert swap.printed(report=report) == 0


@pytest.mark.parametrize('fault', list(FAULTS))
def test_a_rail_file_the_loader_rejects_keeps_the_earlier_trains(
    data_dir: Path, tmp_path: Path, fault: str,
) -> None:
    """After a good night, the earlier trains stay, night after night."""
    bad = changed(out=tmp_path / 'bad.zip', change=FAULTS[fault])
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    good = departures(path=data_dir / 'timetable.db', day=RECORDED_DAY)
    before = load_states(path=data_dir / 'sources' / 'sources.json')
    shelf.files['rail'] = bad
    for _ in range(2):
        report = night(data_dir=data_dir, shelf=shelf)
        assert (report.failed, report.all_failed) == (('rail',), False)
        assert trip_counts(data_dir=data_dir) == {'bods': 67, 'rail': 5}
        assert departures(
            path=data_dir / 'timetable.db', day=RECORDED_DAY,
        ) == good
        assert rail_date(data_dir=data_dir) == date(2026, 11, 16)
        after = load_states(path=data_dir / 'sources' / 'sources.json')
        assert after == before


def test_a_bus_source_that_fails_still_fails_the_refresh(
    data_dir: Path,
) -> None:
    """With the buses unreachable and the rail fine, every required fails."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    shelf.gone = {'bods'}
    report = night(data_dir=data_dir, shelf=shelf)
    assert report.failed == ('bods',)
    assert report.all_failed
    assert swap.printed(report=report) == 1
