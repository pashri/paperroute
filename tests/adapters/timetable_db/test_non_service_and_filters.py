"""Tests for timetable db: non service and filters."""

import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path

from paperroute_sydney.city import SydneyMode
from paperroute_testkit.core.factories import pin_of, place
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.core.model import Pin
from tests.adapters.timetable_db.helpers import (
    MONDAY,
    at,
    c_departures,
    opened,
    p_departures,
    platform_repo,
)
from tests.helpers import schema_five, schema_four


def empty_running_repo(*, tmp_path: Path) -> SqliteTimetableRepository:
    """
    Build P to Q with one revenue trip and one out-of-service trip.

    Parameters
    ----------
    tmp_path : Path
        Working directory.

    Returns
    -------
    SqliteTimetableRepository
        Repository over the built file; the caller closes it.
    """
    calls = [('P_1', '08:00:00'), ('Q', '08:10:00')]
    repo = platform_repo(
        tmp_path=tmp_path,
        trips=[('revenue', 'Q', calls), ('empty', 'Q', calls)],
    )
    with closing(sqlite3.connect(tmp_path / 'timetable.db')) as conn:
        conn.execute(
            "UPDATE trips SET service = 0 WHERE trip_id = 'empty'",
        )
        conn.commit()
    return repo


def test_departures_skip_non_service(tmp_path: Path) -> None:
    """An out-of-service trip is not listed, with or without a Destination."""
    with closing(empty_running_repo(tmp_path=tmp_path)) as repo:
        for destination in (None, 'Q'):
            assert p_departures(repo=repo, destination=destination) == [
                ('revenue', at(hour=8, minute=0)),
            ]


def test_route_labels_include_non_service(tmp_path: Path) -> None:
    """The Map still labels an out-of-service trip."""
    with closing(empty_running_repo(tmp_path=tmp_path)) as repo:
        found = repo.route_labels(trip_ids={'empty'})
    assert found == {'empty': 'T1'}


def test_patterns_include_non_service(tmp_path: Path) -> None:
    """The Map still gets the pattern of an out-of-service trip."""
    with closing(empty_running_repo(tmp_path=tmp_path)) as repo:
        found = repo.patterns(trip_ids={'empty'}, around=MONDAY)
    assert found['empty'].route == 'T1'
    assert len(found['empty'].stops) == 2


def test_departures_on_schema_four_file(tmp_path: Path) -> None:
    """A file without the service column still gives departures."""
    empty_running_repo(tmp_path=tmp_path).close()
    schema_four(path=tmp_path / 'timetable.db')
    with opened(path=tmp_path / 'timetable.db') as old:
        for destination in (None, 'Q'):
            assert [
                trip
                for trip, _ in p_departures(repo=old, destination=destination)
            ] == ['empty', 'revenue']


def e2_to_w1(*, repo: SqliteTimetableRepository) -> list[tuple[str, bool]]:
    """
    The network's departures from E2 to W1 on 6 October, 7 to 9 am.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to query.

    Returns
    -------
    list[tuple[str, bool]]
        Trip ID and whether it carries on into another trip, in order.
    """
    found = repo.departures(
        pin=pin_of('E2', 'W1'),
        start=at(hour=7, minute=0, day=6),
        end=at(hour=8, minute=59, day=6),
    )
    return [(d.trip_id, d.facts.onto is not None) for d in found]


def test_departures_add_carry_ons(
    network: SqliteTimetableRepository,
) -> None:
    """x1 reaches W1 only by running on as x2."""
    assert ('x1', True) in e2_to_w1(repo=network)


def test_departures_without_trip_joins(
    network_db: Path, tmp_path: Path,
) -> None:
    """A schema-5 file lists only direct trips, without an error."""
    copy = tmp_path / 'timetable.db'
    shutil.copy(network_db, copy)
    schema_five(path=copy)
    with opened(path=copy) as repo:
        found = e2_to_w1(repo=repo)
    assert found
    assert all(not onto for _, onto in found)
    assert 'x1' not in [trip for trip, _ in found]


