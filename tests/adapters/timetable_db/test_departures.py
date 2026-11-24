"""Tests for timetable db: departures."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from paperroute_sydney.city import SydneyFeed
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_effects import VersionWatch
from paperroute_transit.core.patterns import TripPattern
from tests.adapters.timetable_db.helpers import (
    AROUND,
    HOLLIS,
    HOLLIS_TO_PARRA,
    MONDAY,
    PARRA_TO_CENTRAL,
    WINDOW,
    at,
    bundle_db,
    hollis_departures,
    meta_db,
    opened,
    slice_departures,
)
from tests.helpers import slice_db


def test_departures_bus_with_stand_and_times(
    repo: SqliteTimetableRepository,
) -> None:
    """A bus departure carries route, mode, stand and both times."""
    found = hollis_departures(repo=repo)
    assert [(d.trip_id, d.route, d.source, d.platform) for d in found] == [
        ('bus-0803', '606', SydneyFeed.BUS, '')
    ]
    assert found[0].departs == at(hour=8, minute=3)
    assert found[0].arrives == at(hour=8, minute=39)


def test_day_departures_keep_only_the_service_day(
    repo: SqliteTimetableRepository,
) -> None:
    """A day's departures are that service day's, the 606 among them."""
    found = repo.day_departures(pin=HOLLIS_TO_PARRA, day=date(2026, 10, 16))
    assert {d.service_day for d in found} == {date(2026, 10, 16)}
    assert 'bus-0803' in {d.trip_id for d in found}


def test_departures_carry_road(repo: SqliteTimetableRepository) -> None:
    """The 606 bus is road and the Parramatta trains are not."""
    bus = repo.departures(
        pin=HOLLIS_TO_PARRA,
        start=at(hour=8, minute=0),
        end=at(hour=9, minute=0),
    )
    train = repo.departures(
        pin=PARRA_TO_CENTRAL,
        start=at(hour=8, minute=0),
        end=at(hour=9, minute=30),
    )
    assert [d.facts.road for d in bus] == [True]
    assert {d.facts.road for d in train} == {False}


def test_patterns_carry_road(repo: SqliteTimetableRepository) -> None:
    """A pattern is road by its route's type."""
    found = repo.patterns(trip_ids={'bus-0803', 'S-0835'}, around=MONDAY)
    assert found['bus-0803'].road is True
    assert found['S-0835'].road is False


def bus_pattern(*, tmp_path: Path, direction: int | None) -> TripPattern:
    """
    Read the pattern of the bus trip once its direction is set.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    direction : int | None
        Value to store in the trip's ``direction_id`` column.

    Returns
    -------
    TripPattern
        The pattern of trip ``bus-0803``.
    """
    db = bundle_db(tmp_path=tmp_path, days=3)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            'UPDATE trips SET direction_id = ? WHERE trip_id = ?',
            (direction, 'bus-0803'),
        )
        conn.commit()
        route_id = conn.execute(
            'SELECT route_id FROM trips WHERE trip_id = ?', ('bus-0803',)
        ).fetchone()[0]
    with opened(path=db) as repo:
        found = repo.patterns(trip_ids={'bus-0803'}, around=MONDAY)
    assert found['bus-0803'].route_id == route_id
    return found['bus-0803']


def test_patterns_read_the_direction(tmp_path: Path) -> None:
    """A trip's direction_id is read into its pattern."""
    assert bus_pattern(tmp_path=tmp_path, direction=1).direction == 1


def test_patterns_without_a_direction_read_none(tmp_path: Path) -> None:
    """A NULL direction_id reads as None."""
    assert bus_pattern(tmp_path=tmp_path, direction=None).direction is None


def test_patterns_read_the_route_id(tmp_path: Path) -> None:
    """The route ID is the trip's, not the feed key."""
    found = bus_pattern(tmp_path=tmp_path, direction=0)
    assert found.route_id is not None
    assert found.route_id != found.source


def test_departures_uses_station_children_and_skips_no_pickup(
    repo: SqliteTimetableRepository,
) -> None:
    """Platforms under a station count; a stop with no pickup doesn't."""
    found = repo.departures(
        pin=PARRA_TO_CENTRAL,
        start=at(hour=8, minute=0),
        end=at(hour=9, minute=30),
    )
    assert [d.trip_id for d in found] == ['S-0835', 'B-0900']
    assert found[0].aliases == ('B-0835',)
    assert found[0].platform == '1'


def test_departures_excludes_wrong_direction(
    repo: SqliteTimetableRepository,
) -> None:
    """A trip reaching the destination before the origin isn't direct."""
    found = repo.departures(
        pin=PARRA_TO_CENTRAL,
        start=at(hour=8, minute=0),
        end=at(hour=8, minute=40),
    )
    assert 'B-NB' not in [d.trip_id for d in found]


