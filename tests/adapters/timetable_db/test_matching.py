"""Tests for timetable db: matching."""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.core.model import StopPlace
from tests.adapters.timetable_db.helpers import whole_stop


def test_match_stop_same_id_refreshes_name_and_point(
    network: SqliteTimetableRepository,
) -> None:
    """A place whose ID is still there comes back as the timetable has it."""
    stale = StopPlace(stop_id='B1', name='Old Name', point=None)
    assert network.match_stop(place=stale) == network.stop(stop_id='B1')


def moved_b1(
    *,
    network: SqliteTimetableRepository,
    east: float,
    name: str,
    north: float = 0.0,
) -> StopPlace:
    """
    A place with a retired ID, a distance east of B1.

    Parameters
    ----------
    network : SqliteTimetableRepository
        Network repository.
    east : float
        Metres east of B1.
    name : str
        Name of the place.
    north : float
        Metres north of B1.

    Returns
    -------
    StopPlace
        The place.
    """
    point = network.stop(stop_id='B1').point
    assert point is not None
    return StopPlace(
        stop_id='retired',
        name=name,
        point=(point[0] + east, point[1] + north),
    )


def test_match_stop_by_name_within_100_m(
    network: SqliteTimetableRepository,
) -> None:
    """A renamed ID 60 m away is found by its name."""
    place_60 = moved_b1(network=network, east=60.0, name='B1 Stop')
    assert network.match_stop(place=place_60) == network.stop(stop_id='B1')


def test_match_stop_too_far_or_renamed_is_none(
    network: SqliteTimetableRepository,
) -> None:
    """150 m away, 113 m diagonally, another name or no point gives None."""
    far = moved_b1(network=network, east=150.0, name='B1 Stop')
    corner = moved_b1(network=network, east=80.0, name='B1 Stop', north=80.0)
    renamed = moved_b1(network=network, east=60.0, name='B2 Stop')
    pointless = StopPlace(stop_id='retired', name='B1 Stop', point=None)
    for lost in (far, corner, renamed, pointless):
        assert network.match_stop(place=lost) is None


def edited_network(
    *, network_db: Path, tmp_path: Path, script: str,
) -> SqliteTimetableRepository:
    """
    Open a copy of the network timetable after running SQL on it.

    Parameters
    ----------
    network_db : Path
        Timetable file.
    tmp_path : Path
        Working directory.
    script : str
        SQL statements to run on the copy.

    Returns
    -------
    SqliteTimetableRepository
        Repository over the copy; the caller closes it.
    """
    copy = tmp_path / 'edited.db'
    shutil.copy(network_db, copy)
    with closing(sqlite3.connect(copy)) as conn:
        conn.executescript(script)
        conn.commit()
    return SqliteTimetableRepository(
        path=copy,
        city=SYDNEY,
    )


def test_no_destination_skips_no_pickup_and_skipped_trips(
    network_db: Path, tmp_path: Path,
) -> None:
    """ew1 has no pickup at C and we1 is skipped on the 6th."""
    script = (
        "UPDATE stop_times SET pickup = 1 "
        "WHERE trip_id = 'ew1' AND stop_id = 'C_1';"
        "INSERT INTO trip_skip VALUES ('we1', '2026-10-06');"
    )
    with closing(
        edited_network(network_db=network_db, tmp_path=tmp_path, script=script)
    ) as repo:
        found = whole_stop(repo=repo, origin='C', hours=(7, 7))
    assert [d.trip_id for d in found] == ['er1', 'cc1', 're1', 'y1', 'cc1']


def test_departures_at_the_same_time_sort_by_trip(
    network_db: Path, tmp_path: Path,
) -> None:
    """A late trip from the day before at ew1's time sorts after ew1."""
    late = 31 * 3600 + 6 * 60  # 07:06 the next morning
    script = (
        "INSERT INTO trips SELECT 'zz', route_id, service_id, headsign, "
        'direction_id, block_id, agency_id, source, shape_id, service '
        "FROM trips WHERE trip_id = 'ew1';"
        "INSERT INTO stop_times VALUES "
        f"('zz', 1, 'C_1', {late}, {late}, 0, 0, NULL),"
        f"('zz', 2, 'J_1', {late + 180}, {late + 180}, 0, 0, NULL);"
    )
    with closing(
        edited_network(network_db=network_db, tmp_path=tmp_path, script=script)
    ) as repo:
        found = whole_stop(repo=repo, origin='C', hours=(7, 7))
    assert [(d.trip_id, d.service_day.day) for d in found[:2]] == [
        ('ew1', 6),
        ('zz', 5),
    ]