def routes_of(
    *,
    repo: SqliteTimetableRepository,
    pin: Pin,
) -> set[str]:
    """
    Route names of a pin's departures on 6 October.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Repository to query.
    pin : Pin
        The pin.

    Returns
    -------
    set[str]
        Route short names.
    """
    return {
        departure.route
        for departure in repo.departures(
            pin=pin,
            start=at(hour=0, minute=0, day=6),
            end=at(hour=23, minute=59, day=6),
        )
    }


def test_departures_respect_filter(
    network: SqliteTimetableRepository,
) -> None:
    """A route filter narrows the board; an empty one keeps both lines."""
    pin = Pin(
        origin=place('C'),
        kind=SydneyMode.TRAIN,
        routes=('T1',),
        only=('T2',),
    )
    assert routes_of(repo=network, pin=pin) == {'T2'}
    assert routes_of(repo=network, pin=replace(pin, only=())) == {'T1', 'T2'}


def test_filter_with_destination(
    network: SqliteTimetableRepository,
) -> None:
    """From C to J, the filter keeps only the chosen line."""
    pin = Pin(
        origin=place('C'),
        destination=place('J'),
        kind=SydneyMode.TRAIN,
        routes=(),
    )
    assert routes_of(repo=network, pin=pin) == {'T1', 'T2'}
    only = replace(pin, only=('T1',))
    assert routes_of(repo=network, pin=only) == {'T1'}


def test_filter_matches_carry_on_by_boarding_route(
    network: SqliteTimetableRepository,
) -> None:
    """The 809 carrying on as the 811 is kept by 809, dropped by 811."""
    pin = pin_of('B1', 'B2')
    assert routes_of(repo=network, pin=pin) == {'809'}
    kept = replace(pin, only=('809',))
    assert routes_of(repo=network, pin=kept) == {'809'}
    dropped = replace(pin, only=('811',))
    assert routes_of(repo=network, pin=dropped) == set()


def test_departures_carry_their_route_id(
    network: SqliteTimetableRepository,
) -> None:
    """Every departure names the timetable route its trip runs on."""
    to_w2, anywhere = c_departures(repo=network)
    carried = network.departures(
        pin=pin_of('B1', 'B2'),
        start=at(hour=9, minute=0, day=6),
        end=at(hour=10, minute=0, day=6),
    )
    assert {d.facts.route_id for d in (*to_w2, *anywhere)} >= {
        'sydneytrains:T1',
    }
    assert all(
        d.facts.route_id.endswith(d.route) for d in (*anywhere, *carried)
    )
    assert carried


def test_leg_lines_name_every_route_between_two_stops(
    network: SqliteTimetableRepository,
) -> None:
    """Every route with a trip calling at C and then W2 that day counts."""
    found = network.leg_lines(ends=('C', 'W2'), day=date(2026, 10, 6))
    assert 'sydneytrains:T1' in found
    assert network.leg_lines(
        ends=('W2', 'W2'), day=date(2026, 10, 6),
    ) == frozenset()


def test_leg_lines_without_joins(
    old_network: SqliteTimetableRepository,
) -> None:
    """A timetable without trip joins still names the direct lines."""
    assert 'sydneytrains:T1' in old_network.leg_lines(
        ends=('C', 'W2'), day=date(2026, 10, 6),
    )


def test_leg_lines_repeat_ask_does_not_query_again(
    network: SqliteTimetableRepository,
) -> None:
    """The same ends and day are answered from memory the second time."""
    day = date(2026, 10, 6)
    first = network.leg_lines(ends=('C', 'W2'), day=day)
    queries: list[str] = []
    fetch = network.fetch

    def counting(
        *, sql: str, params: tuple[object, ...] | dict[str, object],
    ) -> list[tuple[object, ...]]:
        queries.append(sql)
        return fetch(sql=sql, params=params)

    network.fetch = counting  # type: ignore[method-assign]
    assert network.leg_lines(ends=('C', 'W2'), day=day) == first
    assert not queries
    network.leg_lines(ends=('C', 'W2'), day=date(2026, 10, 7))
    assert queries
