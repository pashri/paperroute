"""Tests for app: map data."""

import logging
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pytest

from paperroute_sydney.city import SYDNEY, SydneyFeed
from paperroute_testkit.core.factories import pin_of, place
from paperroute_testkit.fakes import (
    STOP_POINT,
    BrokenTimetable,
    BuiltTimetable,
    PlacedTimetable,
)
from paperroute_transit.adapters.timetable_db import SqliteTimetableRepository
from paperroute_transit.app_effects import loaded_patterns
from paperroute_transit.core.feed_plan import FeedKind
from paperroute_transit.core.geo import Shape, make_shape
from paperroute_transit.core.messages import (
    FeedFailed,
    FetchFeed,
    LoadPatterns,
    MapData,
    MapDataFailed,
    PositionsFetched,
)
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.places import shown
from paperroute_transit.core.tiles import TileVariant
from paperroute_transit.measures import StopMeasures
from tests.app.helpers import (
    BUS_POSITIONS,
    CountingTiles,
    CrashingFeeds,
    PressingDisplay,
    _build_db,
    run_sydney,
    started,
)
from tests.app_effects.helpers import kit_ports
from tests.app_factories import AT, MAP_PIN, ports_of, posted_by


class RelabellingTimetable(SqliteTimetableRepository):
    """The real timetable, with the 606 bus's trip renamed 606X."""

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Label trip 2706990 '606X' when asked for it.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.

        Returns
        -------
        dict[str, str]
            '606X' for trip 2706990 when asked; nothing else.
        """
        return {'2706990': '606X'} if '2706990' in trip_ids else {}


async def test_positions_are_labelled_from_the_timetable(
    tmp_path: Path,
) -> None:
    """Vehicles take the route label the timetable gives their trip."""
    timetable = RelabellingTimetable(
        path=_build_db(tmp_path),
        city=SYDNEY,
    )
    try:
        posted = await posted_by(
            ports=ports_of(timetable=timetable),
            effects=[FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS)],
        )
    finally:
        timetable.close()
    first = posted[0]
    assert isinstance(first, PositionsFetched)
    labels = {
        vehicle.trip_id: vehicle.label
        for vehicle in first.snapshot.vehicles
    }
    assert labels['2706990'] == '606X'


@dataclass
class PatternlessTimetable(BrokenTimetable):
    """A timetable whose patterns query fails."""

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.
        around : date
            Ignored.

        Returns
        -------
        dict[str, TripPattern]
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.Error('file is not a database')


async def test_map_data_failure_posts_event() -> None:
    """A failed patterns read posts MapDataFailed for that request."""
    ports = replace(
        ports_of(timetable=PatternlessTimetable()), feeds=CrashingFeeds(),
    )
    request = LoadPatterns(trip_ids=frozenset({'t1'}), day=AT.date())
    posted = await posted_by(ports=ports, effects=[request])
    first = posted[0]
    assert first == MapDataFailed(request=request)
    assert isinstance(first, MapDataFailed)
    assert first.kind == MapData.PATTERNS


def test_starting_state_fills_the_pins_places() -> None:
    """The pin's places get their names and points from the timetable."""
    state = started(timetable=PlacedTimetable(), seed=MAP_PIN)
    assert shown(state=state).origin == place(
        '2145615', name='2145615', point=STOP_POINT,
    )
    assert shown(state=state).destination == place(
        '215020', name='215020', point=STOP_POINT,
    )
    assert state.map.centre == STOP_POINT
    assert state.now == AT


def test_starting_state_awaits_the_first_tick() -> None:
    """The app starts not yet knowing whether the clock is set."""
    assert not started(timetable=PlacedTimetable(), seed=MAP_PIN).clock_known


def test_starting_state_keeps_no_destination() -> None:
    """A pin without a Destination starts with its From filled in."""
    state = started(timetable=PlacedTimetable(), seed=pin_of('2145615', None))
    assert shown(state=state).origin == place(
        '2145615', name='2145615', point=STOP_POINT,
    )
    assert shown(state=state).destination is None
    assert state.map.centre == STOP_POINT