def test_departures_includes_previous_service_day_after_midnight(
    repo: SqliteTimetableRepository,
) -> None:
    """A 25:10 trip from the 16th's service day leaves at 1:10 on the 17th."""
    found = repo.departures(
        pin=HOLLIS_TO_PARRA,
        start=at(hour=1, minute=0, day=17),
        end=at(hour=1, minute=30, day=17),
    )
    assert [(d.trip_id, d.departs) for d in found] == [
        ('bus-2510', at(hour=1, minute=10, day=17))
    ]


def test_reads_timetable_without_origin(tmp_path: Path) -> None:
    """A schema-7 file with no origin meta still answers departures,
    patterns and the modes vehicle positions are polled for."""
    db = bundle_db(tmp_path=tmp_path, days=3)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO meta VALUES ('schema_version', '7')")
        conn.commit()
    with opened(path=db) as repo:
        found = hollis_departures(repo=repo)
        assert [d.trip_id for d in found] == ['bus-0803']
        assert repo.schema_version() == '7'
        assert repo.origin() is None
        pattern = repo.patterns(
            trip_ids=['bus-0803'], around=found[0].service_day,
        )['bus-0803']
        assert pattern.source == SydneyFeed.BUS
        assert pattern.stops
        assert all(stop.point is not None for stop in pattern.stops)


@pytest.mark.parametrize(
    ('rows', 'last_day'),
    [
        (WINDOW, date(2026, 10, 28)),
        ({'window_start': '2026-10-15'}, None),
        ({'window_days': '14'}, None),
        ({}, None),
        (
            {
                **WINDOW,
                'source_end:bundle': '2027-01-12',
                'source_end:metro': '2026-10-20',
                'source_end:sydneytrains': '2026-11-14',
            },
            date(2026, 10, 20),
        ),
        ({**WINDOW, 'source_end:bundle': '2027-01-12'}, date(2026, 10, 28)),
        ({**WINDOW, 'window_days': 'fourteen'}, None),
        ({**WINDOW, 'window_start': 'soon'}, None),
        ({**WINDOW, 'window_days': '999999999'}, None),
        (
            {
                **WINDOW,
                'source_end:metro': 'never',
                'source_end:bundle': '2026-10-25',
            },
            date(2026, 10, 25),
        ),
    ],
    ids=[
        'window', 'no-days', 'no-start', 'no-meta', 'earliest-source',
        'window-first', 'bad-days', 'bad-start', 'huge-days',
        'bad-source-end',
    ],
)
def test_last_day_from_the_window_and_sources(
    tmp_path: Path, rows: dict[str, str], last_day: date | None,
) -> None:
    """The last day is the earliest of the window's end and source ends."""
    db = meta_db(tmp_path=tmp_path, rows=rows)
    with opened(path=db) as repo:
        assert repo.last_day() == last_day


async def test_version_watch_survives_malformed_window(
    tmp_path: Path,
) -> None:
    """A malformed window reads as no window, not an error."""
    db = meta_db(
        tmp_path=tmp_path,
        rows={**WINDOW, 'window_days': 'x', 'built_at': 'v1'},
    )
    with opened(path=db) as repo:
        seen = await VersionWatch(timetable=repo).check()
    assert seen is not None
    assert seen.last_day is None


def test_old_timetable_still_places_vehicles(tmp_path: Path) -> None:
    """
    A schema-7 slice with no origin still gives shapes and stop points.

    Patterns, shapes, the modes near a stop and the route labels are what
    vehicle positions are placed and labelled from.
    """
    db = slice_db(tmp_path=tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DELETE FROM meta WHERE key = 'origin'")
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('schema_version', '7')",
        )
        conn.commit()
    with opened(path=db) as repo:
        assert (repo.schema_version(), repo.origin()) == ('7', None)
        found = slice_departures(repo=repo)
        trips = [departure.trip_id for departure in found]
        patterns = repo.patterns(trip_ids=trips, around=AROUND)
        assert {pattern.source for pattern in patterns.values()} == {
            SydneyFeed.BUS,
        }
        assert all(
            stop.point is not None
            for pattern in patterns.values()
            for stop in pattern.stops
        )
        shape_ids = {str(departure.shape_id) for departure in found}
        assert set(repo.shapes(shape_ids=shape_ids)) == shape_ids
        x, y = repo.stop(stop_id=HOLLIS).point or (0.0, 0.0)
        box = (x - 2000.0, y - 2000.0, x + 2000.0, y + 2000.0)
        assert SydneyFeed.BUS in repo.modes_near(box=box)
        assert set(repo.route_labels(trip_ids=trips).values()) == {'606'}
