"""Tests for timetable db: no destination."""

from pathlib import Path

from paperroute_testkit.core.factories import pin_of
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from tests.adapters.timetable_db.helpers import (
    at,
    opened,
    platform_db,
    whole_stop,
)


def test_no_destination_lists_the_whole_stop(
    network: SqliteTimetableRepository,
) -> None:
    """Every train route leaving C is listed, whatever routes were chosen."""
    found = whole_stop(repo=network, origin='C', hours=(7, 7))
    assert [d.trip_id for d in found] == [
        'ew1', 'we1', 'er1', 'cc1', 're1', 'y1', 'cc1',
    ]
    assert {d.route for d in found} == {'T1', 'T2'}
    assert [d.departs for d in found] == sorted(d.departs for d in found)


def test_no_destination_covers_every_platform(tmp_path: Path) -> None:
    """Departures from two platforms of one station both appear."""
    db = platform_db(
        tmp_path=tmp_path,
        trips=[
            ('a', 'Q', [('P_1', '08:00:00'), ('Q', '08:10:00')]),
            ('b', 'Q', [('P_2', '08:05:00'), ('Q', '08:15:00')]),
        ],
        extra_stops=[
            {'stop_id': 'Z', 'stop_name': 'Z Station', 'location_type': '0',
             'parent_station': '', 'stop_lat': '-33.7',
             'stop_lon': '150.9'},
        ],
    )
    with opened(path=db) as repo:
        found = repo.departures(
            pin=pin_of('P', None),
            start=at(hour=8, minute=0),
            end=at(hour=9, minute=0),
        )
    assert [(d.trip_id, d.platform, d.facts.then) for d in found] == [
        ('a', '1', None),
        ('b', '2', None),
    ]


def test_no_destination_runs_to_the_last_stop(
    network: SqliteTimetableRepository,
) -> None:
    """A C to W2 trip runs to its last call, at W2."""
    found = whole_stop(repo=network, origin='C', hours=(7, 7))[0]
    assert (found.trip_id, found.origin_stop) == ('ew1', 'C_1')
    assert (found.destination_seq, found.destination_stop) == (6, 'W2_1')
    assert found.arrives == at(hour=7, minute=15, day=6)


def test_no_destination_skips_trips_ending_here(
    network: SqliteTimetableRepository,
) -> None:
    """From W2, only the trips leaving W2 are listed, not those ending."""
    found = whole_stop(repo=network, origin='W2', hours=(0, 23))
    assert [d.trip_id for d in found] == [
        'we1', 'y2', 'we2', 'we3', 'we4', 'we5', 'we6',
    ]
