"""Tests for strip badges that follow a vehicle onto its next trip."""

from dataclasses import replace
from datetime import timedelta

import pytest

from paperroute.core.map_view import Marker
from paperroute.core.strip_badges import Spot
from paperroute.core.strip_model import (
    INDENT,
    ROW_H,
    TRACK_X,
    StripRow,
    hook_straight,
)
from paperroute.core.strip_vehicles import hop_marker
from paperroute.core.strip_view import build_strip_view
from paperroute_testkit.core.factories import (
    EIGHT,
    LINE,
    START,
    calls,
    carry_on_inputs,
    live,
    pattern,
    vehicle,
)
from paperroute_testkit.core.strip_factories import row_named
from paperroute_transit.core.positions import VehiclePosition


def badge_at(
    *,
    minutes: int,
    off: bool = False,
    next_loaded: bool = True,
    vehicles: tuple[VehiclePosition, ...] = (),
) -> tuple[Marker | None, dict[str, StripRow]]:
    """
    Build the carry-on strip some minutes after 8:00.

    Parameters
    ----------
    minutes : int
        Minutes after 8:00.
    off : bool
        Make D an exit row off C.
    next_loaded : bool
        Load trip b's pattern.
    vehicles : tuple[VehiclePosition, ...]
        Vehicles in the positions feed.

    Returns
    -------
    tuple[Marker | None, dict[str, StripRow]]
        The only badge, or None, and the rows by name.
    """
    view = build_strip_view(inputs=carry_on_inputs(
        now=START + timedelta(minutes=minutes),
        off=off,
        next_loaded=next_loaded,
        vehicles=vehicles,
    ))
    rows = {row.name: row for row in view.rows}
    return (view.markers[0] if view.markers else None), rows


def test_guessed_badge_keeps_its_route_before_the_change() -> None:
    """At 8:05 the hollow T8 badge is on the B to C hop."""
    marker, rows = badge_at(minutes=5)
    assert marker is not None
    assert (marker.route, marker.live) == ('T8', False)
    assert rows['B'].y <= marker.y < rows['C'].y


def test_guessed_badge_waits_at_the_change_as_the_next_route() -> None:
    """At 8:11, between trips, the badge reads T2 at C."""
    marker, rows = badge_at(minutes=11)
    assert marker is not None
    assert (marker.route, marker.y) == ('T2', rows['C'].y)


def test_guessed_badge_takes_the_next_route_after_the_change() -> None:
    """At 8:13 the T2 badge is between C and D."""
    marker, rows = badge_at(minutes=13)
    assert marker is not None
    assert marker.route == 'T2'
    assert rows['C'].y < marker.y < rows['D'].y


def test_live_badge_follows_the_next_trip() -> None:
    """A fresh vehicle heard on trip b near D gives a solid T2 badge."""
    now = START + timedelta(minutes=13)
    heard = vehicle(
        timestamp=int(now.timestamp()), trip_id='b', point=(0.0, -2800.0),
    )
    marker, rows = badge_at(minutes=13, vehicles=(heard,))
    assert marker is not None
    assert (marker.route, marker.live) == ('T2', True)
    assert marker.y == pytest.approx(
        rows['C'].y + 0.8 * (rows['D'].y - rows['C'].y),
    )


def test_next_trip_without_pattern_ends_at_the_change() -> None:
    """With no pattern for trip b there is no badge at 8:13."""
    marker, _ = badge_at(minutes=13, next_loaded=False)
    assert marker is None


def test_bound_vehicle_rides_the_exit_hook() -> None:
    """With D an exit off C, the T2 badge at 8:13 is on D's hook."""
    marker, rows = badge_at(minutes=13, off=True)
    junction = rows['C']
    stub = row_named(rows=list(rows.values()), name='D · T2')
    assert marker is not None
    assert marker.route == 'T2'
    left, right = hook_straight(
        track_x=TRACK_X + INDENT * junction.depth,
    )
    assert marker.x == (left + right) / 2
    assert marker.y == stub.y


def test_next_trip_on_the_strip_keeps_one_badge() -> None:
    """When trip b also leaves the From, only its own badge is drawn."""
    now = START + timedelta(minutes=13)
    inputs = carry_on_inputs(now=now)
    (item,) = inputs.departures
    own = replace(
        item.departure,
        trip_id='b', route='T2', departs=START + timedelta(minutes=20),
        facts=replace(item.departure.facts, then=None),
    )
    both = replace(inputs, departures=(item, live(item=own)))
    assert [m.route for m in build_strip_view(inputs=both).markers] == [
        'T2',
    ]


