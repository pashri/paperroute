"""Tests for the cut of the national rail timetable."""

import logging
import zipfile
from collections import Counter
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Final

import pytest

from paperroute_cambridge.build.rail_cut import (
    BADGES,
    CAMBRIDGE_CRS,
    ZIP_TIME,
    CutReport,
    crs_of,
    cut_rail,
    station_id,
)
from paperroute_transit.build.gtfs_files import read_rows
from paperroute_transit.build.gtfs_slice import write_tables
from tests.conftest import FIXTURES

RAIL: Final[Path] = FIXTURES / 'rail.zip'
HEATH: Final[str] = 'A10001_20261102_20261211'
XC_TRIP: Final[str] = 'B20001_20261102_20261211'
KEPT: Final[list[str]] = [
    'rail:B20001_20261102_20261211',
    'rail:C30001_20261102_20261211',
    'rail:C30002_20261102_20261211',
    'rail:D40001_20261102_20261211',
    'rail:E50001_20261102_20261211',
    'rail:X90001_20261129_20261129',
]


def table(*, archive: Path, member: str) -> list[dict[str, str]]:
    """
    Read one member of a zip as rows.

    Parameters
    ----------
    archive : Path
        The zip.
    member : str
        The member's name.

    Returns
    -------
    list[dict[str, str]]
        Its rows, in order.
    """
    return list(read_rows(archive=archive, member=member))


def changed(
    *, out: Path, change: Callable[[dict[str, list[dict[str, str]]]], None],
) -> Path:
    """
    Write a copy of the fixture with its tables changed.

    Parameters
    ----------
    out : Path
        The zip to write.
    change : Callable[[dict[str, list[dict[str, str]]]], None]
        Changes the rows of each member, in place.

    Returns
    -------
    Path
        The zip written.
    """
    with zipfile.ZipFile(RAIL) as feed:
        names = feed.namelist()
    tables = {name: table(archive=RAIL, member=name) for name in names}
    change(tables)
    write_tables(out=out, tables=tables)
    return out


