"""Tests for Cambridge's trains, built beside its buses."""

import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Callable, Collection, Iterator
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Final, NoReturn

import pytest

from paperroute_cambridge.__main__ import CAMBRIDGE_PARTS
from paperroute_cambridge.build.__main__ import CAMBRIDGE_BUILD
from paperroute_cambridge.build.bods_sources import CAMBRIDGE_SOURCES
from paperroute_cambridge.build.rail_cut import KEPT_CRS, crs_of, cut_rail
from paperroute_cambridge.city import (
    CAMBRIDGE,
    CAMBRIDGE_REF,
    CAMBRIDGE_ZONE,
    RAIL_SOURCE,
)
from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.pin_options import SqlitePinOptions
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build import extras
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build import swap
from paperroute_transit.build.cli import given_sources, parse_args
from paperroute_transit.build.gtfs_files import Source, read_rows
from paperroute_transit.build.handoff import Handoff, read_handoff
from paperroute_transit.build.refresh import refresh
from paperroute_transit.build.runs import load_runs
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
from paperroute_transit.core.city import MergeReport
from paperroute_transit.core.model import Pin, StopPlace
from paperroute_transit.core.modes import Classifier
from tests.build.test_rail_cut import (
    XC_TRIP,
    changed,
    with_east_midlands,
    with_norwich,
)
from tests.conftest import FIXTURES, RECORDED_DAY

BODS: Final[Path] = FIXTURES / 'bods.zip'
RAIL: Final[Path] = FIXTURES / 'rail.zip'
STATION: Final[StopPlace] = StopPlace(
    stop_id='rail:CBG', name='Cambridge', point=None,
)
ELY: Final[StopPlace] = StopPlace(stop_id='rail:ELY', name='Ely', point=None)
BIRMINGHAM: Final[StopPlace] = StopPlace(
    stop_id='rail:BHM', name='Birmingham New Street', point=None,
)
HOSPITAL: Final[StopPlace] = StopPlace(
    stop_id='0500CCITY517', name='Hospital Outpatients', point=None,
)
CITY_CENTRE: Final[StopPlace] = StopPlace(
    stop_id='0500CCITY001', name='City centre', point=None,
)
XC_BUILT: Final[str] = f'rail:{XC_TRIP}'
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


def built(
    *, out: Path, start: date, days: int, rail: Path = RAIL,
) -> Path:
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
    rail : Path
        National rail timetable to cut.

    Returns
    -------
    Path
        The timetable file.
    """
    cut = out.with_name('rail-cut.zip')
    cut_rail(archive=rail, out=cut)
    bods, trains = CAMBRIDGE_SOURCES
    build_timetable(
        sources=[
            Source(name=bods.name, archive=BODS),
            Source(name=trains.name, archive=cut, optional=trains.optional),
        ],
        window=Window(start=start, days=days),
        out=out,
        city_ref=CAMBRIDGE_REF,
    )
    return out


def departures(
    *, path: Path, day: date, place: StopPlace = STATION,
) -> list[tuple[str, ...]]:
    """
    Read a day's departures from a station.

    Parameters
    ----------
    path : Path
        Timetable file.
    day : date
        Service day.
    place : StopPlace
        Station to read.

    Returns
    -------
    list[tuple[str, ...]]
        Time, badge, platform, headsign, source and kind of each.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=CAMBRIDGE_ZONE)
    pin = Pin(origin=place, kind='train')
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


@pytest.fixture(name='week_built', scope='module')
def week_built_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build a week of the recorded day with trains.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the directory for the file.

    Returns
    -------
    Path
        The timetable file.
    """
    out = tmp_path_factory.mktemp('week') / 'timetable.db'
    return built(out=out, start=RECORDED_DAY, days=7)


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


def test_the_merge_logs_the_routes_left_after_the_line_split(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The routes kept are the routes the finished file holds."""
    rail = changed(out=tmp_path / 'norwich.zip', change=with_norwich)
    with caplog.at_level(logging.INFO):
        path = built(
            out=tmp_path / 'timetable.db',
            start=RECORDED_DAY,
            days=1,
            rail=rail,
        )
    with closing(sqlite3.connect(path)) as conn:
        (count,) = conn.execute('SELECT COUNT(*) FROM routes').fetchone()
    assert f'kept {count} routes' in caplog.text


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


