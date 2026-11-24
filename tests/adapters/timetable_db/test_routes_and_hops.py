"""Tests for timetable db: routes and hops."""

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from paperroute_sydney.city import SydneyFeed
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_testkit.core.factories import place
from paperroute_transit.adapters.route_trip_rows import ROUTE_TRIPS
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.build.passed import PASSED_SCHEMA
from paperroute_transit.core.geo import make_shape, pack_shape
from paperroute_transit.core.model import Pin
from tests.adapters.timetable_db.helpers import (
    WINDOW,
    at,
    meta_db,
    opened,
    platform_repo,
    three_day_db,
)
from tests.build.feeds import DAILY, STATIONS, stop_times


def test_source_dates_read_each_source_and_skip_a_bad_one(
    tmp_path: Path,
) -> None:
    """Each source's date is read; one that does not parse is skipped."""
    db = meta_db(tmp_path=tmp_path, rows={
        **WINDOW,
        'source_date:bods': '2026-11-16',
        'source_date:rail': '2026-11-15',
        'source_date:bad': 'x',
        'source_end:bods': '2027-10-03',
    })
    with opened(path=db) as repo:
        assert repo.source_dates() == {
            'bods': date(2026, 11, 16), 'rail': date(2026, 11, 15),
        }


def test_a_timetable_without_source_dates_has_none(tmp_path: Path) -> None:
    """A file built before source dates were recorded reads as none."""
    db = meta_db(tmp_path=tmp_path, rows=WINDOW)
    with opened(path=db) as repo:
        assert repo.source_dates() == {}


@pytest.mark.parametrize(('stop_id', 'station'), [
    ('2145615', 'G2145615'),
    ('2150411', '215020'),
    ('215020', '215020'),
    ('nowhere', 'nowhere'),
])
def test_station_for_names_the_parent_station(
    repo: SqliteTimetableRepository, stop_id: str, station: str,
) -> None:
    """A stand or platform gives its parent; other IDs give themselves."""
    assert repo.station_for(stop_id=stop_id) == station


PATH: Final[tuple[tuple[float, float], ...]] = (
    (0.0, 0.0), (1000.0, 300.0), (2000.0, 0.0),
)


