"""Tests for timetable db: carry on."""

import dataclasses
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from paperroute_sydney.city import SYDNEY_ZONE, SydneyMode
from paperroute_testkit.core.factories import pin_of
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.core.model import CarryOn, TripFacts
from tests.adapters.timetable_db.helpers import (
    REAL_DB,
    at,
    bundle_db,
    c_departures,
    opened,
    require_real_monday,
    whole_stop,
)
from tests.helpers import schema_three


def test_departures_carry_their_kind(
    network: SqliteTimetableRepository,
) -> None:
    """T1 departures are Train, with or without a Destination."""
    to_w2, anywhere = c_departures(repo=network)
    assert to_w2[0].facts.kind == SydneyMode.TRAIN
    assert anywhere[0].facts.kind == SydneyMode.TRAIN


def test_departures_carry_on_as_the_next_route(
    network: SqliteTimetableRepository,
    old_network: SqliteTimetableRepository,
) -> None:
    """v1 carries on as the 811; an old timetable doesn't know."""
    then = CarryOn(route='811', headsign='Parramatta Stn', trip_id='v2')
    for pin in (pin_of('B1', None), pin_of('B1', 'B2')):
        found = network.departures(
            pin=pin,
            start=at(hour=9, minute=0, day=6),
            end=at(hour=10, minute=0, day=6),
        )
        assert [(d.trip_id, d.facts) for d in found] == [(
            'v1',
            TripFacts(
                kind=SydneyMode.BUS, then=then, route_id='sydneytrains:809',
            ),
        )]
    old = old_network.departures(
        pin=pin_of('B1', None),
        start=at(hour=9, minute=0, day=6),
        end=at(hour=10, minute=0, day=6),
    )
    assert [(d.trip_id, d.facts.kind, d.facts.then) for d in old] == [
        ('v1', SydneyMode.BUS, None),
    ]


def test_carry_on_without_next_trip_column(
    network_db: Path, tmp_path: Path,
) -> None:
    """A schema-3 file still carries v1 on as the 811, with no trip."""
    copy = tmp_path / 'timetable.db'
    shutil.copy(network_db, copy)
    schema_three(path=copy)
    with opened(path=copy) as repo:
        found = repo.departures(
            pin=pin_of('B1', None),
            start=at(hour=9, minute=0, day=6),
            end=at(hour=10, minute=0, day=6),
        )
    assert [(d.trip_id, d.facts.then) for d in found] == [
        ('v1', CarryOn(route='811', headsign='Parramatta Stn', trip_id='')),
    ]


def test_old_schema_lists_the_whole_stop(
    network: SqliteTimetableRepository,
    old_network: SqliteTimetableRepository,
) -> None:
    """A schema-2 file lists the same whole stop, without carry-ons."""
    new = whole_stop(repo=network, origin='C', hours=(0, 23))
    old = whole_stop(repo=old_network, origin='C', hours=(0, 23))
    assert old == [
        dataclasses.replace(
            departure,
            facts=dataclasses.replace(departure.facts, then=None),
        )
        for departure in new
    ]
    assert len(old) > 7


def real_809_stops() -> list[str]:
    """
    Find Merrylands Rd Near Bambil St in the real timetable.

    Skips the test when the file is missing, has no run tables or does not
    cover Monday 19 October 2026.

    Returns
    -------
    list[str]
        Stop IDs with that name.
    """
    require_real_monday()
    with closing(sqlite3.connect(REAL_DB)) as conn:
        stops = conn.execute(
            'SELECT stop_id FROM stops WHERE name = ?',
            ('Merrylands Rd Near Bambil St',),
        ).fetchall()
    return [str(stop) for stop, in stops]


def test_809_carries_on_as_811_at_merrylands() -> None:
    """20 of the 46 809s carry on as the 811; the 9:04 and 15:29 don't."""
    stops = real_809_stops()
    with opened(path=REAL_DB) as repo:
        found = [
            departure
            for stop in stops
            for departure in repo.departures(
                pin=pin_of(stop, None),
                start=datetime(2026, 10, 19, tzinfo=SYDNEY_ZONE),
                end=datetime(2026, 10, 19, 23, 59, 59, tzinfo=SYDNEY_ZONE),
            )
            if departure.route == '809'
        ]
    carried = [
        d
        for d in found
        if d.facts.then is not None
        and (d.facts.then.route, d.facts.then.headsign)
        == ('811', 'Parramatta Stn')
    ]
    on = {d.departs.strftime('%H:%M') for d in carried}
    assert len(found) == 46
    assert len(carried) == 20
    assert all(d.facts.then and d.facts.then.trip_id for d in carried)
    assert not on & {'09:04', '15:29'}
    assert {'09:04', '15:29'} <= {d.departs.strftime('%H:%M') for d in found}


def test_version_reads_built_at(
    network: SqliteTimetableRepository, network_db: Path, tmp_path: Path,
) -> None:
    """The version is the build time; a file without one has None."""
    with closing(sqlite3.connect(network_db)) as conn:
        built_at = conn.execute(
            "SELECT value FROM meta WHERE key = 'built_at'",
        ).fetchone()[0]
    assert network.version() == built_at
    bare = bundle_db(tmp_path=tmp_path, days=1)
    with opened(path=bare) as repo:
        assert repo.version() is None
