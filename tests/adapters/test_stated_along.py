"""Tests for the distances a timetable states for each call."""

from contextlib import closing
from datetime import date
from pathlib import Path

from paperroute_sydney.city import SYDNEY
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.build.gtfs_files import Source
from tests.build.feeds import build_db, measured_bus_feed
from tests.helpers import schema_ten


def measured_db(*, tmp_path: Path) -> Path:
    """
    Load the measured bus feed into a new timetable.

    Parameters
    ----------
    tmp_path : Path
        Directory for the zip and the database.

    Returns
    -------
    Path
        The database file.
    """
    return build_db(
        tmp_path=tmp_path, start=date(2026, 10, 15), days=3,
        sources=[Source(name='bundle',
                        archive=measured_bus_feed(path=tmp_path / 'm.zip'))],
    )


def stated(*, path: Path) -> list[float | None]:
    """
    Read the stated distances of bus-0803's calls.

    Parameters
    ----------
    path : Path
        Timetable file.

    Returns
    -------
    list[float | None]
        One per call, in order.
    """
    with closing(SqliteTimetableRepository(path=path, city=SYDNEY)) as repo:
        found = repo.patterns(trip_ids={'bus-0803'}, around=date(2026, 10, 16))
    return [stop.along for stop in found['bus-0803'].stops]


def test_patterns_carry_the_stated_distances(tmp_path: Path) -> None:
    """Each call carries its stored distance as metres."""
    assert stated(path=measured_db(tmp_path=tmp_path)) == [0.0, 5587.0]


def test_a_schema_ten_file_states_no_distances(tmp_path: Path) -> None:
    """A file built before the column still loads, with no distances."""
    path = measured_db(tmp_path=tmp_path)
    schema_ten(path=path)
    assert stated(path=path) == [None, None]
