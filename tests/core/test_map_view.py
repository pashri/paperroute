"""Tests for the Map view-model."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from paperroute.core.geo import Point, make_shape
from paperroute.core.map_view import (
    MAP_HEIGHT,
    MAP_WIDTH,
    NO_LOCATION,
    SIDEBAR_ROWS,
    MapInputs,
    Marker,
    build_map_view,
    needed_patterns,
    pin_trips,
    pinned_routes,
    position_age,
    route_lines,
    screen_point,
    timetable_moment,
    visible,
)
from paperroute.core.model import Departure, Mode
from paperroute.core.patterns import timetable_point
from paperroute.core.positions import VehiclePosition
from paperroute.core.stop_view import Row, StopView
from paperroute.core.tiles import ZOOMS
from tests.core.factories import START, departure, pattern, snapshot, vehicle

CENTRE: Point = (0.0, 0.0)
ZOOM: int = 2
MPP: int = ZOOMS[ZOOM]
NOW = START + timedelta(minutes=5, seconds=10)
FEED = 'v1/gtfs/vehiclepos/buses'
FETCHED = NOW - timedelta(seconds=30)


def row(*, route: str = '606') -> Row:
    """
    A sidebar row.

    Parameters
    ----------
    route : str
        Route short name.

    Returns
    -------
    Row
        The row.
    """
    return Row(
        route=route,
        mode=Mode.BUS,
        platform='',
        destination='Parramatta',
        countdown='5',
        leaves_at='8:05',
        arrives_at='8:35',
        ride_minutes=30,
        live=True,
        pill=None,
    )


def stop_view(*, hero: Row | None, rows: tuple[Row, ...]) -> StopView:
    """
    A Stop view with a hero and rows.

    Parameters
    ----------
    hero : Row | None
        The next service.
    rows : tuple[Row, ...]
        The list.

    Returns
    -------
    StopView
        The view.
    """
    return StopView(
        origin='Town Hall',
        destination='Parramatta',
        clock='8:05',
        hero=hero,
        rows=rows,
        banner='Live times unavailable',
        empty=None,
        scroll=0,
    )


def pin() -> Departure:
    """
    The pinned departure on trip 't1'.

    Returns
    -------
    Departure
        A 606 departure.
    """
    return departure(departs=START, trip_id='t1')


def inputs(
    *,
    vehicles: tuple[VehiclePosition, ...] = (),
    fetched_at: datetime = NOW,
    departures: tuple[Departure, ...] = (),
    now: datetime = NOW,
) -> MapInputs:
    """
    Map inputs around the origin with the default pattern for 't1'.

    Parameters
    ----------
    vehicles : tuple[VehiclePosition, ...]
        Vehicles in the one positions feed.
    fetched_at : datetime
        When that feed was downloaded.
    departures : tuple[Departure, ...]
        The pin's departures.
    now : datetime
        Current time.

    Returns
    -------
    MapInputs
        The inputs.
    """
    return MapInputs(
        stop_view=stop_view(hero=row(), rows=()),
        departures=departures,
        centre=CENTRE,
        zoom=ZOOM,
        positions={
            FEED: snapshot(fetched_at=fetched_at, vehicles=vehicles),
        },
        patterns={'t1': pattern()},
        shapes={},
        now=now,
    )


def heard(*, age: int) -> int:
    """
    A vehicle timestamp that many seconds before the feed header.

    Parameters
    ----------
    age : int
        Seconds.

    Returns
    -------
    int
        Unix seconds.
    """
    return int(NOW.timestamp()) - age


def at(*, point: Point) -> tuple[float, float]:
    """
    A point in map pixels at the test centre and zoom.

    Parameters
    ----------
    point : Point
        Metres.

    Returns
    -------
    tuple[float, float]
        Map pixels.
    """
    return screen_point(centre=CENTRE, mpp=MPP, point=point)


def test_screen_point_centre_is_map_middle_and_north_is_up() -> None:
    """The centre lands mid-map and 100 m north is 100 / mpp px up."""
    centre = (1234.5, -678.9)
    x, y = screen_point(centre=centre, mpp=MPP, point=centre)
    assert abs(x - MAP_WIDTH / 2) <= 1
    assert abs(y - MAP_HEIGHT / 2) <= 1
    north = (centre[0], centre[1] + 100.0)
    nx, ny = screen_point(centre=centre, mpp=MPP, point=north)
    assert nx == pytest.approx(x)
    assert ny == pytest.approx(y - 100 / MPP)


def test_visible_allows_a_twenty_pixel_margin() -> None:
    """Points up to 20 px outside the map still count."""
    assert visible(at=(-20.0, MAP_HEIGHT + 20.0))
    assert not visible(at=(-21.0, 0.0))
    assert not visible(at=(0.0, MAP_HEIGHT + 21.0))


def test_fresh_vehicle_is_solid_with_bearing() -> None:
    """A vehicle heard just now is live at its point with its bearing."""
    bus = vehicle(timestamp=heard(age=0), point=(100.0, 200.0), bearing=90.0)
    view = build_map_view(inputs=inputs(vehicles=(bus,)))
    x, y = at(point=(100.0, 200.0))
    assert view.markers == (
        Marker(route='606', x=x, y=y, bearing=90.0, live=True, pinned=False),
    )


def test_vehicle_over_two_minutes_old_moves_to_timetable_position() -> None:
    """A 130 s old vehicle is hollow, wedgeless, at its timetable point."""
    bus = vehicle(timestamp=int(FETCHED.timestamp()) - 100, bearing=90.0)
    view = build_map_view(
        inputs=inputs(vehicles=(bus,), fetched_at=FETCHED),
    )
    point = timetable_point(
        pattern=pattern(),
        shape=None,
        now=timetable_moment(now=NOW),
    )
    assert point is not None
    (marker,) = view.markers
    assert (marker.live, marker.bearing) == (False, None)
    assert (marker.x, marker.y) == pytest.approx(at(point=point))


def test_stale_marker_carries_age() -> None:
    """The stale marker's age is taken at now, not floored to 30 s."""
    bus = vehicle(timestamp=int(FETCHED.timestamp()) - 100)
    view = build_map_view(
        inputs=inputs(vehicles=(bus,), fetched_at=FETCHED),
    )
    (marker,) = view.markers
    assert marker.age == 130.0


