"""Tests for storing the rail track in a database."""

import sqlite3
from array import array
from contextlib import closing
from pathlib import Path

from paperroute_transit.build.track_db import (
    RailTrack,
    TrackStation,
    read_track_db,
    write_track,
)

TRACK = RailTrack(
    xs=array('d', [0.0, 10.5, 20.0]),
    ys=array('d', [0.0, 1.5, -3.0]),
    edges=array('q', [0, 1, 1, 2]),
    stations=(TrackStation(7, 'Halt One', -33.5, 151.25, 10.5, 1.5),),
)


def test_the_track_round_trips(tmp_path: Path) -> None:
    """Nodes, edges and stations read back as they were written."""
    path = tmp_path / 'tiles.db'
    with closing(sqlite3.connect(path)) as conn:
        write_track(conn=conn, track=TRACK)
        conn.commit()
    assert read_track_db(path=path) == TRACK


def test_a_tiles_file_without_track_reads_as_none(tmp_path: Path) -> None:
    """A missing file and a file with no track tables give None."""
    assert read_track_db(path=tmp_path / 'absent.db') is None
    other = tmp_path / 'other.db'
    with closing(sqlite3.connect(other)) as conn:
        conn.execute('CREATE TABLE meta (key TEXT, value TEXT)')
        conn.commit()
    assert read_track_db(path=other) is None
    assert not (tmp_path / 'absent.db').exists()
