"""Tests for timetable db: visits."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Final

from paperroute_sydney.city import SydneyMode
from paperroute_testkit.core.factories import pin_of
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from tests.adapters.timetable_db.helpers import at, p_departures, platform_repo


def test_empty_running_trips_are_never_listed(tmp_path: Path) -> None:
    """An Empty Train is left off the whole-stop and Destination boards."""
    trips = [
        ('a', 'Q', [('P_1', '08:00:00'), ('Q', '08:10:00')]),
        ('e', 'Empty Train', [('P_2', '08:05:00'), ('Q', '08:15:00')]),
    ]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        for destination in (None, 'Q'):
            assert p_departures(repo=repo, destination=destination) == [
                ('a', at(hour=8, minute=0)),
            ]


def test_consecutive_calls_within_the_from_are_one_departure(
    tmp_path: Path,
) -> None:
    """A trip moving between two platforms of the From leaves once.

    It is listed at its last call there, when it leaves the place; a
    trip that comes back after another stop is listed at both visits.
    """
    trips = [
        ('two', 'Q', [('P_1', '08:00:00'), ('P_2', '08:05:00'),
                      ('Q', '08:10:00')]),
        ('loop', 'Q', [('P_1', '08:20:00'), ('Q', '08:30:00'),
                       ('P_2', '08:40:00'), ('Q', '08:50:00')]),
    ]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        for destination in (None, 'Q'):
            assert p_departures(repo=repo, destination=destination) == [
                ('two', at(hour=8, minute=5)),
                ('loop', at(hour=8, minute=20)),
                ('loop', at(hour=8, minute=40)),
            ]


def test_consecutive_calls_keep_the_last_with_pickup(
    tmp_path: Path,
) -> None:
    """A later call at the From with no pickup leaves the earlier one."""
    trips = [
        ('two', 'Q', [('P_1', '08:00:00'), ('P_2', '08:05:00'),
                      ('Q', '08:10:00')]),
    ]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        with closing(sqlite3.connect(tmp_path / 'timetable.db')) as conn:
            conn.execute(
                "UPDATE stop_times SET pickup = 1 "
                "WHERE trip_id = 'two' AND stop_id = 'P_2'",
            )
            conn.commit()
        for destination in (None, 'Q'):
            assert p_departures(repo=repo, destination=destination) == [
                ('two', at(hour=8, minute=0)),
            ]


LOOP: Final[list[tuple[str, str, list[tuple[str, str]]]]] = [
    (
        'x',
        'Q',
        [
            ('Z', '07:50:00'), ('P_1', '08:00:00'), ('Z', '08:10:00'),
            ('Z', '08:20:00'), ('P_2', '08:30:00'), ('Z', '08:40:00'),
            ('Q', '08:50:00'),
        ],
    ),
]


def loop_departures(
    *,
    repo: SqliteTimetableRepository,
    kind: str | None,
    end: datetime,
) -> list[tuple[str, datetime]]:
    """
    List the trips and times leaving P for Q from 7:55 until a time.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository.
    kind : str | None
        The pin's kind, or None for no kind.
    end : datetime
        Latest departure.

    Returns
    -------
    list[tuple[str, datetime]]
        Trip ID and departure time of each departure.
    """
    pin = replace(pin_of('P', 'Q'), kind=kind)
    found = repo.departures(
        pin=pin, start=at(hour=7, minute=55), end=end,
    )
    return [(d.trip_id, d.departs) for d in found]


def test_a_loop_pass_is_not_listed(tmp_path: Path) -> None:
    """A train kind lists only the pass after the last call at the From."""
    with closing(platform_repo(tmp_path=tmp_path, trips=LOOP)) as repo:
        assert loop_departures(
            repo=repo,
            kind=SydneyMode.TRAIN,
            end=at(hour=9, minute=0),
        ) == [('x', at(hour=8, minute=30))]
        assert loop_departures(
            repo=repo,
            kind=None,
            end=at(hour=9, minute=0),
        ) == [('x', at(hour=8, minute=0)), ('x', at(hour=8, minute=30))]


def test_a_loop_pass_is_dropped_when_its_later_pass_is_past_the_window(
    tmp_path: Path,
) -> None:
    """The window ends before the later pass, which still drops the first."""
    with closing(platform_repo(tmp_path=tmp_path, trips=LOOP)) as repo:
        assert loop_departures(
            repo=repo,
            kind=SydneyMode.TRAIN,
            end=at(hour=8, minute=15),
        ) == []
        assert loop_departures(
            repo=repo,
            kind=None,
            end=at(hour=8, minute=15),
        ) == [('x', at(hour=8, minute=0))]


def test_consecutive_from_calls_are_one_visit(tmp_path: Path) -> None:
    """A trip From, From, X, Destination is listed once, never dropped."""
    trips = [(
        'c', 'Q',
        [('P_1', '08:00:00'), ('P_2', '08:05:00'), ('Z', '08:10:00'),
         ('Q', '08:20:00')],
    )]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        assert loop_departures(
            repo=repo,
            kind=SydneyMode.TRAIN,
            end=at(hour=9, minute=0),
        ) == [('c', at(hour=8, minute=5))]


def test_a_visit_ending_without_pickup_is_listed_once(
    tmp_path: Path,
) -> None:
    """A last call that takes no one on leaves the visit's first call."""
    trips = [(
        'c', 'Q',
        [('P_1', '08:00:00'), ('P_2', '08:05:00'), ('Z', '08:10:00'),
         ('Q', '08:20:00')],
    )]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        with closing(sqlite3.connect(tmp_path / 'timetable.db')) as conn:
            conn.execute(
                "UPDATE stop_times SET pickup = 1 "
                "WHERE trip_id = 'c' AND stop_id = 'P_2'",
            )
            conn.commit()
        assert loop_departures(
            repo=repo,
            kind=SydneyMode.TRAIN,
            end=at(hour=9, minute=0),
        ) == [('c', at(hour=8, minute=0))]


def test_a_true_loop_still_drops_the_first_pass(tmp_path: Path) -> None:
    """A trip From, X, From, Destination lists only its second pass."""
    trips = [(
        'c', 'Q',
        [('P_1', '08:00:00'), ('Z', '08:10:00'), ('P_2', '08:20:00'),
         ('Q', '08:30:00')],
    )]
    with closing(platform_repo(tmp_path=tmp_path, trips=trips)) as repo:
        assert loop_departures(
            repo=repo,
            kind=SydneyMode.TRAIN,
            end=at(hour=9, minute=0),
        ) == [('c', at(hour=8, minute=20))]