async def test_run_draws_the_map_in_the_variant_asked_for() -> None:
    """The tiles variant given to run is the one the Map reads."""
    tiles = CountingTiles()
    display = PressingDisplay()
    await run_sydney(
        ports=replace(ports_of(timetable=BuiltTimetable()), tiles=tiles),
        kit=kit_ports(display=display),
        seed=MAP_PIN,
        post_hook=display.hook,
        max_events=10,
        variant=TileVariant.BITS,
    )
    assert tiles.reads > 0
    assert tiles.variants == {TileVariant.BITS}


@dataclass
class LabelFailingTimetable(BrokenTimetable):
    """A timetable whose route labels query fails."""

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Fail like a damaged database file.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, str]
            Never returns.

        Raises
        ------
        sqlite3.Error
            Always.
        """
        raise sqlite3.Error('file is not a database')


async def test_label_failure_posts_feed_failed_for_that_feed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A labels read error fails that positions feed without a crash."""
    with caplog.at_level(logging.WARNING):
        posted = await posted_by(
            ports=ports_of(timetable=LabelFailingTimetable()),
            effects=[FetchFeed(feed=BUS_POSITIONS, kind=FeedKind.POSITIONS)],
        )
    first = posted[0]
    assert isinstance(first, FeedFailed)
    assert first.feed == BUS_POSITIONS
    assert first.reason != 'unexpected error'
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@dataclass
class PatternedTimetable(BrokenTimetable):
    """A timetable that knows one trip, t1, on shape s1."""

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Know trips t1 and t3, which share a shape and stops.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs.
        around : date
            Service date.

        Returns
        -------
        dict[str, TripPattern]
            Those asked for.
        """
        trip = TripPattern(
            trip_id='t1',
            route='606',
            source=SydneyFeed.BUS,
            shape_id='s1',
            stops=(
                PatternStop(arrives=0, departs=0, point=(0.0, 0.0)),
                PatternStop(arrives=60, departs=60, point=(100.0, 0.0)),
            ),
            days=(around,),
        )
        return {
            trip_id: replace(trip, trip_id=trip_id)
            for trip_id in ('t1', 't3')
            if trip_id in trip_ids
        }

    def shapes(self, *, shape_ids: Collection[str]) -> dict[str, Shape]:
        """
        Know shape s1.

        Parameters
        ----------
        shape_ids : Collection[str]
            Shape IDs.

        Returns
        -------
        dict[str, Shape]
            s1 when asked for.
        """
        shape = make_shape(shape_id='s1', points=[(0.0, 0.0), (100.0, 0.0)])
        return {'s1': shape} if 's1' in shape_ids else {}


def test_loaded_patterns_measures_found_trips_and_nones_the_rest() -> None:
    """Found trips are placed on their shapes; unknown ones are None."""
    loaded = loaded_patterns(
        timetable=PatternedTimetable(),
        effect=LoadPatterns(trip_ids=frozenset({'t1', 't2'}), day=AT.date()),
        measures=StopMeasures(),
    )
    assert loaded.trip_ids == {'t1', 't2'}
    assert set(loaded.patterns) == {'t1', 't2'}
    assert loaded.patterns['t2'] is None
    found = loaded.patterns['t1']
    assert found is not None
    assert found.along == pytest.approx((0.0, 100.0))
    assert set(loaded.shapes) == {'s1'}


def test_loaded_patterns_measure_a_shape_and_stops_once() -> None:
    """Trips sharing a shape and stops share one measurement."""
    measures = StopMeasures()
    loaded = loaded_patterns(
        timetable=PatternedTimetable(),
        effect=LoadPatterns(trip_ids=frozenset({'t1', 't3'}), day=AT.date()),
        measures=measures,
    )
    first, third = loaded.patterns['t1'], loaded.patterns['t3']
    assert first is not None
    assert third is not None
    assert first.along is third.along
    later = loaded_patterns(
        timetable=PatternedTimetable(),
        effect=LoadPatterns(trip_ids=frozenset({'t1'}), day=AT.date()),
        measures=measures,
    ).patterns['t1']
    assert later is not None
    assert later.along is first.along
