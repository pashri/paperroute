"""Shared fixtures for the timetable db tests."""

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_sydney.build.dedupe import merge_rail
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build.gtfs_files import Source
from tests.adapters.timetable_db.helpers import opened, three_day_db
from tests.build.feeds import bundle_rail_feed, bus_feed, sydneytrains_feed
from tests.build.network_feed import build_network_db
from tests.helpers import old_schema, slice_db


@pytest.fixture(name='repo')
def _repo(tmp_path: Path) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over the small merged fixture timetable.

    Parameters
    ----------
    tmp_path : Path
        Working directory.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the test.
    """
    db = three_day_db(
        tmp_path=tmp_path,
        sources=[
            Source(
                name='sydneytrains',
                archive=sydneytrains_feed(path=tmp_path / 's.zip'),
            ),
            Source(
                name='bundle',
                archive=bundle_rail_feed(path=tmp_path / 'r.zip'),
            ),
            Source(name='bundle', archive=bus_feed(path=tmp_path / 'b.zip')),
        ],
    )
    with closing(sqlite3.connect(db)) as conn:
        merge_rail(conn=conn)
    with opened(path=db) as repo:
        yield repo


@pytest.fixture(name='slice_repo', scope='module')
def _slice_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over the real GTFS slice, with shapes.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the working directory.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the tests.
    """
    db = slice_db(tmp_path=tmp_path_factory.mktemp('slice'))
    with opened(path=db) as repo:
        yield repo


@pytest.fixture(name='network_db', scope='module')
def _network_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the network feed, with its run tables, over a 3-day window.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Returns
    -------
    Path
        Timetable file.
    """
    return build_network_db(folder=tmp_path_factory.mktemp('network'))


@pytest.fixture(name='network')
def _network(network_db: Path) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over the network timetable.

    Parameters
    ----------
    network_db : Path
        Timetable file.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the test.
    """
    with opened(path=network_db) as repo:
        yield repo


@pytest.fixture(name='old_network')
def _old_network(
    network_db: Path, tmp_path: Path,
) -> Iterator[SqliteTimetableRepository]:
    """
    A repository over a schema-2 copy of the network timetable.

    Parameters
    ----------
    network_db : Path
        Timetable file.
    tmp_path : Path
        Working directory.

    Yields
    ------
    SqliteTimetableRepository
        Repository, closed after the test.
    """
    copy = tmp_path / 'timetable.db'
    shutil.copy(network_db, copy)
    old_schema(path=copy)
    with opened(path=copy) as repo:
        yield repo
