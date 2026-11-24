"""Shared fixtures for the refresh tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from paperroute_device.adapters.rate_limit import SyncStartLimiter
from paperroute_sydney.build.tfnsw_sources import SYDNEY_SOURCES
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.build import swap
from tests.build.feed_server import FeedServer, Served, feed_server
from tests.build.refresh.helpers import REAL, START, metro_zip


@pytest.fixture(name='server')
def _server(tmp_path: Path) -> Iterator[FeedServer]:
    """
    A feed server holding all three sources.

    Parameters
    ----------
    tmp_path : Path
        Directory for the Metro zip.

    Yields
    ------
    FeedServer
        Server, closed after the test.
    """
    bodies = {
        'sydneytrains': (REAL / 'sydneytrains.zip').read_bytes(),
        'metro': metro_zip(tmp_path=tmp_path),
        'bundle': (REAL / 'bundle.zip').read_bytes(),
    }
    with feed_server() as server:
        for spec in SYDNEY_SOURCES:
            server.state.files['/' + spec.path] = Served(
                body=bodies[spec.name],
            )
        yield server


@pytest.fixture(name='waits', autouse=True)
def _waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """
    Record when the refresh's requests start instead of sleeping.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Patcher.

    Returns
    -------
    list[float]
        When each request started, on a clock that moves only by sleeping.
    """
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append((waits[-1] if waits else 0.0) + seconds)

    limiter = SyncStartLimiter(
        per_second=1,
        clock=lambda: waits[-1] if waits else 0.0,
        sleep=sleep,
    )
    monkeypatch.setattr(refresh_module, 'LIMITER', limiter)
    return waits


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
        refresh_module,
        'yesterday',
        lambda *, zone: START,
    )
    path = tmp_path / 'data'
    path.mkdir()
    return path