def test_next_trip_cancelled_keeps_the_carry_on() -> None:
    """A cancelled trip b has no badge of its own; a's follows into it."""
    now = START + timedelta(minutes=13)
    inputs = carry_on_inputs(now=now)
    (item,) = inputs.departures
    own = replace(
        item.departure,
        trip_id='b', route='T2', departs=START + timedelta(minutes=20),
        facts=replace(item.departure.facts, then=None),
    )
    both = replace(
        inputs, departures=(item, live(item=own, cancelled=True)),
    )
    assert [m.route for m in build_strip_view(inputs=both).markers] == [
        'T2',
    ]


def test_one_stop_next_trip_gives_no_badge() -> None:
    """A next trip of one stop gives no badge at the change, no crash."""
    for minutes in (11, 13):
        inputs = carry_on_inputs(now=START + timedelta(minutes=minutes))
        lone = pattern(
            trip_id='b', route='T2',
            stops=calls(stations='C', start=EIGHT + 720, points=LINE),
        )
        alone = replace(inputs, patterns={**inputs.patterns, 'b': lone})
        assert build_strip_view(inputs=alone).markers == ()


def test_hop_marker_needs_two_ends() -> None:
    """A hop with one end gives no badge."""
    assert hop_marker(
        route='T2', ends=(Spot(x=30, y=100),), fraction=0.0, age=None,
        live=False,
    ) is None


def test_fresh_next_leg_beats_a_stale_own_leg() -> None:
    """A stale vehicle on a and a fresh one on b: the solid T2 wins."""
    now = START + timedelta(minutes=13)
    stale = vehicle(
        timestamp=int(now.timestamp()) - 300, trip_id='a',
        point=(0.0, -1000.0),
    )
    fresh = vehicle(
        timestamp=int(now.timestamp()), trip_id='b', point=(0.0, -2800.0),
    )
    marker, _ = badge_at(minutes=13, vehicles=(stale, fresh))
    assert marker is not None
    assert (marker.route, marker.live, marker.age) == ('T2', True, None)


def test_fresh_own_leg_after_its_last_arrival() -> None:
    """At 8:11 a fresh vehicle still on a sits at C as a solid T8."""
    now = START + timedelta(minutes=11)
    heard = vehicle(
        timestamp=int(now.timestamp()), trip_id='a', point=(0.0, -2000.0),
    )
    marker, rows = badge_at(minutes=11, vehicles=(heard,))
    assert marker is not None
    assert (marker.route, marker.live) == ('T8', True)
    assert marker.y == pytest.approx(rows['C'].y)


def hook_ends() -> tuple[Spot, Spot]:
    """
    Give a junction's spot and its exit's spot three rows below.

    Returns
    -------
    tuple[Spot, Spot]
        The junction on the depth 0 track, then the exit at the middle
        of its hook's straight.
    """
    left, right = hook_straight(track_x=TRACK_X)
    return (
        Spot(x=TRACK_X, y=100),
        Spot(x=(left + right) / 2, y=100 + 3 * ROW_H, exit=True),
    )


def test_spur_badge_sits_on_hook_straight() -> None:
    """On its last row before the exit a badge sits on the exit's spot."""
    junction, off = hook_ends()
    found = hop_marker(
        route='T2', ends=(junction, off), fraction=0.9, age=None, live=True,
    )
    left, right = hook_straight(track_x=TRACK_X)
    assert found is not None
    assert found.spur
    assert (found.marker.x, found.marker.y) == (off.x, off.y)
    assert left < found.marker.x < right
    assert found.marker.bearing == 90.0


def test_bound_badge_rides_junction_track_until_last_row() -> None:
    """Further up the hop the badge rides the junction's track."""
    junction, off = hook_ends()
    found = hop_marker(
        route='T2', ends=(junction, off), fraction=0.1, age=None,
        live=False,
    )
    assert found is not None
    assert not found.spur
    assert found.marker.x == junction.x


def test_leaving_train_disappears_past_the_exit() -> None:
    """A hop on from the exit to a stop with no row gives no badge."""
    _, off = hook_ends()
    assert hop_marker(
        route='T2', ends=(off, None), fraction=0.5, age=None, live=True,
    ) is None
