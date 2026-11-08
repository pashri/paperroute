"""Tests for the change choices a connection's editor offers."""

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from paperroute.adapters.change_rows import CHANGE_COUNT, CHANGE_M
from paperroute.adapters.network_db import SqliteNetworkRepository
from paperroute.adapters.pin_options import SqlitePinOptions
from paperroute.build.timetable import Window, build_timetable
from paperroute.city import SYDNEY, SYDNEY_PROJECTION
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.model import StopPlace

REAL = Path(__file__).parent.parent / 'fixtures' / 'real'


@pytest.fixture(name='built', scope='module')
def _built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the real-slice timetable once for the module.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Returns
    -------
    Path
        Timetable file.
    """
    out = tmp_path_factory.mktemp('changes') / 'timetable.db'
    build_timetable(
        city=SYDNEY,
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=date(2026, 10, 15), days=3),
        out=out,
    )
    return out


@pytest.fixture(name='options')
def _options(built: Path) -> Iterator[SqlitePinOptions]:
    """
    Pin options over the real slice.

    Parameters
    ----------
    built : Path
        Timetable file.

    Yields
    ------
    SqlitePinOptions
        Options, closed after the test.
    """
    with closing(SqliteNetworkRepository(
        path=built,
        projection=SYDNEY_PROJECTION,
    )) as network:
        yield SqlitePinOptions(network=network)


def known(options: SqlitePinOptions, stop_id: str) -> StopPlace:
    """
    Read a place that the slice has.

    Parameters
    ----------
    options : SqlitePinOptions
        Options.
    stop_id : str
        Stop or station ID.

    Returns
    -------
    StopPlace
        The place.
    """
    found = options.place(stop_id=stop_id)
    assert found is not None
    return found


def test_change_stops_near_parramatta(options: SqlitePinOptions) -> None:
    """Close stops come nearest first, the stand's station leading."""
    stand = known(options, '2150119')
    assert stand.point is not None
    changes = options.change_stops(place=stand)
    ids = [change.place.stop_id for change in changes]
    assert '215020' in ids
    assert 'G2150296' in ids
    assert ids[0] == '215020'
    metres = [change.metres for change in changes]
    assert metres == sorted(metres)
    assert all(each <= CHANGE_M for each in metres)
    assert len(changes) <= CHANGE_COUNT


def test_reached_from_parramatta_includes_central(
    options: SqlitePinOptions,
) -> None:
    """Central is reached from Parramatta; Parramatta itself is not."""
    reached = options.reached_from(place=known(options, '215020'))
    ids = [each.stop_id for each in reached]
    assert '200060' in ids
    assert '215020' not in ids
    assert reached == tuple(
        sorted(reached, key=lambda each: (each.name, each.stop_id)),
    )


def test_routes_between_parramatta_central(
    options: SqlitePinOptions,
) -> None:
    """The T1 runs both ways between Parramatta and Central."""
    parramatta = known(options, '215020')
    central = known(options, '200060')
    assert 'T1' in options.routes_between(
        origin=parramatta, destination=central,
    )
    assert 'T1' in options.routes_between(
        origin=central, destination=parramatta,
    )


def test_change_stops_without_point_empty(options: SqlitePinOptions) -> None:
    """A place with no point has no change stops."""
    bare = StopPlace(stop_id='215020', name='Parramatta', point=None)
    assert options.change_stops(place=bare) == ()


def test_no_network_tables_empty(built: Path, tmp_path: Path) -> None:
    """A file without the run and chooser tables offers nothing."""
    copy = Path(shutil.copy(built, tmp_path / 'timetable.db'))
    with closing(sqlite3.connect(copy)) as conn:
        conn.executescript(
            'DROP TABLE chooser_routes; DROP TABLE runs; '
            'DROP TABLE run_stops;',
        )
        conn.commit()
    with closing(SqliteNetworkRepository(
        path=copy,
        projection=SYDNEY_PROJECTION,
    )) as network:
        old = SqlitePinOptions(network=network)
        place = StopPlace(stop_id='215020', name='P', point=(0.0, 0.0))
        assert old.change_stops(place=place) == ()
        assert old.reached_from(place=place) == ()
        assert old.routes_between(origin=place, destination=place) == ()