def test_live_marker_has_no_age() -> None:
    """A fresh vehicle carries no age."""
    bus = vehicle(timestamp=heard(age=10))
    (marker,) = build_map_view(inputs=inputs(vehicles=(bus,))).markers
    assert marker.live
    assert marker.age is None


def test_never_heard_marker_has_no_age() -> None:
    """A pin trip with no vehicle is hollow and carries no age."""
    (marker,) = build_map_view(inputs=inputs(departures=(pin(),))).markers
    assert not marker.live
    assert marker.age is None


def test_exactly_two_minutes_is_still_live() -> None:
    """An age of exactly 120 s is still live."""
    bus = vehicle(timestamp=heard(age=120))
    (marker,) = build_map_view(inputs=inputs(vehicles=(bus,))).markers
    assert marker.live
    assert marker.age is None


def test_stale_vehicle_without_pattern_is_not_drawn() -> None:
    """A stale vehicle on a trip with no pattern is dropped."""
    unknown = vehicle(timestamp=heard(age=300), trip_id='extra')
    missing = vehicle(timestamp=heard(age=300), trip_id='gone')
    base = inputs(vehicles=(unknown, missing))
    view = build_map_view(
        inputs=replace(base, patterns={'gone': None}),
    )
    assert view.markers == ()


def test_stale_vehicle_not_running_is_not_drawn() -> None:
    """A stale vehicle whose trip isn't running now is dropped."""
    late = START + timedelta(hours=1)
    bus = vehicle(timestamp=int(late.timestamp()) - 300)
    view = build_map_view(
        inputs=inputs(vehicles=(bus,), fetched_at=late, now=late),
    )
    assert view.markers == ()


