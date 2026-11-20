"""Tests for the day's lines read at a stop."""

from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Final

from paperroute_testkit.core.factories import START, departure
from paperroute_testkit.core.strip_factories import PIN
from paperroute_testkit.fakes import EmptyPlacedTimetable
from paperroute_transit.app_effects import loaded_patterns, map_data
from paperroute_transit.app_lines import loaded_day_lines
from paperroute_transit.core.geo import Point, Shape
from paperroute_transit.core.messages import (
    LoadDayLines,
    LoadPatterns,
    PatternsLoaded,
)
from paperroute_transit.core.model import Departure, Pin
from paperroute_transit.core.patterns import TripPattern
from paperroute_transit.measures import StopMeasures
from tests.core.test_track import FEED, HOP, RAIL

DAY: Final[date] = START.date()
PIN_NO_TO: Final[Pin] = replace(PIN, destination=None)


@dataclass
class RecordingTimetable(EmptyPlacedTimetable):
    """Gives a fixed day of departures and records each read."""

    reads: list[tuple[Pin, date]] = field(default_factory=list)

    def day_departures(self, *, pin: Pin, day: date) -> list[Departure]:
        """
        Give the 606 twice, the 610, the 620 and a school run.

        Parameters
        ----------
        pin : Pin
            Recorded.
        day : date
            Recorded.

        Returns
        -------
        list[Departure]
            The day.
        """
        self.reads.append((pin, day))
        return [
            departure(departs=START, route='606', trip_id='a'),
            departure(departs=START, route='610', trip_id='b'),
            departure(departs=START, route='606', trip_id='c'),
            departure(departs=START, route='620', trip_id='d'),
            departure(
                departs=START, route='S1', trip_id='e', kind='school',
            ),
        ]


def kept(*, skip_school: bool) -> list[tuple[str, str]]:
    """
    List the route and trip of the day lines read with a school setting.

    Parameters
    ----------
    skip_school : bool
        Whether school runs are dropped.

    Returns
    -------
    list[tuple[str, str]]
        The route and trip ID of each, in order.
    """
    loaded = loaded_day_lines(
        timetable=RecordingTimetable(),
        effect=LoadDayLines(
            pin=PIN_NO_TO, day=DAY, timetable=None, skip_school=skip_school,
        ),
    )
    return [(item.route, item.trip_id) for item in loaded.departures]


def test_day_lines_keep_the_first_departure_of_each_line() -> None:
    """The day is read once and kept one departure per line."""
    timetable = RecordingTimetable()
    loaded_day_lines(
        timetable=timetable,
        effect=LoadDayLines(
            pin=PIN_NO_TO, day=DAY, timetable=None, skip_school=False,
        ),
    )
    assert timetable.reads == [(PIN_NO_TO, DAY)]
    assert kept(skip_school=False) == [
        ('606', 'a'), ('610', 'b'), ('620', 'd'), ('S1', 'e'),
    ]


def test_day_lines_drop_school_runs_when_school_buses_are_off() -> None:
    """A school run gets no line while the school buses setting is on."""
    assert kept(skip_school=True) == [
        ('606', 'a'), ('610', 'b'), ('620', 'd'),
    ]


@dataclass
class HopTimetable(EmptyPlacedTimetable):
    """A train on track A to B and a replacement bus on its own shape."""

    def version(self) -> str | None:
        """
        Name the build.

        Returns
        -------
        str | None
            Always 'v1'.
        """
        return 'v1'

    def patterns(
        self, *, trip_ids: Collection[str], around: date,
    ) -> dict[str, TripPattern]:
        """
        Give RAIL as 'train', and as 'replacement' on 'bus-shape', road.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.
        around : date
            Ignored.

        Returns
        -------
        dict[str, TripPattern]
            The two trips.
        """
        bus = replace(
            RAIL, trip_id='replacement', shape_id='bus-shape', road=True,
        )
        return {'train': RAIL, 'replacement': bus}

    def shapes(self, *, shape_ids: Collection[str]) -> dict[str, Shape]:
        """
        Give FEED, and the bus's shape as a copy of it.

        Parameters
        ----------
        shape_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, Shape]
            The two shapes.
        """
        return {
            'feed': FEED, 'bus-shape': replace(FEED, shape_id='bus-shape'),
        }

    def rail_hops(
        self, *, pairs: Collection[tuple[str, str]],
    ) -> dict[tuple[str, str], tuple[Point, ...]]:
        """
        Give the A to B track.

        Parameters
        ----------
        pairs : Collection[tuple[str, str]]
            Ignored.

        Returns
        -------
        dict[tuple[str, str], tuple[Point, ...]]
            The one hop.
        """
        return {('A', 'B'): HOP}


LOAD: Final[LoadPatterns] = LoadPatterns(
    trip_ids=frozenset({'train', 'replacement'}), day=DAY,
)


def on_the_map() -> PatternsLoaded:
    """
    Load the two trips the way the map does.

    Returns
    -------
    PatternsLoaded
        The loaded patterns.
    """
    loaded = map_data(
        timetable=HopTimetable(), effect=LOAD, measures=StopMeasures(),
    )
    assert isinstance(loaded, PatternsLoaded)
    return loaded


def test_train_patterns_follow_track_on_the_map() -> None:
    """A map's train pattern is measured along its track shape."""
    loaded = on_the_map()
    train = loaded.patterns['train']
    assert train is not None and train.shape_id is not None
    assert train.shape_id.startswith('track:')
    assert train.shape_id in loaded.shapes


def test_road_patterns_never_follow_track() -> None:
    """A replacement bus between two stations keeps its own shape."""
    loaded = on_the_map()
    bus = loaded.patterns['replacement']
    assert bus is not None and bus.shape_id == 'bus-shape'


def test_estimates_keep_the_feed_shape() -> None:
    """Delay estimates load patterns without the track."""
    loaded = loaded_patterns(
        timetable=HopTimetable(), effect=LOAD, measures=StopMeasures(),
    )
    train = loaded.patterns['train']
    assert train is not None and train.shape_id == 'feed'
