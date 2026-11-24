"""Tests for buses projected onto the hero's line."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Final

import pytest

from paperroute_sydney.city import SYDNEY_ZONE
from paperroute_testkit.core.factories import pattern, vehicle
from paperroute_transit.core.gauge_buses import (
    GaugeBus,
    along_index,
    gauge_buses,
    same_way,
    timetable_index,
)
from paperroute_transit.core.geo import Point
from paperroute_transit.core.model import service_time
from paperroute_transit.core.patterns import PatternStop, TripPattern
from paperroute_transit.core.positions import VehiclePosition
from paperroute_transit.core.route_trips import RouteTrip

ZONE: Final = SYDNEY_ZONE
DAY: Final[date] = date(2026, 10, 16)
EIGHT: Final[int] = 8 * 3600  # seconds
STEP: Final[int] = 60  # seconds between stops
STATIONS: Final[tuple[str, ...]] = tuple(f'S{i}' for i in range(10))


def at(*, seconds: int) -> datetime:
    """
    A moment on the service day.

    Parameters
    ----------
    seconds : int
        Seconds into the service day.

    Returns
    -------
    datetime
        Aware local time.
    """
    return service_time(day=DAY, seconds=seconds, zone=ZONE)


NOW: Final[datetime] = at(seconds=EIGHT)


def call(*, station: str, x: float, y: float, at_s: int) -> PatternStop:
    """
    A call of a trip.

    Parameters
    ----------
    station : str
        Station ID.
    x : float
        Metres east.
    y : float
        Metres north.
    at_s : int
        Arrival and departure, in seconds into the service day.

    Returns
    -------
    PatternStop
        The call.
    """
    return PatternStop(
        arrives=at_s, departs=at_s, point=(x, y), station=station,
    )


def run(
    *,
    order: Sequence[int],
    first: int = EIGHT,
    direction: int | None = None,
    trip_id: str = 'p',
) -> TripPattern:
    """
    A trip along the line through the given stops.

    Parameters
    ----------
    order : Sequence[int]
        Stop numbers in running order; stop `n` is `n * 100` m east.
    first : int
        Departure from the first stop, in seconds into the day.
    direction : int | None
        Direction ID.
    trip_id : str
        Trip ID.

    Returns
    -------
    TripPattern
        The trip.
    """
    return pattern(
        trip_id=trip_id,
        direction=direction,
        stops=tuple(
            call(
                station=STATIONS[n],
                x=n * 100.0,
                y=0.0,
                at_s=first + STEP * place,
            )
            for place, n in enumerate(order)
        ),
    )


def midway(first: Point, second: Point) -> Point:
    """
    The point halfway between two.

    Parameters
    ----------
    first : Point
        One point.
    second : Point
        The other.

    Returns
    -------
    Point
        The midpoint.
    """
    return ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)


def at_point(*, trip_id: str, point: Point) -> VehiclePosition:
    """
    A report of a vehicle at a point.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    point : Point
        Position in metres.

    Returns
    -------
    VehiclePosition
        The report.
    """
    return vehicle(
        timestamp=int(NOW.timestamp()), trip_id=trip_id, point=point,
    )


def route_trip(*, trip_id: str, starts: int = EIGHT) -> RouteTrip:
    """
    A trip of the route.

    Parameters
    ----------
    trip_id : str
        Trip ID.
    starts : int
        First departure in seconds into the service day.

    Returns
    -------
    RouteTrip
        The trip.
    """
    return RouteTrip(
        trip_id=trip_id,
        day=DAY,
        starts=starts,
        ends=starts + STEP * 9,
        direction=0,
    )


S3: Final[Point] = (300.0, 0.0)
S4: Final[Point] = (400.0, 0.0)
S6: Final[Point] = (600.0, 0.0)
S8: Final[Point] = (800.0, 0.0)
S9: Final[Point] = (900.0, 0.0)
Z: Final[Point] = (510.0, 80.0)
HERO: Final[TripPattern] = run(order=range(10), direction=0)
SAME: Final[TripPattern] = run(order=range(10), direction=0, trip_id='t2')
BACK: Final[TripPattern] = run(
    order=range(9, -1, -1), direction=1, trip_id='r1',
)
SAME_LATER: Final[TripPattern] = run(
    order=range(10), first=EIGHT + 600, direction=0, trip_id='t3',
)
T3_AT_S2: Final[int] = EIGHT + 600 + 2 * STEP
SPUR: Final[TripPattern] = pattern(
    trip_id='s',
    stops=(
        *run(order=range(5)).stops,
        PatternStop(
            arrives=EIGHT + 300,
            departs=EIGHT + 300,
            point=Z,
            station='Z',
        ),
        PatternStop(
            arrives=EIGHT + 360,
            departs=EIGHT + 360,
            point=(510.0, 400.0),
            station='Y',
        ),
    ),
)


def buses_of(
    *,
    patterns: Mapping[str, TripPattern | None],
    vehicles: Sequence[VehiclePosition] = (),
    trips_running: Sequence[RouteTrip] = (),
    now: datetime = NOW,
) -> tuple[GaugeBus, ...]:
    """
    Place buses on the hero's line.

    Parameters
    ----------
    patterns : Mapping[str, TripPattern | None]
        Patterns by trip ID.
    vehicles : Sequence[VehiclePosition]
        Reported vehicles.
    trips_running : Sequence[RouteTrip]
        Trips under way by the timetable.
    now : datetime
        The moment.

    Returns
    -------
    tuple[GaugeBus, ...]
        The buses.
    """
    return gauge_buses(
        hero=HERO,
        patterns=patterns,
        vehicles=vehicles,
        trips_running=trips_running,
        now=now,
        zone=ZONE,
    )


def test_gauge_buses_places_a_same_way_bus_between_its_stops() -> None:
    """A same-way bus sits between the hero stops it is between."""
    (bus,) = buses_of(
        patterns={'t2': SAME},
        vehicles=(at_point(trip_id='t2', point=midway(S3, S4)),),
    )
    assert bus.spot == pytest.approx(3.5)
    assert (bus.right, bus.live, bus.trip_id) == (True, True, 't2')


def test_gauge_buses_faces_an_opposite_bus_left() -> None:
    """An opposite bus is placed at the nearest hero stop, facing left."""
    (bus,) = buses_of(
        patterns={'r1': BACK},
        vehicles=(at_point(trip_id='r1', point=S6),),
    )
    assert (bus.spot, bus.right) == (6.0, False)


def test_gauge_buses_snaps_a_bus_off_the_line_to_the_nearest_hero_stop(
) -> None:
    """A bus on a spur snaps to the hero stop nearest its stop."""
    (bus,) = buses_of(
        patterns={'s': SPUR},
        vehicles=(at_point(trip_id='s', point=Z),),
    )
    assert bus.spot == 5.0


def test_gauge_buses_draws_a_dashed_badge_for_a_running_trip() -> None:
    """A running trip without a report is placed by its timetable."""
    (bus,) = buses_of(
        patterns={'t3': SAME_LATER},
        trips_running=(route_trip(trip_id='t3', starts=EIGHT + 600),),
        now=at(seconds=T3_AT_S2),
    )
    assert (bus.spot, bus.live) == (2.0, False)


def test_gauge_buses_draws_no_dashed_badge_for_a_matched_trip() -> None:
    """A running trip with a report is drawn from the report only."""
    buses = buses_of(
        patterns={'t2': SAME},
        vehicles=(at_point(trip_id='t2', point=S3),),
        trips_running=(route_trip(trip_id='t2'),),
    )
    assert [bus.live for bus in buses] == [True]


def test_gauge_buses_keeps_a_bus_past_the_from_until_its_last_stop(
) -> None:
    """A bus at the second last stop stays; at the last stop it drops."""
    near = buses_of(
        patterns={'t2': SAME},
        vehicles=(at_point(trip_id='t2', point=S8),),
    )
    done = buses_of(
        patterns={'t2': SAME},
        vehicles=(at_point(trip_id='t2', point=S9),),
    )
    assert (len(near), done) == (1, ())


def test_gauge_buses_skips_a_vehicle_without_a_pattern() -> None:
    """A vehicle with no pattern, or an unknown trip, is left out."""
    assert buses_of(
        patterns={'x': None},
        vehicles=(
            at_point(trip_id='x', point=S3),
            at_point(trip_id='unknown', point=S3),
        ),
    ) == ()


def test_gauge_buses_orders_the_buses_by_spot() -> None:
    """The buses come back by spot."""
    buses = buses_of(
        patterns={'t2': SAME, 'r1': BACK},
        vehicles=(
            at_point(trip_id='r1', point=S6),
            at_point(trip_id='t2', point=S3),
        ),
    )
    assert [bus.spot for bus in buses] == [3.0, 6.0]


def test_same_way_without_directions_compares_stop_order() -> None:
    """With no direction ids the order of shared stations decides."""
    assert same_way(hero=run(order=range(10)), pattern=run(order=[2, 3]))
    assert not same_way(
        hero=run(order=range(10)), pattern=run(order=[3, 2]),
    )


def test_same_way_uses_direction_ids_of_one_route() -> None:
    """Direction ids decide for trips of one route."""
    assert not same_way(hero=HERO, pattern=BACK)
    assert same_way(hero=HERO, pattern=SAME)


def test_same_way_across_route_ids_compares_stop_order() -> None:
    """Opposite direction ids of different route IDs do not count."""
    other = pattern(
        stops=run(order=range(10)).stops, direction=1, route_id='other',
    )
    hero = pattern(
        stops=run(order=range(10)).stops, direction=0, route_id='hero',
    )
    assert same_way(hero=hero, pattern=other)


def test_along_index_finds_the_nearest_segment() -> None:
    """A point near a segment gives the fraction along it."""
    assert along_index(
        pattern=HERO, point=(250.0, 30.0),
    ) == pytest.approx(2.5)


def test_timetable_index_dwells_and_interpolates() -> None:
    """Dwelling gives the stop; between stops is linear in time."""
    assert timetable_index(pattern=HERO, seconds=EIGHT + 2 * STEP) == 2.0
    assert timetable_index(
        pattern=HERO, seconds=EIGHT + STEP + 15,
    ) == pytest.approx(1.25)


def test_timetable_index_is_none_before_the_first_departure() -> None:
    """Before it leaves and after it arrives, there is no place."""
    assert timetable_index(pattern=HERO, seconds=EIGHT - 1) is None
    assert timetable_index(pattern=HERO, seconds=EIGHT + 9 * STEP + 1) is None


def test_gauge_buses_places_a_bus_when_the_hero_has_no_positions() -> None:
    """A hero without positions puts every bus at its first stop."""
    blind = pattern(
        stops=tuple(
            PatternStop(
                arrives=EIGHT, departs=EIGHT, point=None, station=name,
            )
            for name in STATIONS
        ),
        direction=0,
    )
    (bus,) = gauge_buses(
        hero=blind,
        patterns={'r1': BACK},
        vehicles=(at_point(trip_id='r1', point=S6),),
        trips_running=(),
        now=NOW,
        zone=ZONE,
    )
    assert (bus.spot, bus.right) == (0.0, False)


def test_gauge_buses_drops_a_bus_at_its_last_stop_with_a_position() -> None:
    """A trip whose final stop has no position ends at the one before."""
    stops = (
        *run(order=range(10), trip_id='t2').stops[:9],
        PatternStop(
            arrives=EIGHT + 600,
            departs=EIGHT + 600,
            point=None,
            station='S9',
        ),
    )
    ragged = pattern(trip_id='t2', stops=stops, direction=0)
    assert buses_of(
        patterns={'t2': ragged},
        vehicles=(at_point(trip_id='t2', point=S8),),
    ) == ()
