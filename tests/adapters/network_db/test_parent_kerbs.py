"""Tests for network db: parent kerbs."""

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_testkit.fakes import FakeNetwork
from paperroute_transit.app_kerbs import parents_read
from paperroute_transit.core.reach import ReachInput, StripKey, build_reach
from tests.adapters.network_db.helpers import opened, place, tails_from


def with_parent(*, path: Path) -> None:
    """
    Put a second stop C_2 under station C and record C's two kerbs.

    Parameters
    ----------
    path : Path
        Timetable file.
    """
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "INSERT INTO stops SELECT 'C_2', 'C_2 Stop', parent_station,"
            " lat, lon, platform FROM stops WHERE stop_id = 'C_1'"
        )
        conn.execute('DELETE FROM parent_kerbs')
        conn.executemany(
            'INSERT INTO parent_kerbs VALUES (?, ?, ?, ?, ?, ?)', [
                ('C_1', 'C', 'C_2', '["J", "L1"]', 9, 1),
                ('C_2', 'C', 'C_1', '["E1"]', 7, 1),
            ],
        )
        conn.commit()


def test_parent_kerbs_reads_both_kerbs(copy: Path) -> None:
    """Both kerbs of a parent come back with their sets and trips."""
    with_parent(path=copy)
    with opened(path=copy) as repo:
        found = repo.parent_kerbs(stations=['C', 'nowhere'])
    assert list(found) == ['C']
    one, two = found['C'].kerbs
    assert (one.place.stop_id, one.onward, one.trips) == (
        'C_1', frozenset({'J', 'L1'}), 9,
    )
    assert (two.place.stop_id, two.place.name, two.onward, two.trips) == (
        'C_2', 'C_2 Stop', frozenset({'E1'}), 7,
    )
    assert two.place.point is not None and found['C'].busy


def test_parent_kerbs_skips_a_station_with_one_kerb_row(copy: Path) -> None:
    """A station without exactly two rows is left out."""
    with_parent(path=copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute("DELETE FROM parent_kerbs WHERE stop_id = 'C_2'")
        conn.commit()
    with opened(path=copy) as repo:
        assert repo.parent_kerbs(stations=['C']) == {}


def test_parent_kerbs_reads_nothing_from_an_old_file(copy: Path) -> None:
    """A file without the table answers empty."""
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE parent_kerbs')
        conn.commit()
    with opened(path=copy) as repo:
        assert repo.parent_kerbs(stations=['C']) == {}


def test_parents_read_answers_empty_for_a_corrupt_row(
    copy: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A kerb row that cannot be decoded gives no parents and a warning."""
    with_parent(path=copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute("UPDATE parent_kerbs SET onward = 'not json'")
        conn.commit()
    with opened(path=copy) as repo:
        with caplog.at_level(logging.WARNING):
            found = parents_read(network=repo, stations=['C'])
    assert found == {}
    assert 'reading the parent kerbs failed' in caplog.text


def test_fake_network_parent_kerbs_answers_the_real_shape(
    copy: Path,
) -> None:
    """The fake answers the same shape as the real repository."""
    with_parent(path=copy)
    with opened(path=copy) as repo:
        real = repo.parent_kerbs(stations=['C', 'nowhere'])
    fake = FakeNetwork(parents=real)
    assert fake.parent_kerbs(stations=['C', 'nowhere']) == real
    assert fake.parent_kerbs(stations=['nowhere']) == {}
    failing = FakeNetwork(pairs_error=sqlite3.OperationalError('gone'))
    with pytest.raises(sqlite3.OperationalError):
        failing.parent_kerbs(stations=['C'])


def parent_input(*, path: Path, key: StripKey) -> ReachInput:
    """
    Read a reach input from the copy for a key on C's kerbs.

    Parameters
    ----------
    path : Path
        Timetable file.
    key : StripKey
        The key.

    Returns
    -------
    ReachInput
        The input.
    """
    with opened(path=path) as repo:
        found = repo.reach_input(key=key, origin=place(stop_id=key.station))
    assert found is not None
    return found


def test_reach_input_splits_a_parent_between_its_kerbs(copy: Path) -> None:
    """Both kerbs of a parent each get their own way's tails."""
    with_parent(path=copy)
    every = tails_from(path=copy, station='C', kind=None)
    found = parent_input(path=copy, key=StripKey(
        station='C_1', kind=None, routes=(), across='C_2',
    ))
    assert found.origin.stop_id == 'C'
    assert found.tails == tuple(
        tail for tail in every if tail.after and tail.after[0] in {'J', 'L1'}
    )
    assert found.across_tails == tuple(
        tail for tail in every if tail.after and tail.after[0] == 'E1'
    )
    assert found.tails and found.across_tails
    assert found.station_of['C_2'] == 'C'
    ids = [row.place.stop_id for row in build_reach(data=found).rows]
    assert ids.index('E1') < ids.index('C') < ids.index('J')


def test_reach_input_keeps_a_kerb_pin_to_its_way(copy: Path) -> None:
    """A key on one kerb keeps only that kerb's way."""
    with_parent(path=copy)
    found = parent_input(
        path=copy, key=StripKey(station='C_2', kind=None, routes=()),
    )
    assert found.tails and {tail.after[0] for tail in found.tails} == {'E1'}
    assert found.across_tails == ()


def test_reach_input_reads_a_parent_once_without_kerb_rows(
    copy: Path,
) -> None:
    """Without kerb rows the station's runs are read once, none across."""
    with_parent(path=copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE parent_kerbs')
        conn.commit()
    found = parent_input(path=copy, key=StripKey(
        station='C_1', kind=None, routes=(), across='C_2',
    ))
    assert found.tails == tails_from(path=copy, station='C', kind=None)
    assert found.across_tails == ()


def test_approach_roads_reads_the_roads_into_a_kerbs_parent(
    copy: Path,
) -> None:
    """A kerb's parent gives its roads by the place before it."""
    with_parent(path=copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.executemany(
            'INSERT INTO approach_roads VALUES (?, ?, ?)', [
                ('C', 'B', 'High St'), ('C', 'B', 'Low Rd'),
                ('C', 'J', 'Mill Rd'), ('E', 'D', 'Far Rd'),
            ],
        )
        conn.commit()
    with opened(path=copy) as repo:
        assert repo.approach_roads(stations=['C_2']) == {
            'B': ('High St', 'Low Rd'), 'J': ('Mill Rd',),
        }
    assert FakeNetwork(roads={'B': ('x',)}).approach_roads(
        stations=['C'],
    ) == {'B': ('x',)}


def test_approach_roads_reads_nothing_from_an_old_file(copy: Path) -> None:
    """A file without the table answers empty."""
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE IF EXISTS approach_roads')
        conn.commit()
    with opened(path=copy) as repo:
        assert repo.approach_roads(stations=['C']) == {}
