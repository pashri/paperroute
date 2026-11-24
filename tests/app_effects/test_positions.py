# pylint: disable=unused-argument
"""Tests for app effects: positions."""

import logging
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY
from paperroute_testkit.adapters.rt_factory import feed as rt_feed
from paperroute_testkit.core.factories import (
    EIGHT,
    START,
    pattern,
    place,
    snapshot,
    vehicle,
)
from paperroute_testkit.fakes import EmptyPlacedTimetable, FakeNetwork
from paperroute_transit.app_effects import (
    estimates_of,
    labelled_positions,
    loaded_route_trips,
)
from paperroute_transit.core.alert_model import AlertPlaces
from paperroute_transit.core.chooser import Column, Level
from paperroute_transit.core.feed_plan import FeedParsers
from paperroute_transit.core.messages import (
    Estimating,
    LoadPreviews,
    LoadRouteTrips,
    LoadStrip,
    PreviewsFailed,
    PreviewsLoaded,
    RouteTripsLoaded,
    StripFailed,
    StripLoaded,
)
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.positions import (
    PositionsSnapshot,
    VehiclePosition,
)
from paperroute_transit.core.reach import RunTail
from paperroute_transit.core.route_trips import RouteTrip
from paperroute_transit.core.sides import KerbPair
from paperroute_transit.measures import StopMeasures
from tests.app_effects.helpers import STRIP_KEY, RecordingParsers, reach_data
from tests.app_factories import AT, ports_of, posted_by

VEHICLES: Final[tuple[tuple[str, str, str], ...]] = (
    ('t1', '10', 'A'),
    ('x9', '57428', ''),
    ('', '', '777'),
    ('x8', '999', ''),
)