def doubled(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Repeat one kept trip's row and one platform's row under new names.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    trip = next(r for r in tables['trips.txt'] if r['trip_id'] == XC_TRIP)
    stop = next(
        r for r in tables['stops.txt'] if r['stop_id'] == '9100CAMBDGE4'
    )
    tables['trips.txt'].append({**trip, 'trip_headsign': 'Copy'})
    tables['stops.txt'].append({**stop, 'stop_name': 'Copy'})


def split(tables: dict[str, list[dict[str, str]]]) -> None:
    """
    Move the second half of one trip's calls to the end of the file.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    """
    times = tables['stop_times.txt']
    calls = [row for row in times if row['trip_id'] == XC_TRIP]
    tail = calls[len(calls) // 2:]
    tables['stop_times.txt'] = [r for r in times if r not in tail] + tail


@pytest.fixture(name='cut', scope='module')
def cut_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Cut the fixture once for the module.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the directory for the cut.

    Returns
    -------
    Path
        The cut zip.
    """
    out = tmp_path_factory.mktemp('cut') / 'rail.zip'
    cut_rail(archive=RAIL, out=out)
    return out


def test_the_fixture_is_in_the_national_format() -> None:
    """The fixture has the national file's members, agencies and a decoy."""
    with zipfile.ZipFile(RAIL) as feed:
        members = feed.namelist()
    trips = {row['trip_id'] for row in table(archive=RAIL, member='trips.txt')}
    agencies = {
        row['agency_id'] for row in table(archive=RAIL, member='agency.txt')
    }
    names = {
        row['stop_name'] for row in table(archive=RAIL, member='stops.txt')
    }
    assert len(trips) == 7 and HEATH in trips
    assert agencies == {'=GN', '=LE', '=LO', '=TL', '=XC'}
    assert 'Cambridge Heath (London)' in names
    assert len(members) == 13 and 'transfers.txt' in members
    assert RAIL.stat().st_size < 8_192


def test_cut_keeps_every_trip_calling_at_a_cambridge_station(
    cut: Path,
) -> None:
    """Cut keeps every trip calling at a Cambridge station, and no other."""
    trips = [row['trip_id'] for row in table(archive=cut, member='trips.txt')]
    assert trips == KEPT
    assert CAMBRIDGE_CRS == frozenset({'CBG', 'CMB', 'CMS'})


def test_cut_keeps_each_trip_whole(cut: Path) -> None:
    """Every call of a kept trip is kept, from its first stop to its last."""
    given = Counter(
        row['trip_id'] for row in table(archive=RAIL, member='stop_times.txt')
    )
    kept = Counter(
        row['trip_id'].removeprefix('rail:')
        for row in table(archive=cut, member='stop_times.txt')
    )
    assert kept == {trip: n for trip, n in given.items() if trip != HEATH}
    assert sum(kept.values()) == 24


def test_cut_drops_cambridge_heath(cut: Path) -> None:
    """Cambridge Heath is not a Cambridge station, so its trip goes."""
    names = {
        row['stop_name'] for row in table(archive=cut, member='stops.txt')
    }
    assert 'Cambridge Heath (London)' not in names
    assert {'Cambridge', 'Cambridge North', 'Cambridge South'} <= names


def test_cut_names_each_station_by_its_crs(cut: Path) -> None:
    """Stations become rail:<CRS>; platforms keep their id under rail:."""
    stops = {
        row['stop_id']: row for row in table(archive=cut, member='stops.txt')
    }
    assert [
        (stops[key]['stop_name'], stops[key]['location_type'])
        for key in ('rail:CBG', 'rail:CMB', 'rail:CMS')
    ] == [
        ('Cambridge', '1'),
        ('Cambridge North', '1'),
        ('Cambridge South', '1'),
    ]
    platform = stops['rail:9100CAMBDGE7']
    assert (platform['parent_station'], platform['platform_code']) == (
        'rail:CBG', '7',
    )
    generic = stops['rail:9100CAMBDGE']
    assert (generic['parent_station'], generic['platform_code']) == (
        'rail:CBG', '',
    )
    assert all(key.startswith('rail:') for key in stops)
    assert len(stops) == 31


def test_station_ids_and_crs_codes_map_both_ways() -> None:
    """A CRS code gives its station's id, and only a station gives a CRS."""
    assert station_id(crs='CBG') == 'rail:CBG'
    assert crs_of(stop_id='rail:CBG') == 'CBG'
    assert crs_of(stop_id='rail:9100CAMBDGE7') is None
    assert crs_of(stop_id='0500CCITY227') is None


def test_cut_writes_the_operator_badge_as_the_short_name(cut: Path) -> None:
    """Each route's short name is its operator's code, with LE as GA."""
    routes = [
        (r['route_id'], r['agency_id'], r['route_short_name'], r['route_type'])
        for r in table(archive=cut, member='routes.txt')
    ]
    assert routes == [
        ('GN', 'GN', 'GN', '2'),
        ('GN_RRB', 'GN', 'GN', '714'),
        ('LE', 'LE', 'GA', '2'),
        ('TL', 'TL', 'TL', '2'),
        ('XC', 'XC', 'XC', '2'),
    ]
    agencies = [
        r['agency_id'] for r in table(archive=cut, member='agency.txt')
    ]
    assert agencies == ['GN', 'LE', 'TL', 'XC']
    assert BADGES == {'LE': 'GA'}


def test_cut_keeps_the_rows_its_trips_need(cut: Path) -> None:
    """Calendars, shapes and the feed's own files come with the trips."""
    services = [
        row['service_id'] for row in table(archive=cut, member='calendar.txt')
    ]
    removed = table(archive=cut, member='calendar_dates.txt')
    shapes = {
        row['shape_id'] for row in table(archive=cut, member='shapes.txt')
    }
    info = table(archive=cut, member='feed_info.txt')
    assert services == ['101', '103']
    assert [(r['service_id'], r['date']) for r in removed] == [
        ('101', '20261127'),
    ]
    assert len(shapes) == 6 and 'a1b2c3d4e5f6' not in shapes
    assert info[0]['feed_start_date'] == '20261116'
    assert len(table(archive=cut, member='attributions.txt')) == 3


def test_cut_reports_what_it_kept(tmp_path: Path) -> None:
    """The report counts what the cut wrote."""
    assert cut_rail(archive=RAIL, out=tmp_path / 'cut.zip') == CutReport(
        trips=6, stops=31, routes=5, services=2, shapes=6, duplicates=0,
    )


def test_cut_is_the_same_bytes_every_time(tmp_path: Path) -> None:
    """Two cuts are the same bytes, in the same members, dated alike."""
    first, second = tmp_path / 'first.zip', tmp_path / 'second.zip'
    cut_rail(archive=RAIL, out=first)
    cut_rail(archive=RAIL, out=second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as feed:
        assert {info.date_time for info in feed.infolist()} == {ZIP_TIME}
        assert feed.namelist() == [
            'stop_times.txt', 'trips.txt', 'routes.txt', 'agency.txt',
            'calendar.txt', 'calendar_dates.txt', 'shapes.txt', 'stops.txt',
            'feed_info.txt', 'attributions.txt',
        ]


def test_a_duplicate_id_is_reported_and_the_first_kept(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A repeated trip or stop id is counted and logged; the first stays."""
    archive = changed(out=tmp_path / 'doubled.zip', change=doubled)
    with caplog.at_level(logging.WARNING):
        report = cut_rail(archive=archive, out=tmp_path / 'cut.zip')
    trips = table(archive=tmp_path / 'cut.zip', member='trips.txt')
    stops = table(archive=tmp_path / 'cut.zip', member='stops.txt')
    assert report.duplicates == 2
    assert 'rail: 2 duplicate ids, the first of each kept' in caplog.text
    assert [
        r['trip_headsign'] for r in trips if r['trip_id'] == f'rail:{XC_TRIP}'
    ] == ['Birmingham New Street']
    assert [
        r['stop_name'] for r in stops if r['stop_id'] == 'rail:9100CAMBDGE4'
    ] == ['Cambridge Platform 4']


def test_a_trip_split_across_the_file_is_refused(tmp_path: Path) -> None:
    """Calls of one trip in two places stop the cut."""
    archive = changed(out=tmp_path / 'split.zip', change=split)
    with pytest.raises(
        ValueError, match='stop_times.txt is not in trip order',
    ):
        cut_rail(archive=archive, out=tmp_path / 'cut.zip')


def test_a_cut_with_no_trips_is_refused(tmp_path: Path) -> None:
    """A file with no trip calling at the stations stops the cut."""
    with pytest.raises(ValueError, match='no trip calls at the stations'):
        cut_rail(archive=RAIL, out=tmp_path / 'cut.zip', crs=frozenset())


def without(*, member: str) -> Callable[
    [dict[str, list[dict[str, str]]]], None
]:
    """
    Make a change that removes tables.

    Parameters
    ----------
    member : str
        The member to remove; ``calendar`` removes both calendar tables.

    Returns
    -------
    Callable[[dict[str, list[dict[str, str]]]], None]
        The change.
    """
    names = (
        ['calendar.txt', 'calendar_dates.txt']
        if member == 'calendar'
        else [member]
    )

    def change(tables: dict[str, list[dict[str, str]]]) -> None:
        """
        Remove the tables.

        Parameters
        ----------
        tables : dict[str, list[dict[str, str]]]
            Rows by member, changed in place.
        """
        for name in names:
            del tables[name]

    return change


@pytest.mark.parametrize(
    'member',
    ['stops.txt', 'stop_times.txt', 'trips.txt', 'routes.txt', 'calendar'],
)
def test_a_file_missing_a_needed_table_is_refused(
    tmp_path: Path, member: str,
) -> None:
    """A release without a table the cut needs is refused, no cut left."""
    archive = changed(
        out=tmp_path / 'short.zip', change=without(member=member),
    )
    with pytest.raises(ValueError, match='missing'):
        cut_rail(archive=archive, out=tmp_path / 'cut.zip')
    assert not (tmp_path / 'cut.zip').exists()


def test_calendar_dates_alone_with_only_removals_is_refused(
    tmp_path: Path,
) -> None:
    """Without calendar.txt, removal rows alone give no service day."""
    archive = changed(
        out=tmp_path / 'removals.zip',
        change=without(member='calendar.txt'),
    )
    with pytest.raises(ValueError, match='no service days for the trips'):
        cut_rail(archive=archive, out=tmp_path / 'cut.zip')
    assert not (tmp_path / 'cut.zip').exists()


def test_calendar_alone_with_a_weekday_is_enough(tmp_path: Path) -> None:
    """A release with calendar.txt and no calendar_dates.txt is cut."""
    archive = changed(
        out=tmp_path / 'one.zip', change=without(member='calendar_dates.txt'),
    )
    assert cut_rail(archive=archive, out=tmp_path / 'cut.zip').trips == 6


def test_calendar_dates_alone_with_an_addition_is_enough(
    tmp_path: Path,
) -> None:
    """A release with only added dates for the kept services is cut."""

    def additions(tables: dict[str, list[dict[str, str]]]) -> None:
        """
        Keep only calendar_dates.txt, adding a day for each service.

        Parameters
        ----------
        tables : dict[str, list[dict[str, str]]]
            Rows by member, changed in place.
        """
        without(member='calendar.txt')(tables)
        tables['calendar_dates.txt'] = [
            {'service_id': service, 'date': '20261130', 'exception_type': '1'}
            for service in ('101', '102', '103')
        ]

    archive = changed(out=tmp_path / 'added.zip', change=additions)
    assert cut_rail(archive=archive, out=tmp_path / 'cut.zip').trips == 6


def test_a_calendar_with_no_weekday_set_is_refused(tmp_path: Path) -> None:
    """Services with every weekday 0 and no added dates never run."""

    def idle(tables: dict[str, list[dict[str, str]]]) -> None:
        """
        Clear every weekday of every service.

        Parameters
        ----------
        tables : dict[str, list[dict[str, str]]]
            Rows by member, changed in place.
        """
        for row in tables['calendar.txt']:
            for day in (
                'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
                'saturday', 'sunday',
            ):
                row[day] = '0'

    archive = changed(out=tmp_path / 'idle.zip', change=idle)
    with pytest.raises(ValueError, match='no service days for the trips'):
        cut_rail(archive=archive, out=tmp_path / 'cut.zip')
    assert not (tmp_path / 'cut.zip').exists()


def test_kept_trips_with_no_route_rows_are_refused(tmp_path: Path) -> None:
    """Trips whose routes are not in routes.txt stop the cut."""

    def orphan(tables: dict[str, list[dict[str, str]]]) -> None:
        """
        Point every trip at a route that is not there.

        Parameters
        ----------
        tables : dict[str, list[dict[str, str]]]
            Rows by member, changed in place.
        """
        for row in tables['trips.txt']:
            row['route_id'] = 'nowhere'

    archive = changed(out=tmp_path / 'orphan.zip', change=orphan)
    with pytest.raises(ValueError, match='no routes for the trips'):
        cut_rail(archive=archive, out=tmp_path / 'cut.zip')
    assert not (tmp_path / 'cut.zip').exists()


def test_kept_trips_with_no_service_days_are_refused(tmp_path: Path) -> None:
    """Trips whose services are in neither calendar table stop the cut."""

    def orphan(tables: dict[str, list[dict[str, str]]]) -> None:
        """
        Point every trip at a service that is not there.

        Parameters
        ----------
        tables : dict[str, list[dict[str, str]]]
            Rows by member, changed in place.
        """
        for row in tables['trips.txt']:
            row['service_id'] = 'never'

    archive = changed(out=tmp_path / 'orphan.zip', change=orphan)
    with pytest.raises(ValueError, match='no service days for the trips'):
        cut_rail(archive=archive, out=tmp_path / 'cut.zip')
    assert not (tmp_path / 'cut.zip').exists()


def misplaced(
    tables: dict[str, list[dict[str, str]]], *, stop_id: str, value: str,
) -> None:
    """
    Give one stop the same value for its latitude and longitude.

    Parameters
    ----------
    tables : dict[str, list[dict[str, str]]]
        Rows by member, changed in place.
    stop_id : str
        The stop.
    value : str
        Written to both columns.
    """
    for row in tables['stops.txt']:
        if row['stop_id'] == stop_id:
            row['stop_lat'] = row['stop_lon'] = value


@pytest.mark.parametrize('value', ['nan', '', 'inf', '200'])
@pytest.mark.parametrize('stop_id', ['9100BHAMNWS', '910GCAMBDGE'])
def test_a_kept_stop_without_usable_coordinates_is_refused(
    tmp_path: Path, stop_id: str, value: str,
) -> None:
    """A kept stop or station must have a place on the map."""
    bad = changed(
        out=tmp_path / 'bad.zip',
        change=partial(misplaced, stop_id=stop_id, value=value),
    )
    out = tmp_path / 'cut.zip'
    with pytest.raises(ValueError, match=f'{stop_id} has no usable'):
        cut_rail(archive=bad, out=out)
    assert not out.exists()


def test_a_dropped_stop_may_lack_coordinates(tmp_path: Path) -> None:
    """Cambridge Heath's trip is dropped, so its stop is not checked."""
    bad = changed(
        out=tmp_path / 'bad.zip',
        change=partial(misplaced, stop_id='9100CAMHTH', value='nan'),
    )
    assert cut_rail(archive=bad, out=tmp_path / 'cut.zip').trips == 6
