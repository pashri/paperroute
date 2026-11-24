"""Tests for timetable db: patterns."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from paperroute_sydney.city import SYDNEY_PROJECTION, SydneyFeed
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_transit.adapters.timetable_db import (
    LABEL_LIMIT,
    SqliteTimetableRepository,
)
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.geo import to_metres
from paperroute_transit.core.model import StopPlace
from tests.adapters.timetable_db.helpers import (
    AROUND,
    HOLLIS,
    bus_bundle_db,
    opened,
    regional_db,
    replace_with,
    slice_departures,
)
from tests.build.feeds import build_db


def test_stop_has_projected_point(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A stop's point is its stored lon and lat in metres."""
    found = slice_repo.stop(stop_id=HOLLIS)
    row = slice_repo.fetch(
        sql='SELECT name, lon, lat FROM stops WHERE stop_id = ?',
        params=(HOLLIS,),
    )[0]
    assert found.stop_id == HOLLIS
    assert found.name == row[0]
    lon, lat = (float(str(value)) for value in row[1:])
    assert found.point == to_metres(
        lon=lon,
        lat=lat,
        projection=SYDNEY_PROJECTION,
    )
    with pytest.raises(KeyError):
        slice_repo.stop(stop_id='nope')


def test_stop_without_coordinates_has_no_point(tmp_path: Path) -> None:
    """A stop with no lat or lon has no point."""
    bare = {
        'stop_id': 'X1',
        'stop_name': 'Nowhere',
        'location_type': '0',
        'parent_station': '',
        'stop_lat': '',
        'stop_lon': '',
    }
    archive = write_feed(
        path=tmp_path / 'bare.zip',
        files={
            'stops.txt': [bare],
            'routes.txt': [],
            'calendar.txt': [],
            'trips.txt': [],
            'stop_times.txt': [],
        },
    )
    db = build_db(
        tmp_path=tmp_path,
        start=AROUND,
        days=1,
        sources=[Source(name='bundle', archive=archive)],
    )
    with opened(path=db) as repo:
        assert repo.stop(stop_id='X1') == StopPlace(
            stop_id='X1', name='Nowhere', point=None
        )


