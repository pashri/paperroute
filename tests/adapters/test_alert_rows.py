"""Tests for resolving alert routes and stops against the timetable."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.dedupe import merge_rail
from paperroute.build.gtfs_files import Source
from tests.build.feeds import build_db

REAL: Final[Path] = Path(__file__).parents[1] / 'fixtures' / 'real'
CENTRAL: Final[str] = '200060'
SCHEMA: Final[str] = """
CREATE TABLE stops (stop_id TEXT PRIMARY KEY, name TEXT NOT NULL,
  parent_station TEXT, lat REAL, lon REAL, platform TEXT NOT NULL);
CREATE TABLE routes (route_id TEXT PRIMARY KEY, short_name TEXT NOT NULL,
  long_name TEXT NOT NULL, type INTEGER NOT NULL, color TEXT, text_color TEXT,
  agency_id TEXT, source TEXT NOT NULL);
"""


@pytest.fixture(name='slice_repo', scope='module')
def _slice_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over the real GTFS slice.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the working directory.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the tests.
    """
    db = build_db(
        tmp_path=tmp_path_factory.mktemp('alerts'),
        start=date(2026, 10, 15),
        days=3,
        sources=[
            Source(name='sydneytrains', archive=REAL / 'sydneytrains.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
    with closing(SqliteTimetableRepository(path=db)) as repo:
        yield repo


def routes_repo(
    *, tmp_path: Path, lines: list[tuple[str, str, int]]
) -> SqliteTimetableRepository:
    """
    Make a timetable holding only routes.

    Parameters
    ----------
    tmp_path : Path
        Directory for the file.
    lines : list[tuple[str, str, int]]
        Route ID, short name and type of each route.

    Returns
    -------
    SqliteTimetableRepository
        Repository over the file; the caller closes it.
    """
    path = tmp_path / 'routes.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO routes VALUES (?, ?, '', ?, NULL, NULL, NULL, 'm')",
            lines,
        )
        conn.commit()
    return SqliteTimetableRepository(path=path)


def test_alert_places_resolve_routes(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A Sydney Trains route ID resolves to its line's short name."""
    places = slice_repo.alert_places(route_ids=['WST_2c'], stop_ids=[])
    assert places.routes['WST_2c'] == 'T1'
    assert places.lines == {'WST_2c': 'sydneytrains:WST_2c'}


def test_suffix_route_resolves(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """An unknown ID resolves to its suffix when a route has that name."""
    short = str(
        slice_repo.fetch(
            sql="SELECT short_name FROM routes WHERE short_name = '606'",
            params=(),
        )[0][0]
    )
    found = slice_repo.alert_places(
        route_ids=['9999_' + short], stop_ids=[]
    )
    assert found.routes == {'9999_' + short: short}
    assert found.lines == {}


def test_unresolved_routes_absent(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """IDs the timetable cannot place are left out."""
    ids = ['IWLR-191', '4T.T.NT36']
    known = slice_repo.fetch(
        sql='SELECT route_id FROM routes WHERE route_id IN (?, ?)',
        params=tuple(ids),
    )
    assert not known
    assert slice_repo.alert_places(route_ids=ids, stop_ids=[]).routes == {}


def test_stop_family(slice_repo: SqliteTimetableRepository) -> None:
    """A station's family holds its platforms; a platform's, its station."""
    child = str(
        slice_repo.fetch(
            sql='SELECT stop_id FROM stops WHERE parent_station = ?',
            params=(CENTRAL,),
        )[0][0]
    )
    places = slice_repo.alert_places(
        route_ids=[], stop_ids=[CENTRAL, child, 'nope']
    )
    assert CENTRAL in places.stops[CENTRAL]
    assert child in places.stops[CENTRAL]
    assert CENTRAL in places.stops[child]
    assert child in places.stops[child]
    assert 'nope' not in places.stops


def test_metro_route_read_from_timetable(tmp_path: Path) -> None:
    """The one metro route's short name is read; two give None."""
    with closing(
        routes_repo(
            tmp_path=tmp_path, lines=[('metro:SMNW_M1', 'M1', 401)]
        )
    ) as one:
        assert one.alert_places(route_ids=[], stop_ids=[]).metro == 'M1'
    two_dir = tmp_path / 'two'
    two_dir.mkdir()
    lines = [('metro:SMNW_M1', 'M1', 401), ('metro:X_M2', 'M2', 401)]
    with closing(routes_repo(tmp_path=two_dir, lines=lines)) as two:
        assert two.alert_places(route_ids=[], stop_ids=[]).metro is None


def test_no_metro_route_gives_none(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A timetable without a type-401 route has no metro line."""
    assert slice_repo.alert_places(route_ids=[], stop_ids=[]).metro is None


def test_ferry_route_resolves_through_bundle(tmp_path: Path) -> None:
    """A ferry feed route ID resolves through its bundle: form."""
    lines = [('bundle:9-F9-sj2-1', 'F9', 4)]
    with closing(routes_repo(tmp_path=tmp_path, lines=lines)) as repo:
        places = repo.alert_places(route_ids=['9-F9-sj2-1'], stop_ids=[])
    assert places.routes == {'9-F9-sj2-1': 'F9'}


def test_trainlink_serves_central(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """NSW TrainLink trips call in Central's stop family."""
    family = slice_repo.alert_places(route_ids=[], stop_ids=[CENTRAL]).stops
    assert slice_repo.serves_trainlink(stop_ids=family[CENTRAL])


def test_trainlink_skips_bus_stop(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """No NSW TrainLink trip calls at a 606 bus stop."""
    stop = str(
        slice_repo.fetch(
            sql=(
                'SELECT st.stop_id FROM stop_times st '
                'JOIN trips t USING (trip_id) '
                'JOIN routes r USING (route_id) '
                "WHERE r.short_name = '606' LIMIT 1"
            ),
            params=(),
        )[0][0]
    )
    assert not slice_repo.serves_trainlink(stop_ids=[stop])


def test_trainlink_no_stops_false(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """No stops are served by nothing."""
    assert not slice_repo.serves_trainlink(stop_ids=[])
