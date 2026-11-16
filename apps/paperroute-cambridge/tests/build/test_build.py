"""Tests for the timetable built from the recorded BODS zip."""

import math
import sqlite3
from contextlib import closing
from pathlib import Path

from paperroute_cambridge.area import CAMBRIDGE_PROJECTION
from paperroute_cambridge.city import CAMBRIDGE, COACH_AGENCIES, DRUMMER_STREET
from paperroute_transit.adapters.network_db import SqliteNetworkRepository
from paperroute_transit.core.chooser import ChooserPath, Level
from paperroute_transit.core.geo import to_metres
from tests.build.timetable_rows import (
    agency_ids,
    call_count,
    flags,
    parents,
    route_rows,
    shape_ids,
    short_names,
    stop_row,
    trips_of,
    zip_call_count,
)
from tests.conftest import FIXTURES, RECORDED

NEAR_ROUTES = {'PR2', '4', 'A', 'U1', '777', 'UK083'}


def path_to(*, kind: str) -> ChooserPath:
    """
    Make the chooser path with a kind chosen.

    Parameters
    ----------
    kind : str
        The kind's key.

    Returns
    -------
    ChooserPath
        The path.
    """
    return ((Level.KIND, kind),)


def test_build_keeps_only_the_routes_near_cambridge(
    built: sqlite3.Connection,
) -> None:
    """Build keeps only the routes near cambridge."""
    assert short_names(built) == NEAR_ROUTES
    assert RECORDED['far_route'] not in short_names(built)


def test_build_classifies_buses_and_coaches(
    built: sqlite3.Connection,
) -> None:
    """Build classifies buses and coaches."""
    rows = route_rows(built)
    assert rows
    kinds = {
        short: CAMBRIDGE.classify(
            route_type=kind, agency_id=agency, short_name=short,
        ).kind
        for short, kind, agency in rows
    }
    assert kinds == {
        'PR2': 'bus',
        '4': 'bus',
        'A': 'bus',
        'U1': 'bus',
        '777': 'coach',
        'UK083': 'coach',
    }


def test_build_cuts_each_coach_trip_one_call_past_the_box(
    built: sqlite3.Connection,
) -> None:
    """Build cuts each coach trip one call past the box."""
    trips = trips_of(built, shorts=('777', 'UK083'))
    assert trips
    for trip in trips:
        outside = flags(built, trip)
        assert len(outside) >= 2 and not any(outside[:-1])


def test_build_keeps_local_routes_whole(built: sqlite3.Connection) -> None:
    """Build keeps local routes whole."""
    trips = trips_of(built, shorts=('PR2', '4', 'A', 'U1'))
    assert trips
    for trip in trips:
        assert call_count(built, trip) == zip_call_count(
            FIXTURES / 'bods.zip', trip,
        )


def test_build_groups_drummer_street(built: sqlite3.Connection) -> None:
    """Build groups drummer street."""
    children = [
        stop
        for stop, parent in parents(built).items()
        if parent == 'cambridge-drummer-street'
    ]
    assert len(children) >= 2
    _, lon, lat, _ = stop_row(built, 'cambridge-drummer-street')
    place = to_metres(lon=lon, lat=lat, projection=CAMBRIDGE_PROJECTION)
    assert DRUMMER_STREET.point is not None
    assert math.dist(place, DRUMMER_STREET.point) < 100


def test_build_leaves_whippet_trips_without_shapes(
    built: sqlite3.Connection,
) -> None:
    """Build leaves whippet trips without shapes."""
    assert trips_of(built, shorts=('U1',))
    assert shape_ids(built, shorts=('U1',)) == {None}


def test_fixture_agencies_are_the_coach_agencies() -> None:
    """Fixture agencies are the coach agencies."""
    found = agency_ids(FIXTURES / 'bods.zip', nocs=('NATX', 'FLIX'))
    assert found
    assert found == COACH_AGENCIES


def test_chooser_offers_bus_and_coach_with_no_line_mode(
    built_path: Path,
) -> None:
    """Chooser offers bus and coach with no line mode."""
    with closing(
        SqliteNetworkRepository(city=CAMBRIDGE, path=built_path),
    ) as network:
        kinds = network.kind_column()
        coach = network.next_column(path=path_to(kind='coach'))
    assert [choice.label for choice in kinds.items] == ['Bus', 'Coach']
    assert not any(spec.by_line for spec in CAMBRIDGE.modes)
    assert coach is not None
    assert {choice.badge for choice in coach.items} >= {'777', 'UK083'}

