"""Tests for refresh: schema and origin."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tests.build.feed_server import FeedServer
from tests.build.refresh.helpers import run


def test_refresh_rebuilds_when_schema_version_differs(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A timetable from older code is rebuilt with no source changed."""
    run(server=server, data_dir=data_dir)
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        conn.execute(
            "UPDATE meta SET value = '1' WHERE key = 'schema_version'",
        )
        conn.commit()
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert server.state.hits == {'HEAD': 3}
    assert not report.changed
    assert report.rebuilt


def test_refresh_rebuilds_a_schema_eight_timetable(
    server: FeedServer,
    data_dir: Path,
) -> None:
    """A timetable stored as schema 8 is rebuilt with no source change."""
    run(server=server, data_dir=data_dir)
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        conn.execute(
            "UPDATE meta SET value = '8' WHERE key = 'schema_version'",
        )
        conn.commit()
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert not report.changed
    assert report.rebuilt


@pytest.mark.parametrize(
    ('origin', 'rebuilt'),
    [('0.0,51.5', True), ('151.2093,-33.8688', False), (None, False)],
)
def test_update_rebuilds_on_other_origin(
    *,
    server: FeedServer,
    data_dir: Path,
    origin: str | None,
    rebuilt: bool,
) -> None:
    """A current-schema timetable from another origin is rebuilt."""
    run(server=server, data_dir=data_dir)
    with closing(sqlite3.connect(data_dir / 'timetable.db')) as conn:
        conn.execute("DELETE FROM meta WHERE key = 'origin'")
        if origin is not None:
            conn.execute(
                "INSERT INTO meta VALUES ('origin', ?)",
                (origin,),
            )
        conn.commit()
    server.state.hits.clear()
    report = run(server=server, data_dir=data_dir)
    assert server.state.hits == {'HEAD': 3}
    assert not report.changed
    assert report.rebuilt is rebuilt
