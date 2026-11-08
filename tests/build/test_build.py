"""Tests for building a whole timetable file."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from paperroute.build.timetable import Window, build_timetable
from paperroute.city import SYDNEY_REF
from paperroute_transit.build.gtfs_files import Source
from tests.build.feeds import bundle_rail_feed, bus_feed, sydneytrains_feed

REAL = Path(__file__).parent.parent / 'fixtures' / 'real'


def test_build_timetable_writes_file_and_meta(tmp_path: Path) -> None:
    """The output appears complete, with meta, and no '.new' file left."""
    out = tmp_path / 'timetable.db'
    report = build_timetable(
        city_ref=SYDNEY_REF,
        sources=[
            Source(name='sydneytrains',
                   archive=sydneytrains_feed(path=tmp_path / 's.zip')),
            Source(name='bundle',
                   archive=bundle_rail_feed(path=tmp_path / 'r.zip')),
            Source(name='bundle',
                   archive=bus_feed(path=tmp_path / 'b.zip'))],
        window=Window(start=date(2026, 10, 16), days=2), out=out)
    assert report.dropped == 1
    assert not out.with_suffix('.new').exists()
    with closing(sqlite3.connect(out)) as conn:
        meta = dict(conn.execute('SELECT key, value FROM meta').fetchall())
    assert meta['window_start'] == '2026-10-16'


def test_build_real_slice_has_no_duplicate_t1_departures(
        tmp_path: Path) -> None:
    """Real data: no T1 run appears twice at Parramatta after merging."""
    out = tmp_path / 'timetable.db'
    build_timetable(
        city_ref=SYDNEY_REF,
        sources=[Source(name='sydneytrains',
                        archive=REAL / 'sydneytrains.zip'),
                 Source(name='bundle', archive=REAL / 'bundle.zip')],
        window=Window(start=date(2026, 10, 16), days=1), out=out)
    with closing(sqlite3.connect(out)) as conn:
        rows = conn.execute("""
            SELECT st.dep, COUNT(*) FROM stop_times st
            JOIN trips t ON t.trip_id = st.trip_id
            JOIN routes r ON r.route_id = t.route_id
            JOIN stops s ON s.stop_id = st.stop_id
            WHERE r.short_name = 'T1' AND s.parent_station = '215020'
              AND t.service = 1
            GROUP BY st.dep, st.stop_id, t.direction_id
            HAVING COUNT(*) > 1""").fetchall()
        aliases = conn.execute(
            'SELECT COUNT(*) FROM trip_aliases').fetchone()[0]
    assert rows == []
    assert aliases > 0
