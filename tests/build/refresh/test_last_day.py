"""Tests for refresh: last day."""

from contextlib import closing
from datetime import date, timedelta
from http import HTTPStatus
from pathlib import Path

import pytest

from paperroute_sydney.city import SYDNEY
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_testkit.core.kit_steps import step
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build import refresh as refresh_module
from paperroute_transit.core.banners import timetable_banner
from paperroute_transit.core.messages import TimetableSeen
from tests.build.feed_server import FeedServer, Served
from tests.build.feeds import bus_files
from tests.build.refresh.helpers import DAYS, METRO, START, run, window_start
from tests.core.state.helpers import fresh


def stale_metro(*, tmp_path: Path, ends: date) -> bytes:
    """
    The Metro stand-in with a calendar that runs out on a given day.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip.
    ends : date
        The calendar's last day.

    Returns
    -------
    bytes
        The zip file.
    """
    files = bus_files()
    files['calendar.txt'] = [
        {**row, 'end_date': ends.strftime('%Y%m%d')}
        for row in files['calendar.txt']
    ]
    path = write_feed(path=tmp_path / 'stale-metro.zip', files=files)
    return path.read_bytes()


def last_day(*, data_dir: Path) -> date | None:
    """
    The last day the board would read from the live timetable.

    Parameters
    ----------
    data_dir : Path
        Data directory.

    Returns
    -------
    date | None
        The repository's last day.
    """
    with closing(
        SqliteTimetableRepository(
            path=data_dir / 'timetable.db', city=SYDNEY,
        ),
    ) as repo:
        return repo.last_day()


def test_stale_source_brings_the_last_day_forward(
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
) -> None:
    """A source whose calendar ends inside the window sets the last day."""
    ends = START + timedelta(days=4)
    server.state.files[METRO] = Served(
        body=stale_metro(tmp_path=tmp_path, ends=ends),
    )
    run(server=server, data_dir=data_dir)
    assert last_day(data_dir=data_dir) == ends


def test_rebuilds_from_stored_zips_keep_the_last_day(
    *,
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renewing the window from stored zips never moves the last day on."""
    ends = START + timedelta(days=4)
    server.state.files[METRO] = Served(
        body=stale_metro(tmp_path=tmp_path, ends=ends),
    )
    run(server=server, data_dir=data_dir)
    server.state.status = HTTPStatus.FORBIDDEN
    for week in (1, 2, 3):
        later = START + timedelta(days=week * (DAYS - 4))
        monkeypatch.setattr(
            refresh_module, 'yesterday', lambda *, zone, day=later: day,
        )
        report = run(server=server, data_dir=data_dir)
        assert report.rebuilt
        assert report.all_failed
        assert window_start(data_dir=data_dir) == later.isoformat()
        assert last_day(data_dir=data_dir) == ends


def test_banner_shows_three_days_before_the_earliest_source_ends(
    server: FeedServer,
    data_dir: Path,
    tmp_path: Path,
) -> None:
    """The board warns from three days before a stale source runs out."""
    friday = date(2026, 10, 16)  # fresh()'s day in Sydney
    banners = []
    for ends in (friday + timedelta(days=3), friday + timedelta(days=2)):
        server.state.files[METRO] = Served(
            body=stale_metro(tmp_path=tmp_path, ends=ends),
            etag=f'"{ends}"',
        )
        run(server=server, data_dir=data_dir)
        state, _ = step(
            fresh(),
            TimetableSeen(version='v1', last_day=last_day(data_dir=data_dir)),
        )
        banners.append(timetable_banner(state=state))
    assert banners == [
        None,
        'Timetable ends Sunday. Check the internet and key.',
    ]
