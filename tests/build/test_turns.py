"""Tests for recording where a bus turns back as its next trip."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from paperroute_sydney.city import RAIL_TYPES, SYDNEY
from paperroute_transit.build import timetable
from paperroute_transit.build.runs import (
    RUN_SCHEMA,
    load_runs,
    next_trips,
    trip_joins,
)
from paperroute_transit.build.turns import TURN_SCHEMA, trip_turns
from tests.build.test_extras import network_build, skip_extras
from tests.build.test_runs import hand_trip


def turns_of(*, first: str, second: str) -> dict[str, str]:
    """
    Find the turnarounds between two bus trips on one block.

    Parameters
    ----------
    first : str
        Stations of the earlier trip, space-separated.
    second : str
        Stations of the trip after it.

    Returns
    -------
    dict[str, str]
        Trip ID to the trip its vehicle turns back as.
    """
    trips = [
        hand_trip(trip_id='a', route='400', stations=first,
                  first_dep=0, last_arr=100, block='k'),
        hand_trip(trip_id='b', route='400', stations=second,
                  first_dep=150, last_arr=250, block='k'),
    ]
    return dict(trip_turns(trips=trips, nexts=next_trips(trips=trips)))


def test_trip_turns_yields_a_turnaround() -> None:
    """A bus heading back the way it came turns around."""
    assert turns_of(first='P Q R', second='R Q P') == {'a': 'b'}


def test_trip_turns_skips_a_through_join() -> None:
    """A bus running on through is a join, not a turnaround."""
    assert not turns_of(first='P Q R', second='R S T')


@pytest.mark.parametrize('second', ['R Q P', 'R S T', 'X Y'])
def test_trip_turns_and_trip_joins_never_share_a_pair(second: str) -> None:
    """A pair is a join or a turnaround, never both."""
    trips = [
        hand_trip(trip_id='a', route='400', stations='P Q R',
                  first_dep=0, last_arr=100, block='k'),
        hand_trip(trip_id='b', route='400', stations=second,
                  first_dep=150, last_arr=250, block='k'),
    ]
    nexts = next_trips(trips=trips)
    turns = set(trip_turns(trips=trips, nexts=nexts))
    joins = set(trip_joins(trips=trips, nexts=nexts))
    assert not turns & joins


def test_load_runs_writes_no_turns_without_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the flag the turnaround table stays empty."""
    monkeypatch.setattr(timetable, 'build_extras', skip_extras)
    db = network_build(folder=tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(RUN_SCHEMA)
        conn.executescript(TURN_SCHEMA)
        load_runs(conn=conn, classify=SYDNEY.classify, rail_types=RAIL_TYPES)
        rows = conn.execute('SELECT COUNT(*) FROM trip_turns').fetchone()
    assert rows == (0,)