@pytest.fixture(name='norwich_built', scope='module')
def norwich_built_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the recorded day with trains, adding a train to Norwich.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the directory for the files.

    Returns
    -------
    Path
        The timetable file.
    """
    out = tmp_path_factory.mktemp('norwich')
    rail = changed(out=out / 'norwich.zip', change=with_norwich)
    return built(
        out=out / 'timetable.db', start=RECORDED_DAY, days=1, rail=rail,
    )


def test_the_train_chooser_lists_lines_by_where_they_go(
    norwich_built: Path,
) -> None:
    """Train lists drawing B's lines, ruled off between operators."""
    line = ((Level.KIND, 'train'), (Level.LINE, 'train:GA:Norwich'))
    gn = (
        (Level.KIND, 'train'),
        (Level.LINE, 'train:GN:Kings Lynn & Kings Cross'),
    )
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=norwich_built),
    ) as network:
        kinds = network.kind_column()
        lines = network.line_column(kind='train')
        stations = network.next_column(path=line)
        towards = network.next_column(
            path=(*gn, (Level.STATION, 'rail:CBG')),
        )
    assert [choice.label for choice in kinds.items] == [
        'Bus', 'Coach', 'Train',
    ]
    assert lines is not None and stations is not None and towards is not None
    assert 'rail:KGX' in [choice.key for choice in towards.items]
    assert lines.items[3].value == 'GA:Norwich'
    assert [(c.key, c.badge, c.label) for c in lines.items] == [
        ('train:GN:Kings Lynn & Kings Cross', 'GN',
         'Kings Lynn & Kings Cross'),
        ('train:TL:Brighton via St Pancras', 'TL', 'Brighton via St Pancras'),
        ('train:GA:Liverpool Street', 'GA', 'Liverpool Street'),
        ('train:GA:Norwich', 'GA', 'Norwich'),
        ('train:XC:Birmingham & Stansted', 'XC', 'Birmingham & Stansted'),
        ('train:EM:Norwich & Liverpool', 'EM', 'Norwich & Liverpool'),
    ]
    assert [c.rule_above for c in lines.items] == [
        False, True, True, False, True, True,
    ]
    assert {'rail:CBG', 'rail:ELY', 'rail:NRW'} <= {
        c.key for c in stations.items
    }


def test_each_train_line_has_its_own_stations(norwich_built: Path) -> None:
    """Every train line lists stations; Liverpool Street is not Norwich."""
    with closing(sqlite3.connect(norwich_built)) as conn:
        listed = {row[0] for row in conn.execute(
            "SELECT DISTINCT line_key FROM line_stations"
            " WHERE line_key LIKE 'train:%'",
        )}
        kept = {row[0] for row in conn.execute(
            "SELECT route_key FROM chooser_routes WHERE kind = 'train'",
        )}
        on = {
            (row[0], row[1]) for row in conn.execute(
                "SELECT line_key, station_id FROM line_stations"
                " WHERE station_id = 'rail:LST'",
            )
        }
    assert len(kept) == 6
    assert listed == kept
    assert ('train:GA:Liverpool Street', 'rail:LST') in on
    assert ('train:GA:Norwich', 'rail:LST') not in on


def test_the_replacement_bus_joins_its_operators_line(
    tmp_path: Path,
) -> None:
    """The Sunday replacement bus sits on Great Northern's first line."""
    path = built(out=tmp_path / 'timetable.db', start=SUNDAY, days=1)
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=path),
    ) as network:
        lines = network.line_column(kind='train')
    assert lines is not None
    assert [(c.key, c.badge) for c in lines.items] == [
        ('train:GN:Kings Lynn & Kings Cross', 'GN'),
    ]
    assert departures(path=path, day=SUNDAY) == [
        ('06:10', 'GN', '', 'Royston', 'train', 'train'),
    ]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute(
            "SELECT type FROM routes WHERE route_id LIKE 'rail:GN_RRB:%'",
        ).fetchall() == [(714,)]


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