@pytest.fixture(name='hops_repo')
def _hops_repo(
    repo: SqliteTimetableRepository, tmp_path: Path,
) -> Iterator[SqliteTimetableRepository]:
    """
    A copy of the fixture timetable with two rail hops.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        The fixture timetable.
    tmp_path : Path
        Working directory.

    Yields
    ------
    SqliteTimetableRepository
        Repository over the copy, closed after the test.
    """
    assert repo.db.path is not None
    copy = tmp_path / 'hops.db'
    shutil.copy(repo.db.path, copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.executescript(
            'DROP TABLE IF EXISTS rail_hops;' + PASSED_SCHEMA,
        )
        conn.executemany(
            "INSERT INTO rail_hops VALUES (?, ?, '[]', ?, ?)",
            [
                ('A', 'B', 1, pack_shape(
                    shape=make_shape(shape_id='', points=PATH),
                )),
                ('B', 'C', 0, None),
            ],
        )
        conn.commit()
    with opened(path=copy) as hops:
        yield hops


def test_rail_hops_read_both_directions(
    hops_repo: SqliteTimetableRepository,
) -> None:
    """A hop stored one way is given back either way."""
    found = hops_repo.rail_hops(pairs={('A', 'B'), ('B', 'A'), ('B', 'C')})
    assert found[('A', 'B')] == PATH
    assert found[('B', 'A')] == tuple(reversed(PATH))
    assert ('B', 'C') not in found


def test_rail_hops_without_a_track_table_are_empty(
    hops_repo: SqliteTimetableRepository,
) -> None:
    """A timetable with no rail_hops table gives no hops."""
    assert hops_repo.db.path is not None
    with closing(sqlite3.connect(hops_repo.db.path)) as conn:
        conn.execute('DROP TABLE rail_hops')
        conn.commit()
    assert hops_repo.rail_hops(pairs={('A', 'B')}) == {}


KERBS: Final[list[tuple[str, str, list[tuple[str, str]]]]] = [
    ('here', 'P', [('Q', '08:00:00'), ('P_1', '08:10:00')]),
    ('there', 'Q', [('P_1', '08:05:00'), ('Q', '08:15:00')]),
    (
        'loop', 'P',
        [('P_2', '08:20:00'), ('Q', '08:30:00'), ('P_1', '08:40:00')],
    ),
]


def kerb_departures(
    *, tmp_path: Path, destination: str | None = None,
) -> list[tuple[str, datetime, bool]]:
    """
    List a both-sides pin's departures at Q, the kerb across being P.

    Parameters
    ----------
    tmp_path : Path
        Working directory.
    destination : str | None
        The pin's Destination, if any.

    Returns
    -------
    list[tuple[str, datetime, bool]]
        Trip ID, departure time and whether it leaves from across.
    """
    pin = Pin(
        origin=place('Q'),
        opposite=place('P'),
        destination=None if destination is None else place(destination),
    )
    with closing(platform_repo(tmp_path=tmp_path, trips=KERBS)) as repo:
        found = repo.departures(
            pin=pin, start=at(hour=7, minute=55), end=at(hour=9, minute=0),
        )
    return [(d.trip_id, d.departs, d.facts.across) for d in found]


def test_departures_lists_both_kerbs_in_time_order(tmp_path: Path) -> None:
    """A both-sides pin lists each kerb's departures in one time order."""
    assert kerb_departures(tmp_path=tmp_path) == [
        ('here', at(hour=8, minute=0), False),
        ('there', at(hour=8, minute=5), True),
        ('loop', at(hour=8, minute=20), True),
        ('loop', at(hour=8, minute=30), False),
    ]


def test_departures_reads_one_kerb_beside_a_destination(
    tmp_path: Path,
) -> None:
    """A pin with a Destination reads its From alone."""
    found = kerb_departures(tmp_path=tmp_path, destination='P')
    assert [trip for trip, _, across in found if not across] == [
        'here', 'loop',
    ]
    assert not any(across for _, _, across in found)


def test_route_trips_query_searches_stop_times_by_trip(
    repo: SqliteTimetableRepository,
) -> None:
    """The route's trips are found by index, with no full table scan."""
    assert repo.db.path is not None
    with closing(sqlite3.connect(repo.db.path)) as conn:
        plan = [
            row[3]
            for row in conn.execute(
                'EXPLAIN QUERY PLAN ' + ROUTE_TRIPS.format(service=''),
                ('606', '2026-10-15', '2026-10-16'),
            )
        ]
    assert not any(line.startswith('SCAN') for line in plan)
    assert any('SEARCH r USING INDEX routes_short' in x for x in plan)
    assert any('SEARCH t USING INDEX trips_route' in x for x in plan)


def test_route_trips_lists_the_route_on_the_day_and_the_day_before(
    repo: SqliteTimetableRepository,
) -> None:
    """Both days' trips of the route come with their first and last times."""
    day = date(2026, 10, 16)
    found = repo.route_trips(route='606', source=SydneyFeed.BUS, day=day)
    assert {t.day for t in found} == {day - timedelta(days=1), day}
    first = next(t for t in found if t.trip_id == 'bus-0803')
    assert first.starts < first.ends
    assert first.direction == 0


def test_route_trips_leaves_out_other_routes(
    repo: SqliteTimetableRepository,
) -> None:
    """Another route name or another source finds nothing."""
    day = date(2026, 10, 16)
    other = repo.route_trips(
        route='T1', source=SydneyFeed.TRAIN, day=day,
    )
    assert other
    assert 'bus-0803' not in {t.trip_id for t in other}
    assert not repo.route_trips(route='606', source='nope', day=day)


def test_route_trips_match_the_feed_source(
    repo: SqliteTimetableRepository,
) -> None:
    """Route trips are found by feed source, not the timetable source."""
    found = repo.route_trips(
        route='606', source=SydneyFeed.BUS, day=date(2026, 10, 16),
    )
    assert 'bus-0803' in {trip.trip_id for trip in found}
    assert not repo.route_trips(
        route='606', source=SydneyFeed.TRAIN, day=date(2026, 10, 16),
    )


def metro_files(*, trip: str) -> dict[str, list[dict[str, str]]]:
    """
    Metro-like feed: one M1 trip between the fixture stations.

    Parameters
    ----------
    trip : str
        Trip ID.

    Returns
    -------
    dict[str, list[dict[str, str]]]
        Member name to rows.
    """
    return {
        'stops.txt': STATIONS,
        'routes.txt': [{'route_id': 'SMNW_M1', 'agency_id': 'SMNW',
                        'route_short_name': 'M1',
                        'route_long_name': 'Tallawong to Sydenham',
                        'route_type': '401', 'route_color': '168388',
                        'route_text_color': 'FFFFFF'}],
        'calendar.txt': [{'service_id': 'm-daily', **DAILY}],
        'trips.txt': [{'route_id': 'SMNW_M1', 'service_id': 'm-daily',
                       'trip_id': trip, 'trip_headsign': 'Sydenham',
                       'direction_id': '1', 'block_id': ''}],
        'stop_times.txt': stop_times(
            trip=trip,
            times=[('2150411', '08:15:00'), ('2000321', '08:40:00')],
        ),
    }


def test_route_trips_find_a_metro_line_by_its_feed(tmp_path: Path) -> None:
    """M1 trips are metro whichever timetable source carries them."""
    db = three_day_db(
        tmp_path=tmp_path,
        sources=[
            Source(
                name=name,
                archive=write_feed(
                    path=tmp_path / f'{name}.zip',
                    files=metro_files(trip=trip),
                ),
            )
            for name, trip in (('metro', 'M-0815'), ('bundle', 'B-0815'))
        ],
    )
    with opened(path=db) as repo:
        found = {
            source: repo.route_trips(
                route='M1', source=source, day=date(2026, 10, 16),
            )
            for source in (SydneyFeed.METRO, SydneyFeed.TRAIN)
        }
    assert {trip.trip_id for trip in found[SydneyFeed.METRO]} == {
        'M-0815', 'B-0815',
    }
    assert not found[SydneyFeed.TRAIN]


def test_turned_trips_reads_the_next_trip_after_a_turnaround(
    network_db: Path, tmp_path: Path,
) -> None:
    """A schema-12 file answers the next trip by trip ID."""
    copy = tmp_path / 'timetable.db'
    shutil.copy(network_db, copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DELETE FROM trip_turns')
        conn.execute("INSERT INTO trip_turns VALUES ('x1', 'x2')")
        conn.commit()
    with opened(path=copy) as repo:
        found = repo.turned_trips(trip_ids=['x1', 'e1'])
    assert found == {'x1': 'x2'}


def test_turned_trips_reads_nothing_from_a_schema_11_file(
    network_db: Path, tmp_path: Path,
) -> None:
    """A file without trip_turns answers no turnarounds, without error."""
    copy = tmp_path / 'timetable.db'
    shutil.copy(network_db, copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.execute('DROP TABLE IF EXISTS trip_turns')
        conn.commit()
    with opened(path=copy) as repo:
        assert repo.turned_trips(trip_ids=['x1']) == {}
