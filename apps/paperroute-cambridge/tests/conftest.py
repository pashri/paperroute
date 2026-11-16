"""Give the Cambridge app's tests Cambridge as the city under test."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from paperroute_cambridge.asset_root import APP_ASSETS
from paperroute_cambridge.city import CAMBRIDGE, CAMBRIDGE_REF
from paperroute_testkit.city_setting import use_city_under_test
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.timetable import Window, build_timetable
from tests.build.timetable_rows import OPENED

FIXTURES: Final[Path] = Path(__file__).parent / 'fixtures' / 'real'
RECORDED: Final[dict[str, object]] = json.loads(
    (FIXTURES / 'recorded.json').read_text(),
)
RECORDED_DAY: Final[date] = date.fromisoformat(str(RECORDED['at'])[:10])


def pytest_configure(config: pytest.Config) -> None:
    """
    Set the city under test and its asset root before any test runs.

    Parameters
    ----------
    config : pytest.Config
        The run's configuration.
    """
    del config
    use_city_under_test(city=CAMBRIDGE, assets=APP_ASSETS)


@pytest.fixture(autouse=True)
def _close_connections() -> Iterator[None]:
    """
    Close and forget each connection the test builders opened.

    Yields
    ------
    None
        Once, while the test runs.
    """
    yield
    while OPENED:
        OPENED.pop().close()


@pytest.fixture(name='built_path', scope='module')
def built_path_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the timetable from the recorded zip for the recorded day.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Makes the directory for the database.

    Returns
    -------
    Path
        The database.
    """
    out = tmp_path_factory.mktemp('built') / 'timetable.db'
    build_timetable(
        sources=[Source(name='bods', archive=FIXTURES / 'bods.zip')],
        window=Window(start=RECORDED_DAY, days=1),
        out=out,
        city_ref=CAMBRIDGE_REF,
    )
    return out


@pytest.fixture(name='built', scope='module')
def built_fixture(built_path: Path) -> Iterator[sqlite3.Connection]:
    """
    Open the built timetable read-only.

    Parameters
    ----------
    built_path : Path
        The database.

    Yields
    ------
    sqlite3.Connection
        The connection, closed after the module.
    """
    with closing(
        sqlite3.connect(f'file:{built_path}?mode=ro', uri=True),
    ) as conn:
        yield conn