def night(
    *, data_dir: Path, shelf: Shelf, hand_over: bool = False,
) -> swap.RefreshReport:
    """
    Refresh Cambridge's one-day timetable from the shelf.

    Parameters
    ----------
    data_dir : Path
        Data directory.
    shelf : Shelf
        Where the sources come from.
    hand_over : bool
        Exec into the extras program after the load.

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
        hand_over=hand_over,
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


def test_the_trip_floor_counts_the_buses_alone(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """67 bus trips stay under a floor of 68, whatever the trains add."""
    monkeypatch.setattr(swap, 'MIN_TRIPS', 68)
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    with pytest.raises(swap.TimetableCheckError, match='67 trips, want 68'):
        night(data_dir=data_dir, shelf=shelf)
    assert not (data_dir / 'timetable.db').exists()


def nowhere(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Send the XC trip to Nowhere, so a build can tell this copy apart.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    for row in tables['trips.txt']:
        if row['trip_id'] == XC_TRIP:
            row['trip_headsign'] = 'Nowhere'


@dataclass
class Refusing:
    """Stand in for build_timetable, refusing builds that load a source."""

    names: frozenset[str]  # a build loading one fails; empty fails all
    calls: int = 0

    def __call__(
        self, *, sources: list[Source], window: Window, out: Path,
        city_ref: str, handoff: Handoff | None = None,
    ) -> MergeReport:
        """
        Count the build, then refuse it or run the real one.

        Parameters
        ----------
        sources : list[Source]
            Feeds to load.
        window : Window
            Dates to cover.
        out : Path
            Timetable file.
        city_ref : str
            The city's reference.
        handoff : Handoff | None
            Passed on.

        Returns
        -------
        MergeReport
            The real build's report.

        Raises
        ------
        ValueError
            When the build is refused.
        """
        self.calls += 1
        if not self.names or self.names & {s.name for s in sources}:
            raise ValueError('the build refused a source')
        return build_timetable(
            sources=sources, window=window, out=out, city_ref=city_ref,
            handoff=handoff,
        )


def test_a_stored_rail_copy_the_build_rejects_leaves_the_buses_built(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no new rail copy to drop, the buses are built alone."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    refusing = Refusing(names=frozenset({'rail'}))
    monkeypatch.setattr(refresh_module, 'build_timetable', refusing)
    report = night(data_dir=data_dir, shelf=shelf)
    assert (report.failed, report.all_failed, report.rebuilt) == (
        ('rail',), False, True,
    )
    assert trip_counts(data_dir=data_dir) == {'bods': 67}
    assert rail_date(data_dir=data_dir) is None
    states = load_states(path=data_dir / 'sources' / 'sources.json')
    assert 'rail' not in states
    assert refusing.calls == 2
    assert swap.printed(report=report) == 0


def test_a_build_that_fails_without_the_trains_still_fails(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New copy, stored copy, no trains: three builds, then the error."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    before = trip_counts(data_dir=data_dir)
    shelf.files['rail'] = changed(out=tmp_path / 'new.zip', change=nowhere)
    refusing = Refusing(names=frozenset())
    monkeypatch.setattr(refresh_module, 'build_timetable', refusing)
    with pytest.raises(ValueError, match='refused'):
        night(data_dir=data_dir, shelf=shelf)
    assert refusing.calls == 3
    assert trip_counts(data_dir=data_dir) == before


@pytest.fixture(name='restored_logging')
def _restored_logging() -> Iterator[None]:
    """
    Put the root logger back after a test that configures it.

    Yields
    ------
    None
        Control to the test.
    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


NEW_COPY: Final[str] = "SELECT 1 FROM trips WHERE headsign = 'Nowhere'"
ANY_RAIL: Final[str] = "SELECT 1 FROM trips WHERE source = 'rail'"
EVERY_BUILD: Final[str] = 'SELECT 1'


@dataclass
class FaultyRuns:
    """Stand in for load_runs, failing while a query finds a row."""

    sql: str
    calls: int = 0

    def __call__(
        self, *, conn: sqlite3.Connection, classify: Classifier,
        rail_types: Collection[int],
    ) -> object:
        """
        Count the call, then fail or build the run tables.

        Parameters
        ----------
        conn : sqlite3.Connection
            Timetable being finished.
        classify : Classifier
            The city's classifier.
        rail_types : Collection[int]
            The city's rail route types.

        Returns
        -------
        object
            The real report.

        Raises
        ------
        ValueError
            When the query finds a row.
        """
        self.calls += 1
        if conn.execute(self.sql).fetchone():
            raise ValueError('the run tables refused a trip')
        return load_runs(conn=conn, classify=classify, rail_types=rail_types)


def run_here(path: str, argv: list[str]) -> NoReturn:
    """
    Run the program an exec would start in this process, then exit.

    The lock descriptor is put back as the exec would have kept it.

    Parameters
    ----------
    path : str
        Interpreter, unused.
    argv : list[str]
        Interpreter, '-m', module, then its arguments; the last is the
        handoff file.

    Raises
    ------
    SystemExit
        With the program's exit status.
    """
    del path
    tail = read_handoff(path=Path(argv[-1])).tail
    assert tail is not None
    spare = os.dup(tail.lock_fd)
    try:
        code = import_module(argv[2]).main(argv=argv[3:])
    finally:
        os.dup2(spare, tail.lock_fd)
        os.close(spare)
    raise SystemExit(code)