@dataclass
class StubTimetable(EmptyPlacedTimetable):
    """A timetable that knows one trip's label and one route's name."""

    asked: int = 0

    def route_labels(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Know trip 't1' as 'A'.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trip IDs asked about.

        Returns
        -------
        dict[str, str]
            The label of 't1' when asked about it.
        """
        return {'t1': 'A'} if 't1' in trip_ids else {}

    def alert_places(
        self,
        *,
        route_ids: Collection[str],
        stop_ids: Collection[str],
        route_type: int | None = None,
    ) -> AlertPlaces:
        """
        Know route '57428' as 'PR1' and count the question.

        Parameters
        ----------
        route_ids : Collection[str]
            Route IDs asked about.
        stop_ids : Collection[str]
            Ignored.
        route_type : int | None
            Ignored.

        Returns
        -------
        AlertPlaces
            The name of '57428' when asked about it.
        """
        self.asked += 1
        names = {'57428': 'PR1'}
        return AlertPlaces(
            routes={r: names[r] for r in route_ids if r in names},
            stops={},
        )


def unnamed_positions(
    *, data: bytes, fetched_at: datetime,
) -> PositionsSnapshot:
    """
    Read four vehicles, the unnamed ones labelled ''.

    Parameters
    ----------
    data : bytes
        Feed body, ignored.
    fetched_at : datetime
        When it was downloaded.

    Returns
    -------
    PositionsSnapshot
        The vehicles in `VEHICLES`.
    """
    return PositionsSnapshot(
        fetched_at=fetched_at,
        header_time=0,
        vehicles=tuple(
            VehiclePosition(
                trip_id=trip,
                route_id=route,
                label=label,
                point=(0.0, 0.0),
                bearing=None,
                timestamp=0,
                start_date=None,
            )
            for trip, route, label in VEHICLES
        ),
    )


def labels(timetable: StubTimetable) -> list[str]:
    """
    Run `labelled_positions` over `VEHICLES` and list the labels.

    Parameters
    ----------
    timetable : StubTimetable
        Answers the lookups.

    Returns
    -------
    list[str]
        The label of each vehicle drawn.
    """
    recorder = RecordingParsers()
    labelled = labelled_positions(
        timetable=timetable,
        parsers=FeedParsers(
            trips=recorder.trips,
            positions=unnamed_positions,
            alerts=recorder.alerts,
        ),
        data=rt_feed(trips=[]),
        fetched_at=AT,
    )
    return [vehicle.label for vehicle in labelled.vehicles]


def test_labelled_positions_leaves_sydney_vehicles_alone() -> None:
    """Labelled positions leaves sydney vehicles alone."""
    timetable = StubTimetable(city=SYDNEY)
    assert len(labels(timetable)) == 4
    assert timetable.asked == 0


def test_labelled_positions_falls_back_to_the_route() -> None:
    """Labelled positions falls back to the route."""
    timetable = StubTimetable(city=replace(SYDNEY, route_fallback=True))
    assert labels(timetable) == ['A', 'PR1', '777']
    assert timetable.asked == 1


LATER_STOPS: Final = (  # 8:15 from (1000, 0) to (0, 0) at 8:25
    PatternStop(arrives=EIGHT + 900, departs=EIGHT + 900, point=(1000.0, 0.0)),
    PatternStop(arrives=EIGHT + 1500, departs=EIGHT + 1500, point=(0.0, 0.0)),
)


@dataclass
class PatternTimetable(EmptyPlacedTimetable):
    """A timetable that knows some patterns and joins, and notes each ask."""

    found: dict[str, TripPattern] = field(default_factory=dict)
    joins: dict[str, str] = field(default_factory=dict)
    turns: dict[str, str] = field(default_factory=dict)
    asked: list[frozenset[str]] = field(default_factory=list)

    def patterns(
        self, *, trip_ids: Collection[str], around: date
    ) -> dict[str, TripPattern]:
        """
        Give the known patterns among the trips, noting the ask.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trips asked for.
        around : date
            Ignored.

        Returns
        -------
        dict[str, TripPattern]
            The known ones.
        """
        self.asked.append(frozenset(trip_ids))
        return {k: v for k, v in self.found.items() if k in trip_ids}

    def route_trips(
        self, *, route: str, source: str, day: date
    ) -> tuple[RouteTrip, ...]:
        """
        Know no trips.

        Parameters
        ----------
        route : str
            Ignored.
        source : str
            Ignored.
        day : date
            Ignored.

        Returns
        -------
        tuple[RouteTrip, ...]
            Nothing.
        """
        return ()

    def joined_trips(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Give the known joins of the trips.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trips asked for.

        Returns
        -------
        dict[str, str]
            Joined trip by trip.
        """
        return {k: v for k, v in self.joins.items() if k in trip_ids}

    def turned_trips(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Give the known turnarounds of the trips.

        Parameters
        ----------
        trip_ids : Collection[str]
            Trips asked for.

        Returns
        -------
        dict[str, str]
            Turned trip by trip.
        """
        return {k: v for k, v in self.turns.items() if k in trip_ids}


def test_estimates_of_answers_only_the_asked_trips() -> None:
    """Only the board's trips are answered; trips that join them are read."""
    timetable = PatternTimetable(
        found={
            't1': replace(pattern(trip_id='t1'), next_trip='t2'),
            't2': pattern(trip_id='t2', stops=LATER_STOPS),
            't3': pattern(trip_id='t3'),
        },
        joins={'t1': 't2'},
    )
    when = int((START + timedelta(minutes=12)).timestamp())
    positions = snapshot(
        fetched_at=START + timedelta(minutes=12),
        vehicles=(
            vehicle(timestamp=when, trip_id='t1', point=(500.0, 0.0)),
            vehicle(timestamp=when, trip_id='t3', point=(500.0, 0.0)),
        ),
    )
    found = estimates_of(
        timetable=timetable,
        gauge=timetable,
        measures=StopMeasures(),
        positions=positions,
        ask=Estimating(
            feed='estimates/bus',
            trip_ids=frozenset({'t2'}),
            stale_after=timedelta(minutes=2),
        ),
    )
    assert set(found.trips) == {'t2'}
    assert found.trips['t2'].updates[0].departure_delay == 240
    assert timetable.asked == [frozenset({'t1', 't2'})]


async def test_load_previews_posts_each_rows_column() -> None:
    """A previews load posts one chain per row."""
    column = Column(level=Level.TOWARDS, items=())
    posted = await posted_by(
        ports=ports_of(network=FakeNetwork(column=column)),
        effects=[LoadPreviews(
            path=(), steps=((Level.KIND, 'bus'),), timetable='v1',
        )],
    )
    assert posted == [
        PreviewsLoaded(path=(), previews=((column,),), timetable='v1'),
    ]


async def test_load_previews_failure_posts_previews_failed() -> None:
    """A failed previews read posts PreviewsFailed."""
    network = FakeNetwork(error=sqlite3.OperationalError('locked'))
    posted = await posted_by(
        ports=ports_of(network=network),
        effects=[LoadPreviews(
            path=(), steps=((Level.KIND, 'bus'),), timetable='v1',
        )],
    )
    assert posted == [PreviewsFailed(path=(), timetable='v1')]


async def test_load_strip_reads_the_kerbs_facing_a_both_sides_from() -> None:
    """A both-sides strip loads the pairs of the stops before its From."""
    data = replace(reach_data(), tails=(
        RunTail(before=('P2', 'P1'), after=('E1', 'E2'), trips=8),
    ))
    pair = KerbPair(station='P1', other=place('E1'), busy=False)
    network = FakeNetwork(data=data, pairs={'P1': pair, 'E2': pair})
    both = replace(STRIP_KEY, across='x2')
    (loaded,) = await posted_by(
        ports=ports_of(network=network),
        effects=[LoadStrip(key=both, origin=data.origin, timetable='v1')],
    )
    assert isinstance(loaded, StripLoaded) and loaded.reach is not None
    assert loaded.reach.facing == {'P1': 'E1'}
    (plain,) = await posted_by(
        ports=ports_of(network=network),
        effects=[
            LoadStrip(key=STRIP_KEY, origin=data.origin, timetable='v1'),
        ],
    )
    assert isinstance(plain, StripLoaded) and plain.reach is not None
    assert plain.reach.facing == {}


async def test_load_strip_keeps_the_strip_when_the_pairs_read_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed pairs read gives a Strip with no facing; a runs one fails."""
    data = replace(reach_data(), tails=(
        RunTail(before=('P2', 'P1'), after=('E1', 'E2'), trips=8),
    ))
    both = replace(STRIP_KEY, across='x2')
    load = LoadStrip(key=both, origin=data.origin, timetable='v1')
    locked = sqlite3.OperationalError('locked')
    with caplog.at_level(logging.WARNING):
        (loaded,) = await posted_by(
            ports=ports_of(
                network=FakeNetwork(data=data, pairs_error=locked),
            ),
            effects=[load],
        )
    assert isinstance(loaded, StripLoaded) and loaded.reach is not None
    assert loaded.reach.facing == {}
    assert [
        record.levelno
        for record in caplog.records
        if record.message == 'reading the kerb pairs failed'
    ] == [logging.WARNING]
    failed = await posted_by(
        ports=ports_of(network=FakeNetwork(data=data, error=locked)),
        effects=[load],
    )
    assert failed == [StripFailed(key=both, timetable='v1')]


@dataclass
class RecordingGauge:
    """A gauge timetable that knows one route's trips and notes each ask."""

    trips: tuple[RouteTrip, ...]
    asked: list[tuple[str, str, date]] = field(default_factory=list)

    def route_trips(
        self, *, route: str, source: str, day: date
    ) -> tuple[RouteTrip, ...]:
        """
        Give the known trips, noting the ask.

        Parameters
        ----------
        route : str
            Route short name.
        source : str
            Feed source.
        day : date
            Later service day.

        Returns
        -------
        tuple[RouteTrip, ...]
            The known trips.
        """
        self.asked.append((route, source, day))
        return self.trips

    def turned_trips(self, *, trip_ids: Collection[str]) -> dict[str, str]:
        """
        Know no turnarounds.

        Parameters
        ----------
        trip_ids : Collection[str]
            Ignored.

        Returns
        -------
        dict[str, str]
            Nothing.
        """
        return {}


def test_route_trips_effect_handler_reads_the_repository() -> None:
    """The handler asks the repository and echoes the request back."""
    day = date(2026, 10, 16)
    trip = RouteTrip(
        trip_id='t1', day=day, starts=EIGHT, ends=EIGHT + 600, direction=0,
    )
    gauge = RecordingGauge(trips=(trip,))
    effect = LoadRouteTrips(route='606', source='bus', day=day)
    loaded = loaded_route_trips(gauge=gauge, effect=effect)
    assert loaded == RouteTripsLoaded(request=effect, trips=(trip,))
    assert gauge.asked == [('606', 'bus', day)]


def test_estimates_of_answers_the_hero_from_the_previous_trip_s_bus() -> None:
    """A bus on a trip that turns into the asked trip carries its delay."""
    timetable = PatternTimetable(
        found={
            't1': pattern(trip_id='t1'),
            't2': pattern(trip_id='t2', stops=LATER_STOPS),
        },
        turns={'t1': 't2'},
    )
    when = int((START + timedelta(minutes=12)).timestamp())
    positions = snapshot(
        fetched_at=START + timedelta(minutes=12),
        vehicles=(
            vehicle(timestamp=when, trip_id='t1', point=(500.0, 0.0)),
        ),
    )
    found = estimates_of(
        timetable=timetable,
        gauge=timetable,
        measures=StopMeasures(),
        positions=positions,
        ask=Estimating(
            feed='estimates/bus',
            trip_ids=frozenset({'t2'}),
            stale_after=timedelta(minutes=2),
        ),
    )
    assert set(found.trips) == {'t2'}
    assert found.trips['t2'].updates[0].departure_delay == 240


def test_estimates_of_prefers_a_join_over_a_turnaround() -> None:
    """A trip with both a join and a turnaround carries into the join."""
    timetable = PatternTimetable(
        found={
            't1': replace(pattern(trip_id='t1'), next_trip='t2'),
            't2': pattern(trip_id='t2', stops=LATER_STOPS),
            't3': pattern(trip_id='t3', stops=LATER_STOPS),
        },
        joins={'t1': 't2'},
        turns={'t1': 't3'},
    )
    when = int((START + timedelta(minutes=12)).timestamp())
    positions = snapshot(
        fetched_at=START + timedelta(minutes=12),
        vehicles=(
            vehicle(timestamp=when, trip_id='t1', point=(500.0, 0.0)),
        ),
    )
    found = estimates_of(
        timetable=timetable,
        gauge=timetable,
        measures=StopMeasures(),
        positions=positions,
        ask=Estimating(
            feed='estimates/bus',
            trip_ids=frozenset({'t2', 't3'}),
            stale_after=timedelta(minutes=2),
        ),
    )
    assert set(found.trips) == {'t2'}