def test_pin_trip_without_live_vehicle_is_hollow_while_running() -> None:
    """A pin trip is hollow while running and absent before it leaves."""
    (marker,) = build_map_view(inputs=inputs(departures=(pin(),))).markers
    assert (marker.live, marker.pinned) == (False, True)
    early = START - timedelta(minutes=10)
    before = build_map_view(
        inputs=inputs(departures=(pin(),), fetched_at=early, now=early),
    )
    assert before.markers == ()


def test_alias_vehicle_counts_as_live() -> None:
    """A fresh vehicle under an alias stops the hollow pin marker."""
    aliased = replace(pin(), aliases=('t1-alias',))
    bus = vehicle(timestamp=heard(age=0), trip_id='t1-alias')
    view = build_map_view(
        inputs=inputs(vehicles=(bus,), departures=(aliased,)),
    )
    (marker,) = view.markers
    assert marker.live
    assert marker.pinned


def test_stale_pin_vehicle_is_drawn_once() -> None:
    """A stale vehicle on a pin trip gives one marker, with its age."""
    bus = vehicle(timestamp=heard(age=300))
    view = build_map_view(
        inputs=inputs(vehicles=(bus,), departures=(pin(),)),
    )
    (marker,) = view.markers
    assert marker.age == 300.0


def test_pinned_route_markers_come_first() -> None:
    """Pinned markers lead, then the rest by distance from the centre."""
    far = vehicle(timestamp=heard(age=0), label='606', point=(900.0, 0.0))
    near = vehicle(
        timestamp=heard(age=0),
        trip_id='t700',
        label='700',
        point=(10.0, 0.0),
    )
    mid = vehicle(
        timestamp=heard(age=0),
        trip_id='t701',
        label='701',
        point=(400.0, 0.0),
    )
    view = build_map_view(
        inputs=inputs(vehicles=(mid, near, far), departures=(pin(),)),
    )
    assert [m.route for m in view.markers] == ['606', '700', '701']
    assert [m.pinned for m in view.markers] == [True, False, False]


def test_vehicles_outside_view_are_dropped() -> None:
    """A vehicle off the map rectangle gets no marker."""
    bus = vehicle(timestamp=heard(age=0), point=(100_000.0, 0.0))
    assert build_map_view(inputs=inputs(vehicles=(bus,))).markers == ()


def test_no_centre_gives_empty_map_text() -> None:
    """A stop without a location says so and draws no markers."""
    bus = vehicle(timestamp=heard(age=0))
    view = build_map_view(
        inputs=replace(inputs(vehicles=(bus,)), centre=None),
    )
    assert view.empty == NO_LOCATION
    assert view.markers == ()
    assert view.base.centre is None


def test_view_copies_stop_view_fields_and_zoom() -> None:
    """Header, banner and empty text come from the Stop view."""
    view = build_map_view(inputs=inputs())
    assert (view.origin, view.destination) == ('Town Hall', 'Parramatta')
    assert (view.clock, view.banner, view.empty) == (
        '8:05',
        'Live times unavailable',
        None,
    )
    assert (view.zoom, view.base.mpp, view.base.centre) == (ZOOM, 8, CENTRE)


def test_sidebar_has_at_most_five_rows() -> None:
    """The hero and rows are cut to five, and a missing hero is skipped."""
    rows = tuple(row(route=str(n)) for n in range(6))
    full = replace(inputs(), stop_view=stop_view(hero=row(), rows=rows))
    sidebar = build_map_view(inputs=full).sidebar
    assert len(sidebar) == SIDEBAR_ROWS
    assert sidebar[0] == row()
    no_hero = replace(inputs(), stop_view=stop_view(hero=None, rows=rows))
    assert build_map_view(inputs=no_hero).sidebar == rows[:SIDEBAR_ROWS]


def test_route_lines_are_distinct_shapes_in_departure_order() -> None:
    """Each shape appears once, in the order departures use it."""
    first = make_shape(shape_id='a', points=[(0.0, 0.0), (1.0, 0.0)])
    second = make_shape(shape_id='b', points=[(0.0, 1.0), (1.0, 1.0)])
    departures = (
        replace(pin(), shape_id='b'),
        replace(pin(), shape_id='a'),
        replace(pin(), shape_id='b'),
        pin(),
    )
    lines = route_lines(
        departures=departures,
        shapes={'a': first, 'b': second},
    )
    assert lines == (second.points, first.points)


