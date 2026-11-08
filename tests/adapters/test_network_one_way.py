"""Tests for the TO column on stations a train only stops at one way."""

from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.network_db import SqliteNetworkRepository
from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.build.timetable import Window, build_timetable
from paperroute.city import SYDNEY, SYDNEY_PROJECTION, SYDNEY_ZONE, SydneyMode
from paperroute.core.chooser import Column, Level
from paperroute_testkit.build.feed_zip import write_feed
from paperroute_transit.build.gtfs_files import Source
from paperroute_transit.core.model import Pin, StopPlace
from tests.adapters.test_network_db import (
    T1,
    build_network,
    merge_e2_into_r3,
    set_runs,
)
from tests.build.network_feed import network_files
from tests.helpers import schema_five

DAY: Final[date] = date(2026, 10, 5)
ONE_WAY: Final[dict[tuple[str, str], tuple[str, str]]] = {
    **{(f'we{n}', 'E1_1'): ('0', '1') for n in range(1, 7)},
    ('ew1', 'J_1'): ('1', '0'),
    ('x2', 'J_1'): ('1', '0'),
}


@pytest.fixture(name='copy')
def _copy(tmp_path: Path) -> Path:
    """
    Build the network timetable for a test to change.

    Parameters
    ----------
    tmp_path : Path
        Working directory.

    Returns
    -------
    Path
        Timetable file.
    """
    return build_network(folder=tmp_path)


def to_column(*, path: Path, origin: str) -> Column:
    """
    Read the T1 TO column from a station.

    Parameters
    ----------
    path : Path
        Timetable file.
    origin : str
        From station ID.

    Returns
    -------
    Column
        The column.
    """
    with closing(SqliteNetworkRepository(
        path=path,
        city=SYDNEY,
    )) as repo:
        column = repo.choices(path=(*T1, (Level.STATION, origin)))
    assert column is not None
    return column


def dim_keys(*, column: Column) -> set[str]:
    """
    List the dim station keys of a column.

    Parameters
    ----------
    column : Column
        TO column.

    Returns
    -------
    set[str]
        Keys of dim rows.
    """
    return {choice.key for choice in column.items if choice.dim}


def test_pickup_only_to_is_grey(copy: Path) -> None:
    """A run that only picks up at E2 leaves E2 grey from W2."""
    set_runs(path=copy, runs=[('W2 W1 J C E1 +E2', 4)])
    column = to_column(path=copy, origin='W2')
    assert dim_keys(column=column) == {'R1', 'R2', 'R3', 'E2'}


def test_pickup_only_from_reaches_nothing(copy: Path) -> None:
    """From C, which the only run only sets down at, all is grey."""
    set_runs(path=copy, runs=[('W2 W1 J -C E1 E2', 4)])
    column = to_column(path=copy, origin='C')
    assert {c.key for c in column.items if not c.dim} == {'', 'C'}


def test_alias_counts_any_member(copy: Path) -> None:
    """R3 is reached when its merged E2 call lets people off."""
    merge_e2_into_r3(path=copy)
    set_runs(path=copy, runs=[('W2 W1 J +R3 -E2', 4)])
    column = to_column(path=copy, origin='W2')
    assert dim_keys(column=column) == {'R1', 'R2', 'C', 'E1'}


def test_to_column_on_schema_five_file(copy: Path) -> None:
    """A schema-5 file counts every stop both ways, as before."""
    today = to_column(path=copy, origin='W2')
    schema_five(path=copy)
    assert to_column(path=copy, origin='W2') == today
    set_runs(path=copy, runs=[('W2 W1 J C E1 +E2', 4)])
    column = to_column(path=copy, origin='W2')
    assert dim_keys(column=column) == {'R1', 'R2', 'R3'}


def one_way_network(*, folder: Path) -> Path:
    """
    Build the network feed with some calls only one way.

    Parameters
    ----------
    folder : Path
        Directory to build in.

    Returns
    -------
    Path
        Timetable file.
    """
    files = network_files()
    for row in files['stop_times.txt']:
        flags = ONE_WAY.get((row['trip_id'], row['stop_id']))
        if flags is not None:
            row['pickup_type'], row['drop_off_type'] = flags
    out = folder / 'timetable.db'
    archive = write_feed(path=folder / 'network.zip', files=files)
    build_timetable(
        sources=[Source(name='sydneytrains', archive=archive)],
        window=Window(start=DAY, days=3), out=out,
        city=SYDNEY,
    )
    return out


def has_departures(
    *, repo: SqliteTimetableRepository, origin: str, to: str,
) -> bool:
    """
    Say whether any train runs from one station to another on DAY.

    Parameters
    ----------
    repo : SqliteTimetableRepository
        Timetable.
    origin : str
        From station ID.
    to : str
        Destination station ID.

    Returns
    -------
    bool
        True when some departure exists.
    """
    pin = Pin(
        origin=StopPlace(stop_id=origin, name=origin, point=None),
        destination=StopPlace(stop_id=to, name=to, point=None),
        kind=SydneyMode.TRAIN,
    )
    return bool(repo.departures(
        pin=pin, start=datetime(2026, 10, 5, tzinfo=SYDNEY_ZONE),
        end=datetime(2026, 10, 5, 23, 59, 59, tzinfo=SYDNEY_ZONE),
    ))


def test_every_bright_to_has_a_departure(tmp_path: Path) -> None:
    """With one-way calls, a To not grey always has a train that day."""
    path = one_way_network(folder=tmp_path)
    columns = {
        origin: to_column(path=path, origin=origin)
        for origin in ('W2', 'W1', 'J', 'C', 'E1', 'E2', 'R1', 'R3')
    }
    assert 'E1' in dim_keys(column=columns['W2'])
    with closing(
        SqliteTimetableRepository(
            path=path,
            city=SYDNEY,
        ),
    ) as repo:
        missing = [
            (origin, choice.key)
            for origin, column in columns.items()
            for choice in column.items
            if not (choice.heading or choice.dim or choice.tag)
            and not has_departures(repo=repo, origin=origin, to=choice.key)
        ]
    assert not missing
