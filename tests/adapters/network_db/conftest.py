"""Shared fixtures for the network db tests."""

import shutil
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from tests.adapters.network_db.helpers import LINE_CITY
from tests.build.network_feed import build_network_db


@pytest.fixture(name='network_db', scope='module')
def _network_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the network timetable once for the module.

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


@pytest.fixture(name='copy')
def _copy(network_db: Path, tmp_path: Path) -> Path:
    """
    A copy of the network timetable that a test may change.

    Parameters
    ----------
    network_db : Path
        Timetable file.
    tmp_path : Path
        Working directory.

    Returns
    -------
    Path
        The copy.
    """
    return Path(shutil.copy(network_db, tmp_path / 'timetable.db'))


@pytest.fixture(name='network')
def _network(network_db: Path) -> Iterator[SqliteNetworkRepository]:
    """
    A repository over the unchanged network timetable.

    Parameters
    ----------
    network_db : Path
        Timetable file.

    Yields
    ------
    SqliteNetworkRepository
        Repository, closed after the test.
    """
    with closing(SqliteNetworkRepository(
        path=network_db,
        city=LINE_CITY,
    )) as repo:
        yield repo
