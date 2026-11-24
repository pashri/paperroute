"""Tests for network db: kerbs."""

import sqlite3
from contextlib import closing
from pathlib import Path

from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.core.chooser import Choice, Column, Level
from tests.adapters.network_db.helpers import add_routes, labels, opened


def test_kerb_pairs_reads_the_other_kerb(copy: Path) -> None:
    """A pair is read with the other kerb's name and point."""
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DELETE FROM stop_pairs')
        conn.executemany('INSERT INTO stop_pairs VALUES (?, ?, ?)', [
            ('B1', 'B2', 1), ('B2', 'B1', 1),
        ])
        conn.commit()
    with opened(path=copy) as repo:
        found = repo.kerb_pairs(stations=['B1', 'nowhere'])
    assert list(found) == ['B1']
    assert found['B1'].other.stop_id == 'B2'
    assert found['B1'].other.point is not None
    assert found['B1'].busy


def test_kerb_pairs_reads_nothing_from_an_old_file(copy: Path) -> None:
    """A file without the pairs table gives no pairs."""
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE stop_pairs')
        conn.commit()
    with opened(path=copy) as repo:
        assert repo.kerb_pairs(stations=['B1']) == {}


def test_places_include_the_track_places(copy: Path) -> None:
    """A station that exists only in track_places has a place."""
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute(
            "INSERT INTO track_places VALUES ('osm:5', 'Halt', -33.9, 151.1)",
        )
        conn.commit()
    with opened(path=copy) as repo:
        found = repo.places(stations=['osm:5', 'C', 'nowhere'])
    assert set(found) == {'osm:5', 'C'}
    assert found['osm:5'].name == 'Halt'


def test_kind_column_lists_seven_kinds_in_order(copy: Path) -> None:
    """Every kind with routes is listed, counted, in the chooser order."""
    add_routes(path=copy, rows=[
        ('metro:M1', 'metro', '', 0, 'M1', 1, 'North West', '', ''),
        ('light_rail:L1', 'light_rail', '', 0, 'L1', 1, 'Dulwich', '', ''),
        ('ferry:F1', 'ferry', '', 0, 'F1', 1, 'Manly', '', ''),
        ('school:S1:x', 'school', '1–99', 0, 'S1', 1, 'School', '', ''),
        ('coach:C1:x', 'coach', '', 0, 'C1', 1, 'Far', '', ''),
        ('replacement:1T1:x', 'replacement', '', 0, '1T1', 1, 'B', '',
         'T1'),
    ])
    with opened(path=copy) as repo:
        column = repo.choices(path=())
    assert column == Column(level=Level.KIND, items=(
        Choice(key='bus', label='Bus', badge='B', detail='2 routes'),
        Choice(key='train', label='Train', badge='T', detail='2 lines'),
        Choice(key='metro', label='Metro', badge='M', detail='1 line'),
        Choice(key='light_rail', label='Light rail', badge='L',
               detail='1 line'),
        Choice(key='ferry', label='Ferry', badge='F', detail='1 line'),
        Choice(key='school', label='School', badge='S', detail='1 route'),
        Choice(key='coach', label='Coach', badge='C',
               detail='1 route'),
    ))


def test_kind_column_leaves_out_empty_kinds(
    network: SqliteNetworkRepository,
) -> None:
    """Kinds without routes are not offered."""
    assert labels(column=network.choices(path=())) == ['Bus', 'Train']
