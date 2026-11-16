"""Tests for cutting and writing GTFS slices."""

from pathlib import Path

from paperroute_testkit.build.feed_zip import write_feed
from paperroute_transit.build.gtfs_files import read_rows
from paperroute_transit.build.gtfs_slice import rows_where, write_tables


def test_rows_where_leaves_out_rows_without_the_column(
    tmp_path: Path,
) -> None:
    """Rows without the column are left out, and a missing member is empty."""
    archive = write_feed(
        path=tmp_path / 'feed.zip',
        files={'stops.txt': [{'stop_id': 'a'}, {'stop_id': 'b'}]},
    )
    kept = rows_where(
        archive=archive, member='stops.txt', key='stop_id', keep={'b'},
    )
    assert kept == [{'stop_id': 'b'}]
    assert not rows_where(
        archive=archive, member='trips.txt', key='stop_id', keep={'b'},
    )
    assert not rows_where(
        archive=archive, member='stops.txt', key='parent', keep={''},
    )


def test_write_tables_skips_empty_tables(tmp_path: Path) -> None:
    """Write tables writes a member for each table that has rows."""
    out = tmp_path / 'slice.zip'
    write_tables(out=out, tables={'stops.txt': [{'stop_id': 'a'}], 'x.txt': []})
    assert list(read_rows(archive=out, member='stops.txt')) == [
        {'stop_id': 'a'},
    ]
    assert not list(read_rows(archive=out, member='x.txt'))
