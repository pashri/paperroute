"""Shared fixtures for the rail build tests."""

from pathlib import Path

import pytest

from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build import swap
from tests.build.rail_build.helpers import built
from tests.conftest import RECORDED_DAY


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
