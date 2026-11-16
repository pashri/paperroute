"""Tests that replay the recorded BODS feeds."""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from paperroute_cambridge.adapters.bods_positions import named, parse_positions
from paperroute_cambridge.adapters.siri_vm import coach_sightings
from paperroute_cambridge.area import CAMBRIDGE_PROJECTION
from paperroute_cambridge.cambridge_feeds import POSITIONS_FEED
from paperroute_cambridge.city import CAMBRIDGE
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_effects import labelled_positions
from paperroute_transit.core.positions import (
    PARKED,
    ParkedAge,
    PositionsSnapshot,
    without_parked,
)
from tests.build.timetable_rows import all_trip_ids, short_names
from tests.conftest import FIXTURES, RECORDED

RECORDED_AT = datetime.fromisoformat(str(RECORDED['at']))


def read(name: str) -> bytes:
    """
    Read a recorded fixture.

    Parameters
    ----------
    name : str
        File name.

    Returns
    -------
    bytes
        Its contents.
    """
    return (FIXTURES / name).read_bytes()


def parse(body: bytes) -> PositionsSnapshot:
    """
    Read a positions body at the recording's time.

    Parameters
    ----------
    body : bytes
        The body.

    Returns
    -------
    PositionsSnapshot
        The vehicles.
    """
    return parse_positions(
        data=body, fetched_at=RECORDED_AT, projection=CAMBRIDGE_PROJECTION,
    )


SIGHTINGS = [
    found
    for name in ('siri_natx.xml', 'siri_flix.xml')
    for found in coach_sightings(data=read(name))
]


def test_recorded_join_matches_the_raw_bodies() -> None:
    """Recorded join matches the raw bodies."""
    joined = parse(named(data=read('bods_positions_raw.pb'), sightings=SIGHTINGS))
    assert joined == parse(read('bods_positions.pb'))
    assert any(v.label and not v.trip_id for v in joined.vehicles)


def test_recorded_positions_label_by_trip_by_route_and_by_coach(
    built: sqlite3.Connection,
    built_path: Path,
) -> None:
    """Recorded positions label by trip by route and by coach."""
    with closing(
        SqliteTimetableRepository(city=CAMBRIDGE, path=built_path),
    ) as timetable:
        snapshot = labelled_positions(
            timetable=timetable,
            parsers=CAMBRIDGE.feeds.parsers(feed=POSITIONS_FEED),
            data=read('bods_positions.pb'),
            fetched_at=RECORDED_AT,
        )
    stored = all_trip_ids(built)
    by_trip = [v for v in snapshot.vehicles if v.trip_id in stored]
    by_route = [
        v for v in snapshot.vehicles if v.trip_id and v.trip_id not in stored
    ]
    by_coach = [v for v in snapshot.vehicles if not v.trip_id]
    assert by_trip and all(v.label in short_names(built) for v in by_trip)
    assert by_route and {v.label for v in by_route} == {RECORDED['by_route']}
    assert by_coach and {v.label for v in by_coach} <= {
        s.line for s in SIGHTINGS
    }


def test_recorded_positions_lose_parked_vehicles_at_ten_minutes() -> None:
    """Recorded positions lose parked vehicles at ten minutes."""
    snapshot = parse(read('bods_positions.pb'))
    kept = without_parked(snapshot=snapshot, limit=PARKED[ParkedAge.TEN])
    assert 0 < len(kept.vehicles) < len(snapshot.vehicles)
    assert all(
        snapshot.header_time - v.timestamp <= 600
        for v in kept.vehicles
        if v.timestamp
    )
