"""Tests for reading the stations between a pin's From and To."""

import logging
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Final

import pytest

from paperroute.core.alert_model import AlertPlaces, BetweenLoaded, LoadBetween
from paperroute.core.model import Mode, RouteKind
from paperroute.core.patterns import PatternStop, TripPattern
from paperroute_testkit.app_factories import ports_of, posted_by
from paperroute_testkit.core.factories import pin_of
from paperroute_testkit.fakes import EmptyPlacedTimetable

STRATHFIELD_PLATFORM: Final[str] = '2135171'


def stopping(*, stations: str) -> TripPattern:
    """
    A trip calling at one-letter stations named after themselves.

    Parameters
    ----------
    stations : str
        Station IDs, in running order.

    Returns
    -------
    TripPattern
        The pattern.
    """
    return TripPattern(
        trip_id='trip-0',
        route='T1',
        mode=Mode.TRAIN,
        shape_id=None,
        stops=tuple(
            PatternStop(arrives=0, departs=0, point=None, station=station)
            for station in stations
        ),
        days=(),
    )


@dataclass
class BetweenTimetable(EmptyPlacedTimetable):
    """A timetable with one pattern and fixed stop families."""

    families: dict[str, frozenset[str]] = field(default_factory=dict)
    error: sqlite3.Error | None = None
    trainlink: bool = False
    trainlink_asked: set[str] = field(default_factory=set)

    def serves_trainlink(self, *, stop_ids: Collection[str]) -> bool:
        """
        Record the stops asked about and give the fixed answer.

        Parameters
        ----------
        stop_ids : Collection[str]
            Stop IDs.

        Returns
        -------
        bool
            The `trainlink` field.
        """
        self.trainlink_asked.update(stop_ids)
        return self.trainlink

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Give a pattern from S through B and C to T, or fail.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.
        around : date
            Ignored.

        Returns
        -------
        dict[str, TripPattern]
            The pattern under 'trip-0'.

        Raises
        ------
        sqlite3.Error
            When `error` is set.
        """
        if self.error is not None:
            raise self.error
        stops = stopping(stations='SBCT')
        named = tuple(
            replace(stop, name=f'{stop.station} Station')
            for stop in stops.stops
        )
        return {'trip-0': replace(stops, stops=named)}

    def alert_places(
        self, *, route_ids: Collection[str], stop_ids: Collection[str]
    ) -> AlertPlaces:
        """
        Give the fixed families of the stops asked about.

        Parameters
        ----------
        route_ids : Collection[str]
            Ignored.
        stop_ids : Collection[str]
            Stop IDs.

        Returns
        -------
        AlertPlaces
            The known families.
        """
        stops = {
            stop: self.families[stop]
            for stop in stop_ids
            if stop in self.families
        }
        return AlertPlaces(routes={}, stops=stops)


async def test_between_from_a_platform() -> None:
    """A From on a platform is read as its station."""
    pin = pin_of(STRATHFIELD_PLATFORM, 'T')
    timetable = BetweenTimetable(
        families={STRATHFIELD_PLATFORM: frozenset({STRATHFIELD_PLATFORM, 'S'})}
    )
    posted = await posted_by(
        ports=ports_of(timetable=timetable),
        effects=[
            LoadBetween(
                pin=pin,
                trip_ids=frozenset({'trip-0'}),
                day=date(2026, 10, 24),
            ),
        ],
    )
    assert posted == [
        BetweenLoaded(
            pin=pin,
            names=frozenset({'B', 'C'}),
            families={
                STRATHFIELD_PLATFORM: frozenset({STRATHFIELD_PLATFORM, 'S'}),
            },
        ),
    ]


async def test_between_reads_trainlink() -> None:
    """The TrainLink answer covers every member of each end's family."""
    pin = pin_of(STRATHFIELD_PLATFORM, 'T')
    families = {
        STRATHFIELD_PLATFORM: frozenset({STRATHFIELD_PLATFORM, 'S'}),
    }
    timetable = BetweenTimetable(families=families, trainlink=True)
    posted = await posted_by(
        ports=ports_of(timetable=timetable),
        effects=[
            LoadBetween(
                pin=pin,
                trip_ids=frozenset({'trip-0'}),
                day=date(2026, 10, 24),
            ),
        ],
    )
    assert posted == [
        BetweenLoaded(
            pin=pin,
            names=frozenset({'B', 'C'}),
            families=families,
            trainlink=True,
        ),
    ]
    assert timetable.trainlink_asked == {STRATHFIELD_PLATFORM, 'S', 'T'}


async def test_between_read_failure_is_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed patterns read posts no names and logs one error."""
    pin = pin_of('S', 'T')
    timetable = BetweenTimetable(error=sqlite3.OperationalError('disk I/O'))
    with caplog.at_level(logging.ERROR):
        posted = await posted_by(
            ports=ports_of(timetable=timetable),
            effects=[
                LoadBetween(
                    pin=pin,
                    trip_ids=frozenset({'trip-0'}),
                    day=date(2026, 10, 24),
                    timetable='v',
                ),
            ],
        )
    assert posted == [
        BetweenLoaded(pin=pin, names=frozenset(), timetable='v'),
    ]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1


async def test_bus_pin_skips_trainlink() -> None:
    """A pin of a kind other than train never asks about TrainLink."""
    pin = replace(pin_of('S', 'T'), kind=RouteKind.BUS)
    timetable = BetweenTimetable(trainlink=True)
    posted = await posted_by(
        ports=ports_of(timetable=timetable),
        effects=[
            LoadBetween(
                pin=pin, trip_ids=frozenset(), day=date(2026, 10, 24),
            ),
        ],
    )
    assert posted == [BetweenLoaded(pin=pin, names=frozenset())]
    assert not timetable.trainlink_asked
