"""Tests for rail build: sources."""

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_cambridge.__main__ import CAMBRIDGE_PARTS
from paperroute_cambridge.build.__main__ import CAMBRIDGE_BUILD
from paperroute_cambridge.build.bods_sources import CAMBRIDGE_SOURCES
from paperroute_cambridge.build.rail_cut import crs_of, cut_rail
from paperroute_cambridge.city import CAMBRIDGE
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.adapters.pin_options import SqlitePinOptions
from paperroute_transit.build.sources import UrlDownloader
from tests.build.rail_build.helpers import SUNDAY, built, departures
from tests.build.test_rail_cut import changed, with_norwich
from tests.conftest import RECORDED_DAY


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
        UrlDownloader.request(
            downloader, spec=spec, method='HEAD',
        ).full_url
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