@pytest.mark.usefixtures('restored_logging')
@pytest.mark.parametrize(
    ('good_first', 'new_copy', 'fault', 'expected'),
    [
        (False, True, NEW_COPY, (0, {'bods': 67}, 2, [['rail']])),
        (True, True, NEW_COPY,
         (0, {'bods': 67, 'rail': 5}, 2, [['rail']])),
        (True, False, ANY_RAIL, (0, {'bods': 67}, 2, [['rail']])),
        (True, True, EVERY_BUILD, (1, {'bods': 67, 'rail': 5}, 3, [])),
    ],
    ids=['first-night', 'new-copy', 'stored-copy', 'every-build'],
)
def test_a_handed_over_build_falls_back_when_the_run_tables_fail(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], good_first: bool, new_copy: bool,
    fault: str, expected: tuple[int, dict[str, int], int, list[list[str]]],
) -> None:
    """After the exec, a failure tries again without trusting the rail."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    if good_first:
        night(data_dir=data_dir, shelf=shelf)
    if new_copy:
        shelf.files['rail'] = changed(
            out=tmp_path / 'new.zip', change=nowhere,
        )
    runs = FaultyRuns(sql=fault)
    monkeypatch.setattr(extras, 'load_runs', runs)
    monkeypatch.setattr(os, 'execv', run_here)
    capsys.readouterr()
    with pytest.raises(SystemExit) as stop:
        night(data_dir=data_dir, shelf=shelf, hand_over=True)
    reports = [
        json.loads(line)['failed']
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('{"downloaded"')
    ]
    assert (
        stop.value.code, trip_counts(data_dir=data_dir), runs.calls,
        reports,
    ) == expected


def test_each_source_declares_a_size_limit() -> None:
    """The bus zip may reach 250 MB and the national rail zip 150 MB."""
    assert [spec.size_limit for spec in CAMBRIDGE_SOURCES] == [
        250_000_000, 150_000_000,
    ]


def no_coordinates(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Give every stop 'nan' for its latitude and longitude.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    for row in tables['stops.txt']:
        row['stop_lat'] = row['stop_lon'] = 'nan'


def test_a_rail_file_without_coordinates_keeps_the_earlier_trains(
    data_dir: Path, tmp_path: Path,
) -> None:
    """Trains whose stations have no place are refused, not lost."""
    shelf = Shelf(files={'bods': BODS, 'rail': RAIL})
    night(data_dir=data_dir, shelf=shelf)
    shelf.files['rail'] = changed(
        out=tmp_path / 'bad.zip', change=no_coordinates,
    )
    report = night(data_dir=data_dir, shelf=shelf)
    assert (report.failed, report.all_failed) == (('rail',), False)
    assert trip_counts(data_dir=data_dir) == {'bods': 67, 'rail': 5}
    assert rail_date(data_dir=data_dir) == date(2026, 11, 16)


def test_an_east_midlands_train_leaves_ely(tmp_path: Path) -> None:
    """A train calling at Ely and not Cambridge is built, badged EM."""
    rail = changed(out=tmp_path / 'em.zip', change=with_east_midlands)
    path = built(
        out=tmp_path / 'timetable.db', start=RECORDED_DAY, days=1, rail=rail,
    )
    assert ('07:45', 'EM', '', 'Peterborough', 'train', 'train') in (
        departures(path=path, day=RECORDED_DAY, place=ELY)
    )


def test_rail_cut_rule_is_the_sorted_kept_stations() -> None:
    """The rail source's rule is derived from the kept stations."""
    rail = next(s for s in CAMBRIDGE_SOURCES if s.name == RAIL_SOURCE)
    assert rail.cut_rule == ','.join(sorted(KEPT_CRS))


def test_trains_reach_every_later_station(rail_built: Path) -> None:
    """Only train runs count when the kind is train."""
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=rail_built),
    ) as network:
        options = SqlitePinOptions(network=network)
        trains = options.reached_from(place=STATION, kind='train')
        buses = options.reached_from(place=STATION, kind='bus')
    assert [place.name for place in trains] == [
        'Birmingham New Street', 'Brighton', 'Cambridge North',
        'Cambridge South', 'Ely', 'London Kings Cross',
        'London Liverpool Street', 'London St Pancras International',
        'Royston',
    ]
    assert buses == ()