def test_missing_shape_draws_no_route_line_and_uses_the_chord() -> None:
    """A trip whose shape has no row has no line and still gets a marker."""
    split = replace(pin(), shape_id='dropped')
    base = inputs(departures=(split,))
    view = build_map_view(
        inputs=replace(
            base,
            patterns={'t1': pattern(shape_id='dropped')},
        ),
    )
    assert view.base.route == ()
    (marker,) = view.markers
    point = timetable_point(
        pattern=pattern(),
        shape=None,
        now=timetable_moment(now=NOW),
    )
    assert point is not None
    assert (marker.x, marker.y) == pytest.approx(at(point=point))


def test_position_age_counts_header_lag_and_time_since_fetch() -> None:
    """Age is header minus timestamp plus the time since the download."""
    feed = snapshot(fetched_at=FETCHED, vehicles=())
    bus = vehicle(timestamp=feed.header_time - 100)
    assert position_age(vehicle=bus, snapshot=feed, now=NOW) == 130.0
    unset = vehicle(timestamp=0)
    assert position_age(vehicle=unset, snapshot=feed, now=NOW) == 30.0


def test_timetable_moment_floors_to_30_s() -> None:
    """Now is floored to a 30 s step, keeping its zone."""
    moment = timetable_moment(now=START + timedelta(seconds=59.5))
    assert moment == START + timedelta(seconds=30)
    assert moment.tzinfo == START.tzinfo
    assert timetable_moment(now=START) == START


def test_needed_patterns_lists_pin_and_nearby_stale_trips() -> None:
    """Pin trips without a fresh vehicle and stale trips nearby."""
    width = MAP_WIDTH * MPP
    stale_near = vehicle(
        timestamp=heard(age=300),
        trip_id='near',
        point=(width, 0.0),
    )
    stale_far = vehicle(
        timestamp=heard(age=300),
        trip_id='far',
        point=(1.6 * width, 0.0),
    )
    fresh = vehicle(timestamp=heard(age=0), trip_id='live')
    covered = replace(pin(), trip_id='live')
    needed = needed_patterns(
        inputs=inputs(
            vehicles=(stale_near, stale_far, fresh),
            departures=(pin(), covered),
        ),
    )
    assert needed == frozenset({'t1', 'near'})


def test_needed_patterns_without_centre_is_empty() -> None:
    """With no centre there is no map, so no patterns are needed."""
    base = inputs(departures=(pin(),))
    assert needed_patterns(inputs=replace(base, centre=None)) == frozenset()


def test_pin_trips_and_routes_include_aliases_and_short_names() -> None:
    """Pin trips are IDs and aliases; pinned routes are short names."""
    departures = (
        replace(pin(), aliases=('t1-alias',)),
        departure(departs=START, trip_id='t2', route='M52'),
    )
    assert pin_trips(departures=departures) == frozenset(
        {'t1', 't1-alias', 't2'},
    )
    assert pinned_routes(departures=departures) == frozenset({'606', 'M52'})


def test_two_vehicles_on_one_trip_give_the_freshest_marker() -> None:
    """Only the most recently heard vehicle on a trip is drawn."""
    stale = vehicle(timestamp=heard(age=300), point=(500.0, 0.0))
    fresh = vehicle(timestamp=heard(age=5), point=(100.0, 0.0))
    for vehicles in ((stale, fresh), (fresh, stale)):
        (marker,) = build_map_view(inputs=inputs(vehicles=vehicles)).markers
        assert marker.live
        assert (marker.x, marker.y) == pytest.approx(at(point=(100.0, 0.0)))


def test_vehicles_without_a_trip_are_each_drawn() -> None:
    """Fresh vehicles with no trip ID are not merged."""
    first = vehicle(timestamp=heard(age=0), trip_id='', point=(0.0, 0.0))
    second = vehicle(timestamp=heard(age=0), trip_id='', point=(80.0, 0.0))
    view = build_map_view(inputs=inputs(vehicles=(first, second)))
    assert len(view.markers) == 2
