"""Tests for a connection whose leg 2 stays on as the next trip."""

from collections.abc import Iterator
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Final

import pytest

from paperroute.adapters.timetable_db import SqliteTimetableRepository
from paperroute.app_connections import departures_read
from paperroute.build.gtfs_files import Source
from paperroute.build.timetable import Window, build_timetable
from paperroute.core.connection_model import pairs_of
from paperroute.core.messages import LoadDepartures
from paperroute.core.model import SYDNEY, Change, Pin, RouteKind
from tests.core.factories import live

REAL: Final[Path] = Path(__file__).parent / 'fixtures' / 'real'
DAY: Final[date] = date(2026, 10, 16)


@pytest.fixture(name='timetable', scope='module')
def _timetable(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[SqliteTimetableRepository]:
    """
    Build the 606 and a T1 that ends at Central and runs on as the next.

    Parameters
    ----------
    tmp_path_factory : pytest.TempPathFactory
        Temporary directories.

    Yields
    ------
    SqliteTimetableRepository
        The timetable.
    """
    out = tmp_path_factory.mktemp('carry') / 'timetable.db'
    build_timetable(
        sources=[
            Source(name='sydneytrains', archive=REAL / 'carry_on.zip'),
            Source(name='bundle', archive=REAL / 'bundle.zip'),
        ],
        window=Window(start=DAY, days=1),
        out=out,
    )
    with closing(SqliteTimetableRepository(path=out)) as timetable:
        yield timetable


def at(hour: int, minute: int) -> datetime:
    """
    A Sydney time on the fixture's day.

    Parameters
    ----------
    hour : int
        Hour.
    minute : int
        Minute.

    Returns
    -------
    datetime
        Aware Sydney time.
    """
    return datetime(
        DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=SYDNEY,
    )


def test_leg_two_follows_carry_on_to_town_hall(
    timetable: SqliteTimetableRepository,
) -> None:
    """606 to Parramatta, then the T1 that becomes a city trip at Central."""
    stop = timetable.stop
    pin = Pin(
        origin=stop(stop_id='2145615'),
        destination=stop(stop_id='200070'),
        kind=RouteKind.BUS,
        routes=('606',),
        change=Change(
            alight=stop(stop_id='2150119'),
            board=stop(stop_id='215020'),
            allow=5,
        ),
    )
    loaded = departures_read(
        timetable=timetable,
        effect=LoadDepartures(pin=pin, start=at(6, 0), end=at(8, 0)),
    )
    assert loaded.legs is not None
    pairs = pairs_of(
        first=[live(item=item) for item in loaded.departures],
        second=[live(item=item) for item in loaded.legs.second],
        direct=[live(item=item) for item in loaded.legs.direct],
        allow=5,
        now=at(6, 0),
    )
    trains = [
        pair.second.departure
        for pair in pairs
        if pair.second is not None and pair.second.departure.route == 'T1'
    ]
    assert [train.trip_id for train in trains] == [
        '169C.947.173.64.A.8.91436348',
    ]
    assert trains[0].departs.strftime('%H:%M') == '07:01'