def test_departures_carry_shape_id(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Every 606 departure names a namespaced shape."""
    found = slice_departures(repo=slice_repo)
    assert found
    assert {d.route for d in found} == {'606'}
    assert all(
        d.shape_id and d.shape_id.startswith('bundle:') for d in found
    )


def test_shapes_returns_requested_only(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Only the asked-for shapes come back; unknown IDs are absent."""
    ids = {d.shape_id for d in slice_departures(repo=slice_repo)}
    first = sorted(str(i) for i in ids)[0]
    found = slice_repo.shapes(shape_ids=[first, 'bundle:nope'])
    assert list(found) == [first]
    assert found[first].shape_id == first
    assert len(found[first].points) >= 2
    assert slice_repo.shapes(shape_ids=[]) == {}


def test_patterns_resolve_alias_and_days(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """An aliased trip is keyed by the alias, with ordered stops."""
    alias = str(
        slice_repo.fetch(
            sql="""
            SELECT a.alias_id FROM trip_aliases a
            JOIN trips t ON t.trip_id = a.trip_id
            JOIN services sv ON sv.service_id = t.service_id
            WHERE sv.date = '2026-10-16' ORDER BY a.alias_id
            """,
            params=(),
        )[0][0]
    )
    found = slice_repo.patterns(trip_ids=[alias], around=AROUND)
    pattern = found[alias]
    assert pattern.trip_id == alias
    assert pattern.route == 'T1'
    assert pattern.source == SydneyFeed.TRAIN
    times = [stop.arrives for stop in pattern.stops]
    assert times == sorted(times)
    assert len(times) > 2
    assert pattern.days
    assert set(pattern.days) <= {date(2026, 10, 15), AROUND}
    assert all(stop.point is not None for stop in pattern.stops)


def test_patterns_carry_stations(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Each call names its parent station, or the stop when it has none."""
    trips = [d.trip_id for d in slice_departures(repo=slice_repo)]
    found = slice_repo.patterns(trip_ids=trips, around=AROUND)
    stations = {stop.station for p in found.values() for stop in p.stops}
    parents = {
        str(row[0])
        for row in slice_repo.fetch(
            sql="SELECT stop_id FROM stops WHERE parent_station IS NULL "
            "OR parent_station = ''",
            params=(),
        )
    }
    assert f'G{HOLLIS}' in stations
    assert HOLLIS not in stations
    assert '215020' in stations
    assert stations <= parents


def test_patterns_name_each_calls_station(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Each call carries its station's name, not its platform's."""
    trips = [d.trip_id for d in slice_departures(repo=slice_repo)]
    found = slice_repo.patterns(trip_ids=trips, around=AROUND)
    names = {
        str(row[0]): str(row[1])
        for row in slice_repo.fetch(
            sql='SELECT stop_id, name FROM stops', params=(),
        )
    }
    stops = [stop for p in found.values() for stop in p.stops]
    assert stops
    assert all(stop.name == names[stop.station] for stop in stops)


def test_unknown_trip_pattern_is_absent(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """A trip the timetable doesn't know has no pattern."""
    assert slice_repo.patterns(trip_ids=['nope'], around=AROUND) == {}


def test_route_labels_by_trip_and_alias(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """Trips and aliases both map to their route's short name."""
    trip = slice_departures(repo=slice_repo)[0].trip_id
    alias = str(
        slice_repo.fetch(
            sql='SELECT alias_id FROM trip_aliases ORDER BY alias_id',
            params=(),
        )[0][0]
    )
    labels = slice_repo.route_labels(trip_ids=[trip, alias, 'nope'])
    assert labels == {trip: '606', alias: 'T1'}


def test_route_labels_cache_is_bounded(
    repo: SqliteTimetableRepository,
) -> None:
    """A cache past its limit is cleared before it takes more."""
    unknown = [f'x{i}' for i in range(LABEL_LIMIT + 1)]
    assert repo.route_labels(trip_ids=unknown) == {}
    assert len(repo.labels) == LABEL_LIMIT + 1
    assert repo.route_labels(trip_ids=['bus-0803']) == {'bus-0803': '606'}
    assert len(repo.labels) == 1


def record_queries(
    *, repo: SqliteTimetableRepository, monkeypatch: pytest.MonkeyPatch
) -> list[int]:
    """
    Record the parameter count of every parameterised query a repo runs.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to watch.
    monkeypatch : pytest.MonkeyPatch
        Restores the repository afterwards.

    Returns
    -------
    list[int]
        Filled as queries run.
    """
    seen: list[int] = []
    original = repo.fetch

    def fetch(
        *, sql: str, params: tuple[object, ...] | dict[str, object]
    ) -> list[tuple[object, ...]]:
        if params:
            seen.append(len(params))
        return original(sql=sql, params=params)

    monkeypatch.setattr(repo, 'fetch', fetch)
    return seen


def test_route_labels_query_in_chunks_of_500(
    repo: SqliteTimetableRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1200 IDs take three chunks per lookup, by trip and by alias."""
    seen = record_queries(repo=repo, monkeypatch=monkeypatch)
    repo.route_labels(trip_ids=[f't{i}' for i in range(1200)])
    assert sorted(seen) == [200, 200, 500, 500, 500, 500]


def test_route_labels_remember_unknown_ids(
    repo: SqliteTimetableRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown ID is asked about once, not on every poll."""
    seen = record_queries(repo=repo, monkeypatch=monkeypatch)
    assert repo.route_labels(trip_ids=['nope', 'bus-0803']) == {
        'bus-0803': '606'
    }
    first = len(seen)
    assert repo.route_labels(trip_ids=['nope', 'bus-0803']) == {
        'bus-0803': '606'
    }
    assert first > 0
    assert len(seen) == first


def test_route_labels_reset_when_file_replaced(tmp_path: Path) -> None:
    """A label cached from the old file is not served from the new one."""
    live = bus_bundle_db(tmp_path=tmp_path, folder='a')
    with opened(path=live) as repo:
        assert repo.route_labels(trip_ids=['bus-0803']) == {
            'bus-0803': '606'
        }
        replace_with(live=live, trip='B-0810')
        assert repo.route_labels(trip_ids=['bus-0803']) == {}
        assert repo.route_labels(trip_ids=['B-0810']) == {'B-0810': 'T1'}


def test_patterns_days_cover_the_day_before(
    repo: SqliteTimetableRepository,
) -> None:
    """Dates are the asked-for day and the day before, where they run."""
    def days(*, day: int) -> tuple[date, ...]:
        found = repo.patterns(
            trip_ids=['bus-0803'], around=date(2026, 10, day)
        )
        return found['bus-0803'].days

    assert days(day=16) == (date(2026, 10, 15), date(2026, 10, 16))
    assert days(day=18) == (date(2026, 10, 17),)
    assert days(day=20) == ()


def test_patterns_days_leave_out_skipped_dates(
    repo: SqliteTimetableRepository,
) -> None:
    """A date the trip is skipped on is not one of its days."""
    assert repo.db.path is not None
    with closing(sqlite3.connect(repo.db.path)) as conn:
        conn.execute(
            "INSERT INTO trip_skip VALUES ('bus-0803', '2026-10-16')"
        )
        conn.commit()
    found = repo.patterns(trip_ids=['bus-0803'], around=AROUND)
    assert found['bus-0803'].days == (date(2026, 10, 15),)


def test_patterns_partial_bundle_trip_shares_the_feed_days(
    tmp_path: Path,
) -> None:
    """A bundle ID covers the dates its feed twin runs as well as its own."""
    db = regional_db(tmp_path=tmp_path, days=2, trim=True)
    day = date(2026, 10, 17)
    with opened(path=db) as repo:
        found = repo.patterns(
            trip_ids=['NT31-bundle', 'NT31-feed'], around=day
        )
    assert found['NT31-bundle'].days == (AROUND, day)
    assert found['NT31-feed'].days == (AROUND,)
    assert found['NT31-bundle'].stops[-1].departs == 7 * 3600 + 40 * 60 + 30
    assert found['NT31-feed'].stops[-1].departs == 7 * 3600 + 40 * 60


def test_patterns_alias_runs_whenever_any_of_its_trips_do(
    slice_repo: SqliteTimetableRepository,
) -> None:
    """An alias of two trips takes the days of both, not just the first."""
    alias = 'NP43.947.121026.124.P.2.91437177'
    first = date(2026, 10, 16)
    second = date(2026, 10, 17)
    assert first in slice_repo.patterns(trip_ids=[alias], around=first)[
        alias
    ].days
    found = slice_repo.patterns(trip_ids=[alias], around=second)[alias]
    assert found.days == (first, second)
    assert found.stops
